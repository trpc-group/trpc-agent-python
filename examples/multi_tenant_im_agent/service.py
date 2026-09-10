"""Multi-tenant orchestration pipeline shared by every stateless worker."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone

from .adapters import (
    ChannelAdapter,
    ChannelSender,
    InvalidCallbackError,
    default_adapters,
)
from .config import TenantNotFoundError, TenantRegistry
from .domain import (
    ChannelResponse,
    derive_session_id,
    derive_session_user_id,
    derive_user_id,
    stable_subject_id,
)
from .governance import TenantPolicy
from .repository import ControlPlaneRepository
from .runtime import AgentRuntime
from .telemetry import GatewayMetrics, request_span


class MultiTenantAgentService:
    """Routes, governs, serializes, executes, persists, and delivers IM turns."""

    def __init__(
        self,
        *,
        registry: TenantRegistry,
        repository: ControlPlaneRepository,
        runtime: AgentRuntime,
        sender: ChannelSender,
        namespace_secret: str,
        adapters: Mapping[str, ChannelAdapter] | None = None,
        metrics: GatewayMetrics | None = None,
        worker_id: str | None = None,
    ):
        if len(namespace_secret) < 16:
            raise ValueError("namespace_secret must contain at least 16 characters")
        self.registry = registry
        self.repository = repository
        self.runtime = runtime
        self.sender = sender
        self.namespace_secret = namespace_secret
        self.adapters = dict(adapters or default_adapters())
        self.metrics = metrics or GatewayMetrics()
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
        self.policy = TenantPolicy()

    async def handle_webhook(
        self,
        *,
        channel: str,
        account_id: str,
        headers: Mapping[str, str],
        query: Mapping[str, str],
        raw_body: bytes,
    ) -> ChannelResponse:
        try:
            tenant, binding = self.registry.resolve(channel, account_id)
            adapter = self.adapters[channel.lower()]
        except (TenantNotFoundError, KeyError):
            return ChannelResponse(
                status_code=404, body={"ok": False, "error": "unknown_channel_account"}
            )

        try:
            adapter.verify(
                binding=binding, headers=headers, query=query, raw_body=raw_body
            )
            message = adapter.parse(tenant=tenant, binding=binding, raw_body=raw_body)
        except InvalidCallbackError:
            return ChannelResponse(
                status_code=401, body={"ok": False, "error": "invalid_callback"}
            )
        except ValueError as exc:
            return ChannelResponse(
                status_code=202, body={"ok": True, "ignored": type(exc).__name__}
            )

        session_id = derive_session_id(message, self.namespace_secret)
        actor_user_id = derive_user_id(message, self.namespace_secret)
        session_user_id = derive_session_user_id(message, self.namespace_secret)
        now = datetime.now(timezone.utc)
        month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        monthly_tokens = await asyncio.to_thread(
            self.repository.tenant_token_usage, tenant.tenant_id, month_start
        )
        decision = self.policy.evaluate(
            tenant, message, monthly_tokens_used=monthly_tokens
        )
        if not decision.allowed:
            await asyncio.to_thread(
                self._audit,
                tenant_id=tenant.tenant_id,
                channel=message.channel,
                user_id=actor_user_id,
                session_id=session_id,
                agent_name=tenant.agent_name,
                decision="denied",
                error_type=decision.reason,
                token_count=decision.estimated_tokens,
            )
            self.metrics.observe_request(tenant.tenant_id, message.channel, "denied", 0)
            return ChannelResponse(
                status_code=403, body={"ok": False, "error": decision.reason}
            )

        conversation_hash = stable_subject_id(
            self.namespace_secret,
            tenant.tenant_id,
            message.channel,
            message.conversation_id,
            prefix="con",
        )
        await asyncio.to_thread(
            self.repository.ensure_session,
            session_id=session_id,
            tenant=tenant,
            channel=message.channel,
            conversation_hash=conversation_hash,
            user_hash=session_user_id,
        )
        lease_owner = f"{self.worker_id}:{message.external_message_id[:64]}"
        acquired = await asyncio.to_thread(
            self.repository.acquire_session_lease,
            session_id,
            lease_owner,
            tenant.session_lease_seconds,
        )
        if not acquired:
            await asyncio.to_thread(
                self._audit,
                tenant_id=tenant.tenant_id,
                channel=message.channel,
                user_id=actor_user_id,
                session_id=session_id,
                agent_name=tenant.agent_name,
                decision="busy",
                error_type="session_lease_conflict",
            )
            self.metrics.observe_request(tenant.tenant_id, message.channel, "busy", 0)
            return ChannelResponse(
                status_code=429,
                body={"ok": False, "error": "session_busy", "retryable": True},
                headers={"Retry-After": "2"},
            )

        event_id = 0
        trace_result = None
        try:
            claim = await asyncio.to_thread(
                self.repository.claim_message, message, session_id
            )
            event_id = claim.event_id
            if claim.payload_conflict:
                await asyncio.to_thread(
                    self._audit,
                    tenant_id=tenant.tenant_id,
                    channel=message.channel,
                    user_id=actor_user_id,
                    session_id=session_id,
                    agent_name=tenant.agent_name,
                    decision="denied",
                    error_type="idempotency_conflict",
                )
                self.metrics.observe_request(
                    tenant.tenant_id, message.channel, "conflict", 0
                )
                return ChannelResponse(
                    status_code=409, body={"ok": False, "error": "idempotency_conflict"}
                )
            if not claim.accepted:
                # The transactional outbox owns redelivery. Do not send the
                # same completed response twice when the IM provider retries.
                await asyncio.to_thread(
                    self._audit,
                    tenant_id=tenant.tenant_id,
                    channel=message.channel,
                    user_id=actor_user_id,
                    session_id=session_id,
                    agent_name=tenant.agent_name,
                    decision="duplicate",
                )
                self.metrics.observe_request(
                    tenant.tenant_id, message.channel, "duplicate", 0
                )
                return ChannelResponse(
                    status_code=200,
                    body={"ok": True, "duplicate": True, "status": claim.status},
                )

            with request_span(
                tenant.tenant_id, message.channel, session_id
            ) as trace_result:
                reply = await self.runtime.reply(
                    tenant=tenant,
                    message=message,
                    user_id=session_user_id,
                    session_id=session_id,
                )
                delivery = adapter.delivery(
                    binding=binding, message=message, reply=reply
                )
                outbox_id = await asyncio.to_thread(
                    self.repository.complete_message,
                    event_id=event_id,
                    tenant_id=tenant.tenant_id,
                    session_id=session_id,
                    channel=message.channel,
                    reply=reply,
                    delivery=delivery,
                )
                delivery_queued = False
                try:
                    claimed = await asyncio.to_thread(
                        self.repository.claim_outbox, outbox_id
                    )
                    if not claimed:
                        raise RuntimeError("outbox claim failed")
                    await self.sender.send(delivery)
                    await asyncio.to_thread(self.repository.mark_outbox_sent, outbox_id)
                    self.metrics.observe_delivery(message.channel, "sent")
                except Exception:  # noqa: BLE001 - provider failures are persisted for retry
                    delivery_queued = True
                    await asyncio.to_thread(
                        self.repository.mark_outbox_retry, outbox_id
                    )
                    self.metrics.observe_delivery(message.channel, "retry")

            await asyncio.to_thread(
                self._audit,
                tenant_id=tenant.tenant_id,
                channel=message.channel,
                user_id=actor_user_id,
                session_id=session_id,
                agent_name=tenant.agent_name,
                tool_name=",".join(reply.tool_names),
                decision="delivery_queued" if delivery_queued else "completed",
                latency_ms=trace_result.latency_ms,
                cost=reply.cost,
                token_count=reply.token_count or decision.estimated_tokens,
                trace_id=trace_result.trace_id,
            )
            self.metrics.observe_request(
                tenant.tenant_id,
                message.channel,
                "completed",
                trace_result.latency_ms,
                reply.token_count or decision.estimated_tokens,
            )
            return ChannelResponse(
                status_code=202 if delivery_queued else 200,
                body={"ok": True, "queued": delivery_queued, "session_id": session_id},
            )
        except asyncio.TimeoutError:
            return await self._handle_failure(
                event_id,
                tenant.tenant_id,
                message.channel,
                actor_user_id,
                session_id,
                tenant.agent_name,
                "model_timeout",
            )
        except Exception as exc:  # noqa: BLE001 - request boundary converts failures to audited 503
            return await self._handle_failure(
                event_id,
                tenant.tenant_id,
                message.channel,
                actor_user_id,
                session_id,
                tenant.agent_name,
                type(exc).__name__,
            )
        finally:
            await asyncio.to_thread(
                self.repository.release_session_lease, session_id, lease_owner
            )

    async def _handle_failure(
        self,
        event_id: int,
        tenant_id: str,
        channel: str,
        user_id: str,
        session_id: str,
        agent_name: str,
        error_type: str,
    ) -> ChannelResponse:
        if event_id:
            await asyncio.to_thread(
                self.repository.mark_message_failed, event_id, error_type
            )
        await asyncio.to_thread(
            self._audit,
            tenant_id=tenant_id,
            channel=channel,
            user_id=user_id,
            session_id=session_id,
            agent_name=agent_name,
            decision="failed",
            error_type=error_type,
        )
        self.metrics.observe_request(tenant_id, channel, "failed", 0)
        return ChannelResponse(
            status_code=503,
            body={"ok": False, "error": "temporarily_unavailable", "retryable": True},
        )

    async def dispatch_outbox_once(self, limit: int = 50) -> int:
        items = await asyncio.to_thread(self.repository.claim_due_outbox, limit)
        for item in items:
            try:
                await self.sender.send(item.request)
                await asyncio.to_thread(
                    self.repository.mark_outbox_sent, item.outbox_id
                )
                self.metrics.observe_delivery(item.request.channel, "sent")
            except Exception:  # noqa: BLE001 - each outbox item must fail independently
                await asyncio.to_thread(
                    self.repository.mark_outbox_retry, item.outbox_id
                )
                self.metrics.observe_delivery(item.request.channel, "retry")
        return len(items)

    def _audit(self, **values) -> None:
        defaults = {
            "tool_name": "",
            "latency_ms": 0,
            "error_type": "",
            "cost": 0.0,
            "token_count": 0,
            "trace_id": "",
        }
        defaults.update(values)
        self.repository.add_audit(defaults)
