"""SQL control-plane schema, idempotency, leases, audit, and outbox storage."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import ConfigurationError
from .domain import AgentReply, DeliveryRequest, InboundMessage, TenantConfig

OUTBOX_MAX_ATTEMPTS = 8
OUTBOX_RETRY_BASE_SECONDS = 5
OUTBOX_RETRY_CAP_SECONDS = 300
OUTBOX_INLINE_CLAIM_SECONDS = 30
PRECISE_DATETIME = DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class TenantRecord(Base):
    __tablename__ = "mt_tenants"
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="active")
    config_version: Mapped[int] = mapped_column(Integer, default=1)
    token_budget_period: Mapped[str] = mapped_column(String(7), default="")
    token_usage: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME, default=utcnow)


class AgentAppRecord(Base):
    __tablename__ = "mt_agent_apps"
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("mt_tenants.tenant_id"), primary_key=True, index=True
    )
    agent_app_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(128))
    model_name: Mapped[str] = mapped_column(String(128))
    tool_allowlist_json: Mapped[str] = mapped_column(Text, default="[]")
    updated_at: Mapped[datetime] = mapped_column(
        PRECISE_DATETIME, default=utcnow, onupdate=utcnow
    )


class ChannelBindingRecord(Base):
    __tablename__ = "mt_channel_bindings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("mt_tenants.tenant_id"), index=True
    )
    channel: Mapped[str] = mapped_column(String(32))
    account_id: Mapped[str] = mapped_column(String(128))
    secret_ref: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (
        UniqueConstraint("channel", "account_id", name="uq_mt_channel_account"),
    )


class SessionRecord(Base):
    __tablename__ = "mt_sessions"
    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    agent_app_id: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(32))
    conversation_hash: Mapped[str] = mapped_column(String(64))
    user_hash: Mapped[str] = mapped_column(String(64))
    last_event_seq: Mapped[int] = mapped_column(Integer, default=0)
    state_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        PRECISE_DATETIME, default=utcnow, onupdate=utcnow
    )
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "agent_app_id"],
            ["mt_agent_apps.tenant_id", "mt_agent_apps.agent_app_id"],
            name="fk_mt_session_agent_app",
        ),
    )


class MessageEventRecord(Base):
    __tablename__ = "mt_message_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("mt_tenants.tenant_id"), index=True
    )
    channel: Mapped[str] = mapped_column(String(32))
    account_id: Mapped[str] = mapped_column(String(128))
    external_message_id: Mapped[str] = mapped_column(String(192))
    session_id: Mapped[str] = mapped_column(
        ForeignKey("mt_sessions.session_id"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    direction: Mapped[str] = mapped_column(String(16), default="inbound")
    status: Mapped[str] = mapped_column(String(24), default="processing")
    payload_hash: Mapped[str] = mapped_column(String(64))
    content_redacted: Mapped[str] = mapped_column(Text, default="")
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        PRECISE_DATETIME, default=utcnow, onupdate=utcnow
    )
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "channel",
            "account_id",
            "external_message_id",
            name="uq_mt_inbound_idempotency",
        ),
        UniqueConstraint(
            "session_id",
            "sequence",
            "direction",
            name="uq_mt_session_sequence_direction",
        ),
    )


class MemoryRecord(Base):
    __tablename__ = "mt_memories"
    memory_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("mt_tenants.tenant_id"), index=True
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("mt_sessions.session_id"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    content_ref: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME, default=utcnow)


class SummaryRecord(Base):
    __tablename__ = "mt_summaries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("mt_tenants.tenant_id"), index=True
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("mt_sessions.session_id"), index=True
    )
    through_sequence: Mapped[int] = mapped_column(Integer)
    summary_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME, default=utcnow)
    __table_args__ = (
        UniqueConstraint(
            "session_id", "through_sequence", name="uq_mt_summary_version"
        ),
    )


class ArtifactRecord(Base):
    __tablename__ = "mt_artifacts"
    artifact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("mt_tenants.tenant_id"), index=True
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("mt_sessions.session_id"), index=True
    )
    object_uri: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME, default=utcnow)


class KnowledgeRecord(Base):
    __tablename__ = "mt_knowledge"
    knowledge_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("mt_tenants.tenant_id"), index=True
    )
    vector_namespace: Mapped[str] = mapped_column(String(192))
    source_uri: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME, default=utcnow)


class AuditLogRecord(Base):
    __tablename__ = "mt_audit_logs"
    audit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(32))
    user_id: Mapped[str] = mapped_column(String(64))
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    agent_name: Mapped[str] = mapped_column(String(128))
    tool_name: Mapped[str] = mapped_column(String(128), default="")
    decision: Mapped[str] = mapped_column(String(32))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_type: Mapped[str] = mapped_column(String(128), default="")
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    trace_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(
        PRECISE_DATETIME, default=utcnow, index=True
    )


class SessionLeaseRecord(Base):
    __tablename__ = "mt_session_leases"
    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME)


class OutboxRecord(Base):
    __tablename__ = "mt_outbox"
    outbox_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(32))
    payload_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME, default=utcnow)


@dataclass(frozen=True)
class ClaimResult:
    accepted: bool
    status: str
    event_id: int
    sequence: int
    cached_response: str | None = None
    payload_conflict: bool = False


@dataclass(frozen=True)
class OutboxItem:
    outbox_id: str
    request: DeliveryRequest


class ControlPlaneRepository:
    """Transactional repository shared by all stateless workers."""

    def __init__(self, db_url: str):
        connect_args = (
            {"check_same_thread": False} if db_url.startswith("sqlite") else {}
        )
        engine_kwargs: dict[str, Any] = {
            "pool_pre_ping": True,
            "connect_args": connect_args,
        }
        if db_url in {"sqlite://", "sqlite:///:memory:"}:
            engine_kwargs["poolclass"] = StaticPool
        self.engine = create_engine(db_url, **engine_kwargs)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        # SQLite drops SELECT FOR UPDATE.  A repository-local lock preserves
        # thread safety for the documented single-process evaluation mode;
        # production replicas use MySQL/PostgreSQL row locks.
        self._write_lock = RLock()

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    def close(self) -> None:
        """Release pooled database connections during graceful shutdown."""

        self.engine.dispose()

    def sync_tenants(self, tenants: Iterable[TenantConfig]) -> None:
        """Idempotently seed public configuration; secret values are never stored."""

        configured_routes: set[tuple[str, str]] = set()
        with self._write_lock, self.Session.begin() as db:
            for tenant in tenants:
                record = db.get(TenantRecord, tenant.tenant_id)
                if record is None:
                    record = TenantRecord(
                        tenant_id=tenant.tenant_id,
                        display_name=tenant.display_name,
                        token_budget_period=utcnow().strftime("%Y-%m"),
                        token_usage=0,
                    )
                    db.add(record)
                else:
                    record.display_name = tenant.display_name
                app = db.get(AgentAppRecord, (tenant.tenant_id, tenant.agent_app_id))
                if app is None:
                    db.add(
                        AgentAppRecord(
                            agent_app_id=tenant.agent_app_id,
                            tenant_id=tenant.tenant_id,
                            agent_name=tenant.agent_name,
                            model_name=tenant.model_name,
                            tool_allowlist_json=json.dumps(tenant.tool_allowlist),
                        )
                    )
                else:
                    app.agent_name = tenant.agent_name
                    app.model_name = tenant.model_name
                    app.tool_allowlist_json = json.dumps(tenant.tool_allowlist)
                for binding in tenant.bindings:
                    channel = binding.channel.lower()
                    route = (channel, binding.account_id)
                    configured_routes.add(route)
                    existing = db.scalar(
                        select(ChannelBindingRecord).where(
                            ChannelBindingRecord.channel == channel,
                            ChannelBindingRecord.account_id == binding.account_id,
                        )
                    )
                    if existing is None:
                        db.add(
                            ChannelBindingRecord(
                                tenant_id=tenant.tenant_id,
                                channel=channel,
                                account_id=binding.account_id,
                                secret_ref=binding.webhook_secret_env,
                                enabled=int(binding.enabled),
                            )
                        )
                    else:
                        if existing.tenant_id != tenant.tenant_id:
                            raise ConfigurationError(
                                "channel account cannot move between tenants implicitly: "
                                f"{route}"
                            )
                        existing.secret_ref = binding.webhook_secret_env
                        existing.enabled = int(binding.enabled)
            for existing in db.scalars(select(ChannelBindingRecord)):
                route = (existing.channel.lower(), existing.account_id)
                if route not in configured_routes:
                    existing.enabled = 0

    def ensure_session(
        self,
        *,
        session_id: str,
        tenant: TenantConfig,
        channel: str,
        conversation_hash: str,
        user_hash: str,
    ) -> None:
        try:
            with self._write_lock, self.Session.begin() as db:
                if db.get(SessionRecord, session_id) is None:
                    db.add(
                        SessionRecord(
                            session_id=session_id,
                            tenant_id=tenant.tenant_id,
                            agent_app_id=tenant.agent_app_id,
                            channel=channel,
                            conversation_hash=conversation_hash,
                            user_hash=user_hash,
                        )
                    )
        except IntegrityError:
            # Another worker created the same deterministic session first.
            pass

    def acquire_session_lease(
        self, session_id: str, owner_id: str, ttl_seconds: int
    ) -> bool:
        now = utcnow()
        expires = now + timedelta(seconds=ttl_seconds)
        try:
            with self._write_lock, self.Session.begin() as db:
                lease = db.scalar(
                    select(SessionLeaseRecord)
                    .where(SessionLeaseRecord.session_id == session_id)
                    .with_for_update()
                )
                if lease is None:
                    db.add(
                        SessionLeaseRecord(
                            session_id=session_id, owner_id=owner_id, expires_at=expires
                        )
                    )
                    return True
                lease_expiry = lease.expires_at
                if lease_expiry.tzinfo is None:
                    lease_expiry = lease_expiry.replace(tzinfo=timezone.utc)
                if lease.owner_id != owner_id and lease_expiry > now:
                    return False
                lease.owner_id = owner_id
                lease.expires_at = expires
                return True
        except IntegrityError:
            return False

    def release_session_lease(self, session_id: str, owner_id: str) -> None:
        with self._write_lock, self.Session.begin() as db:
            lease = db.get(SessionLeaseRecord, session_id)
            if lease is not None and lease.owner_id == owner_id:
                db.delete(lease)

    def reserve_token_budget(
        self,
        tenant_id: str,
        period: str,
        estimated_tokens: int,
        limit: int,
    ) -> bool:
        """Atomically reserve tenant budget before invoking a model."""

        with self._write_lock, self.Session.begin() as db:
            tenant = db.scalar(
                select(TenantRecord)
                .where(TenantRecord.tenant_id == tenant_id)
                .with_for_update()
            )
            if tenant is None:
                return False
            if tenant.token_budget_period != period:
                tenant.token_budget_period = period
                tenant.token_usage = 0
            if tenant.token_usage + estimated_tokens > limit:
                return False
            tenant.token_usage += estimated_tokens
            return True

    def settle_token_budget(
        self, tenant_id: str, period: str, reserved: int, actual: int
    ) -> None:
        """Replace a reservation with actual usage, or release it on failure."""

        with self._write_lock, self.Session.begin() as db:
            tenant = db.scalar(
                select(TenantRecord)
                .where(TenantRecord.tenant_id == tenant_id)
                .with_for_update()
            )
            if tenant is None or tenant.token_budget_period != period:
                return
            tenant.token_usage = max(0, tenant.token_usage - reserved + max(0, actual))

    def claim_message(
        self, message: InboundMessage, session_id: str, lease_owner: str
    ) -> ClaimResult:
        """Atomically allocate session order and reject provider redelivery."""

        try:
            with self._write_lock, self.Session.begin() as db:
                existing = db.scalar(
                    select(MessageEventRecord).where(
                        MessageEventRecord.tenant_id == message.tenant_id,
                        MessageEventRecord.channel == message.channel,
                        MessageEventRecord.account_id == message.account_id,
                        MessageEventRecord.external_message_id
                        == message.external_message_id,
                    )
                )
                if existing is not None:
                    lease = db.get(SessionLeaseRecord, session_id)
                    recoverable = existing.status == "failed" or (
                        existing.status == "processing"
                        and lease is not None
                        and lease.owner_id == lease_owner
                    )
                    if (
                        recoverable
                        and existing.response_text is None
                        and existing.payload_hash == message.payload_hash()
                    ):
                        existing.status = "processing"
                        existing.error_type = None
                        existing.updated_at = utcnow()
                        return ClaimResult(
                            True, existing.status, existing.id, existing.sequence
                        )
                    return self._duplicate_result(existing, message.payload_hash())
                session = db.scalar(
                    select(SessionRecord)
                    .where(SessionRecord.session_id == session_id)
                    .with_for_update()
                )
                if session is None:
                    raise RuntimeError(
                        "session must be created before claiming a message"
                    )
                session.last_event_seq += 1
                event = MessageEventRecord(
                    tenant_id=message.tenant_id,
                    channel=message.channel,
                    account_id=message.account_id,
                    external_message_id=message.external_message_id,
                    session_id=session_id,
                    sequence=session.last_event_seq,
                    direction="inbound",
                    status="processing",
                    payload_hash=message.payload_hash(),
                    content_redacted=f"[text:{len(message.text)} chars]",
                )
                db.add(event)
                db.flush()
                return ClaimResult(True, event.status, event.id, event.sequence)
        except IntegrityError:
            with self.Session() as db:
                existing = db.scalar(
                    select(MessageEventRecord).where(
                        MessageEventRecord.tenant_id == message.tenant_id,
                        MessageEventRecord.channel == message.channel,
                        MessageEventRecord.account_id == message.account_id,
                        MessageEventRecord.external_message_id
                        == message.external_message_id,
                    )
                )
                if existing is None:
                    raise
                return self._duplicate_result(existing, message.payload_hash())

    @staticmethod
    def _duplicate_result(
        existing: MessageEventRecord, payload_hash: str
    ) -> ClaimResult:
        return ClaimResult(
            accepted=False,
            status=existing.status,
            event_id=existing.id,
            sequence=existing.sequence,
            cached_response=existing.response_text,
            payload_conflict=existing.payload_hash != payload_hash,
        )

    def complete_message(
        self,
        *,
        event_id: int,
        tenant_id: str,
        session_id: str,
        channel: str,
        reply: AgentReply,
        delivery: DeliveryRequest,
        budget_period: str,
        reserved_tokens: int,
        actual_tokens: int,
    ) -> str:
        """Commit result and outbox atomically before attempting IM delivery."""

        outbox_id = uuid.uuid4().hex
        with self._write_lock, self.Session.begin() as db:
            event = db.get(MessageEventRecord, event_id)
            if event is None:
                raise RuntimeError("message event disappeared")
            if (
                event.status != "processing"
                or event.tenant_id != tenant_id
                or event.session_id != session_id
            ):
                raise RuntimeError("message event is not claimable for completion")
            event.status = "completed"
            event.response_text = reply.text
            tenant = db.scalar(
                select(TenantRecord)
                .where(TenantRecord.tenant_id == tenant_id)
                .with_for_update()
            )
            if tenant is None or tenant.token_budget_period != budget_period:
                raise RuntimeError("tenant budget period changed during request")
            tenant.token_usage = max(
                0, tenant.token_usage - reserved_tokens + max(0, actual_tokens)
            )
            db.add(
                OutboxRecord(
                    outbox_id=outbox_id,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    channel=channel,
                    payload_json=json.dumps(
                        {
                            "channel": delivery.channel,
                            "account_id": delivery.account_id,
                            "conversation_id": delivery.conversation_id,
                            "text": delivery.text,
                            "credentials_env": dict(delivery.credentials_env),
                            "metadata": dict(delivery.metadata),
                        },
                        ensure_ascii=False,
                    ),
                    next_attempt_at=utcnow()
                    + timedelta(seconds=OUTBOX_INLINE_CLAIM_SECONDS),
                )
            )
        return outbox_id

    def claim_outbox(self, outbox_id: str) -> bool:
        now = utcnow()
        with self._write_lock, self.Session.begin() as db:
            item = db.scalar(
                select(OutboxRecord)
                .where(OutboxRecord.outbox_id == outbox_id)
                .with_for_update()
            )
            if item is None or item.status not in {"pending", "retry"}:
                return False
            item.status = "sending"
            item.attempts += 1
            item.next_attempt_at = now + timedelta(seconds=60)
            return True

    def claim_due_outbox(self, limit: int = 50) -> list[OutboxItem]:
        """Lease retryable rows; expired ``sending`` rows recover crashed workers."""

        now = utcnow()
        with self._write_lock, self.Session.begin() as db:
            rows = list(
                db.scalars(
                    select(OutboxRecord)
                    .where(
                        OutboxRecord.status.in_(("pending", "retry", "sending")),
                        OutboxRecord.next_attempt_at <= now,
                    )
                    .order_by(OutboxRecord.next_attempt_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            result: list[OutboxItem] = []
            for item in rows:
                item.status = "sending"
                item.attempts += 1
                item.next_attempt_at = now + timedelta(seconds=60)
                payload = json.loads(item.payload_json)
                result.append(OutboxItem(item.outbox_id, DeliveryRequest(**payload)))
            return result

    def mark_message_failed(self, event_id: int, error_type: str) -> None:
        with self._write_lock, self.Session.begin() as db:
            event = db.get(MessageEventRecord, event_id)
            if event is not None and event.status == "processing":
                event.status = "failed"
                event.error_type = error_type[:128]

    def mark_outbox_sent(self, outbox_id: str) -> None:
        with self._write_lock, self.Session.begin() as db:
            item = db.get(OutboxRecord, outbox_id)
            if item is not None and item.status == "sending":
                item.status = "sent"

    def mark_outbox_retry(
        self, outbox_id: str, delay_seconds: int | None = None
    ) -> str:
        """Schedule bounded exponential retry or move an exhausted item to DLQ."""

        with self._write_lock, self.Session.begin() as db:
            item = db.get(OutboxRecord, outbox_id)
            if item is None:
                return "missing"
            if item.status != "sending":
                return item.status
            if item.attempts >= OUTBOX_MAX_ATTEMPTS:
                item.status = "dead_letter"
                return item.status
            if delay_seconds is None:
                exponential = min(
                    OUTBOX_RETRY_CAP_SECONDS,
                    OUTBOX_RETRY_BASE_SECONDS * (2 ** max(item.attempts - 1, 0)),
                )
                # Stable jitter prevents synchronized retries and stays reproducible.
                jitter = sum(outbox_id.encode("utf-8")) % OUTBOX_RETRY_BASE_SECONDS
                delay_seconds = exponential + jitter
            item.status = "retry"
            item.next_attempt_at = utcnow() + timedelta(seconds=delay_seconds)
            return item.status

    def healthcheck(self) -> bool:
        with self.Session() as db:
            db.execute(select(1))
        return True

    def add_audit(self, values: Mapping[str, Any]) -> str:
        audit_id = uuid.uuid4().hex
        allowed = {
            "tenant_id",
            "channel",
            "user_id",
            "session_id",
            "agent_name",
            "tool_name",
            "decision",
            "latency_ms",
            "error_type",
            "cost",
            "token_count",
            "trace_id",
        }
        clean = {key: value for key, value in values.items() if key in allowed}
        with self._write_lock, self.Session.begin() as db:
            db.add(AuditLogRecord(audit_id=audit_id, **clean))
        return audit_id
