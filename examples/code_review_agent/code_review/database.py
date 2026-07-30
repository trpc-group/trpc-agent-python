"""SQLAlchemy persistence for code review runs and their child records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    select,
    text,
    update,
)
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    selectinload,
)

from .models import (
    AnalyzerExecution,
    AnalyzerStatus,
    ChangedFile,
    ChangeType,
    Finding,
    ReviewOutput,
    ReviewRun,
    ReviewStatus,
    Severity,
)

SCHEMA_VERSION = 4
_UNSET = object()


class ReviewDatabaseBase(DeclarativeBase):
    """Dedicated metadata for code review tables."""


class ReviewSchemaVersion(ReviewDatabaseBase):
    """Single-row schema marker for explicit future migrations."""

    __tablename__ = "code_review_schema_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class ReviewRunRecord(ReviewDatabaseBase):
    """Top-level persisted review execution."""

    __tablename__ = "code_review_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_code_review_runs_idempotency"),
        Index("ix_code_review_runs_repository_started", "repository_path_hash", "started_at"),
        Index("ix_code_review_runs_status_started", "status", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    repository_path: Mapped[str] = mapped_column(Text, nullable=False)
    repository_path_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    base_revision: Mapped[str] = mapped_column(String(255), nullable=False)
    head_revision: Mapped[str] = mapped_column(String(255), nullable=False)
    resolved_head_revision: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    effective_base_revision: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    static_analysis_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    diagnostics: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    changed_files: Mapped[list[ChangedFileRecord]] = relationship(
        back_populates="review_run",
        cascade="all, delete-orphan",
        order_by="ChangedFileRecord.position",
    )
    findings: Mapped[list[FindingRecord]] = relationship(
        back_populates="review_run",
        cascade="all, delete-orphan",
        order_by="FindingRecord.position",
    )
    analyzer_executions: Mapped[list[AnalyzerExecutionRecord]] = relationship(
        back_populates="review_run",
        cascade="all, delete-orphan",
        order_by="AnalyzerExecutionRecord.position",
    )


class ChangedFileRecord(ReviewDatabaseBase):
    """One changed file belonging to a review run."""

    __tablename__ = "code_review_changed_files"
    __table_args__ = (
        UniqueConstraint("review_run_id", "position", name="uq_code_review_changed_file_position"),
        Index("ix_code_review_changed_files_run", "review_run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_run_id: Mapped[str] = mapped_column(
        ForeignKey("code_review_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    old_path: Mapped[str | None] = mapped_column(Text)
    change_type: Mapped[str] = mapped_column(String(32), nullable=False)
    language: Mapped[str] = mapped_column(String(64), nullable=False)
    patch: Mapped[str] = mapped_column(Text, nullable=False, default="")
    hunks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    added_lines: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_lines: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_binary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    review_run: Mapped[ReviewRunRecord] = relationship(back_populates="changed_files")


class FindingRecord(ReviewDatabaseBase):
    """One normalized finding belonging to a review run."""

    __tablename__ = "code_review_findings"
    __table_args__ = (
        UniqueConstraint("review_run_id", "position", name="uq_code_review_finding_position"),
        Index("ix_code_review_findings_location", "review_run_id", "start_line"),
        Index("ix_code_review_findings_publishable", "review_run_id", "publishable"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_run_id: Mapped[str] = mapped_column(
        ForeignKey("code_review_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_id: Mapped[str] = mapped_column(String(160), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    start_line: Mapped[int | None] = mapped_column(Integer)
    end_line: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    suggestion: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    publishable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    review_run: Mapped[ReviewRunRecord] = relationship(back_populates="findings")


class AnalyzerExecutionRecord(ReviewDatabaseBase):
    """One deterministic analyzer invocation belonging to a review run."""

    __tablename__ = "code_review_analyzer_executions"
    __table_args__ = (
        UniqueConstraint("review_run_id", "position", name="uq_code_review_analyzer_position"),
        Index("ix_code_review_analyzers_tool_status", "tool", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_run_id: Mapped[str] = mapped_column(
        ForeignKey("code_review_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    tool: Mapped[str] = mapped_column(String(80), nullable=False)
    runtime: Mapped[str] = mapped_column(String(32), nullable=False)
    command: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    stdout: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stderr: Mapped[str] = mapped_column(Text, nullable=False, default="")
    findings_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    review_run: Mapped[ReviewRunRecord] = relationship(back_populates="analyzer_executions")


class GitHubDeliveryRecord(ReviewDatabaseBase):
    """Lifecycle and publication state for one GitHub webhook delivery."""

    __tablename__ = "code_review_github_deliveries"
    __table_args__ = (
        Index("ix_code_review_github_delivery_repository", "repository_full_name", "received_at"),
        Index("ix_code_review_github_delivery_status", "status", "updated_at"),
    )

    delivery_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_name: Mapped[str] = mapped_column(String(80), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    repository_full_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    pull_number: Mapped[int | None] = mapped_column(Integer)
    head_sha: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    installation_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="received")
    review_run_id: Mapped[str | None] = mapped_column(ForeignKey("code_review_runs.id", ondelete="SET NULL"), )
    check_run_id: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class GitHubReviewJobRecord(ReviewDatabaseBase):
    """Durable queue entry for one accepted GitHub delivery."""

    __tablename__ = "code_review_github_jobs"
    __table_args__ = (
        Index("ix_code_review_github_jobs_available", "status", "available_at"),
        Index("ix_code_review_github_jobs_lease", "status", "lease_expires_at"),
    )

    delivery_id: Mapped[str] = mapped_column(
        ForeignKey("code_review_github_deliveries.delivery_id", ondelete="CASCADE"),
        primary_key=True,
    )
    event_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class GitHubPublicationRecord(ReviewDatabaseBase):
    """Locally durable progress for retry-safe GitHub publication."""

    __tablename__ = "code_review_github_publications"

    delivery_id: Mapped[str] = mapped_column(
        ForeignKey("code_review_github_deliveries.delivery_id", ondelete="CASCADE"),
        primary_key=True,
    )
    check_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    comments_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


@dataclass(frozen=True)
class SaveReviewResult:
    """Result of an idempotent save operation."""

    review_run: ReviewRun
    created: bool


@dataclass(frozen=True)
class ClaimedReviewJob:
    """One queue job currently leased by a worker."""

    delivery_id: str
    event_payload: dict[str, Any]
    attempt_count: int
    max_attempts: int
    lease_owner: str
    lease_expires_at: datetime


def sqlite_database_url(path: str | Path) -> str:
    """Build an absolute SQLite URL from a local database path."""
    database_path = Path(path).expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{database_path}"


class ReviewStore:
    """Synchronous SQLAlchemy repository with idempotent inserts."""

    def __init__(self, database_url: str, *, engine: Engine | None = None):
        url = make_url(database_url)
        if any(async_driver in url.drivername for async_driver in ("aiosqlite", "aiomysql", "asyncpg")):
            raise ValueError("ReviewStore requires a synchronous SQLAlchemy database URL")
        self.database_url = database_url
        self.engine = engine or create_engine(database_url, future=True)
        try:
            self.initialize()
        except Exception:
            if engine is None:
                self.engine.dispose()
            raise

    def initialize(self) -> None:
        """Create tables and advance supported additive schema versions."""
        ReviewDatabaseBase.metadata.create_all(self.engine)
        with Session(self.engine) as session, session.begin():
            marker = session.get(ReviewSchemaVersion, 1)
            if marker is None:
                session.add(ReviewSchemaVersion(id=1, version=SCHEMA_VERSION))
            elif marker.version in {1, 2, 3}:
                # Versions 2 through 4 only add tables; create_all performs the DDL.
                marker.version = SCHEMA_VERSION
                marker.updated_at = datetime.now(timezone.utc)
            elif marker.version != SCHEMA_VERSION:
                raise RuntimeError(
                    f"Unsupported code review schema version {marker.version}; expected {SCHEMA_VERSION}")

    def save_run(self, review_run: ReviewRun) -> SaveReviewResult:
        """Insert a complete run, returning the existing row on an idempotency hit."""
        if not review_run.idempotency_key:
            raise ValueError("review run must have an idempotency key before persistence")
        try:
            with Session(self.engine) as session, session.begin():
                existing = self._select_by_idempotency(session, review_run.idempotency_key)
                if existing is not None:
                    return SaveReviewResult(review_run=_record_to_model(existing), created=False)
                record = _model_to_record(review_run)
                session.add(record)
                session.flush()
                persisted = _record_to_model(record)
            return SaveReviewResult(review_run=persisted, created=True)
        except IntegrityError:
            # Another worker may win the insert between our lookup and flush.
            existing = self.get_by_idempotency_key(review_run.idempotency_key)
            if existing is None:
                raise
            return SaveReviewResult(review_run=existing, created=False)

    def get_run(self, run_id: str) -> ReviewRun | None:
        """Load one complete review run by ID."""
        with Session(self.engine) as session:
            statement = _run_query().where(ReviewRunRecord.id == run_id)
            record = session.scalar(statement)
            return _record_to_model(record) if record is not None else None

    def get_by_idempotency_key(self, idempotency_key: str) -> ReviewRun | None:
        """Load one complete review run by its deterministic key."""
        with Session(self.engine) as session:
            record = self._select_by_idempotency(session, idempotency_key)
            return _record_to_model(record) if record is not None else None

    def list_runs(
        self,
        *,
        limit: int = 20,
        repository_path: str | None = None,
        status: ReviewStatus | None = None,
    ) -> list[ReviewRun]:
        """List recent complete records with optional repository/status filters."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        statement = _run_query().order_by(ReviewRunRecord.started_at.desc()).limit(limit)
        if repository_path is not None:
            normalized_path = (repository_path if "://" in repository_path else str(
                Path(repository_path).expanduser().resolve()))
            statement = statement.where(
                ReviewRunRecord.repository_path_hash == _path_hash(normalized_path),
                ReviewRunRecord.repository_path == normalized_path,
            )
        if status is not None:
            statement = statement.where(ReviewRunRecord.status == status.value)
        with Session(self.engine) as session:
            return [_record_to_model(record) for record in session.scalars(statement).all()]

    def count_runs(self) -> int:
        """Return the number of top-level persisted runs."""
        with Session(self.engine) as session:
            return int(session.scalar(select(func.count()).select_from(ReviewRunRecord)) or 0)

    def ping(self) -> None:
        """Raise when the configured database cannot serve a trivial query."""
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def claim_github_delivery(
        self,
        *,
        delivery_id: str,
        event_name: str,
        action: str,
        payload_sha256: str,
        repository_full_name: str = "",
        pull_number: int | None = None,
        head_sha: str = "",
        installation_id: int | None = None,
    ) -> bool:
        """Atomically claim a delivery; return false when it was already recorded."""
        record = GitHubDeliveryRecord(
            delivery_id=delivery_id,
            event_name=event_name,
            action=action,
            payload_sha256=payload_sha256,
            repository_full_name=repository_full_name,
            pull_number=pull_number,
            head_sha=head_sha,
            installation_id=installation_id,
        )
        try:
            with Session(self.engine) as session, session.begin():
                session.add(record)
                session.flush()
            return True
        except IntegrityError:
            return False

    def enqueue_github_delivery(
        self,
        *,
        delivery_id: str,
        event_name: str,
        action: str,
        payload_sha256: str,
        event_payload: dict[str, Any],
        repository_full_name: str,
        pull_number: int,
        head_sha: str,
        installation_id: int,
        max_attempts: int = 5,
    ) -> bool:
        """Atomically record a delivery and enqueue its validated event."""
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        now = datetime.now(timezone.utc)
        try:
            with Session(self.engine) as session, session.begin():
                session.add(
                    GitHubDeliveryRecord(
                        delivery_id=delivery_id,
                        event_name=event_name,
                        action=action,
                        payload_sha256=payload_sha256,
                        repository_full_name=repository_full_name,
                        pull_number=pull_number,
                        head_sha=head_sha,
                        installation_id=installation_id,
                        status="queued",
                    )
                )
                session.add(
                    GitHubReviewJobRecord(
                        delivery_id=delivery_id,
                        event_payload=event_payload,
                        status="queued",
                        max_attempts=max_attempts,
                        available_at=now,
                    )
                )
                session.flush()
            return True
        except IntegrityError:
            return False

    def claim_github_job(
        self,
        *,
        worker_id: str,
        lease_seconds: float = 300,
    ) -> ClaimedReviewJob | None:
        """Lease the oldest available job using a portable compare-and-set."""
        if not worker_id or len(worker_id) > 255:
            raise ValueError("worker_id must contain 1 to 255 characters")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now = datetime.now(timezone.utc)
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        for _ in range(10):
            with Session(self.engine) as session, session.begin():
                self._recover_expired_jobs(session, now)
                candidate = session.scalar(
                    select(GitHubReviewJobRecord.delivery_id)
                    .where(
                        GitHubReviewJobRecord.status == "queued",
                        GitHubReviewJobRecord.available_at <= now,
                    )
                    .order_by(
                        GitHubReviewJobRecord.available_at,
                        GitHubReviewJobRecord.created_at,
                    )
                    .limit(1)
                )
                if candidate is None:
                    return None
                result = session.execute(
                    update(GitHubReviewJobRecord)
                    .where(
                        GitHubReviewJobRecord.delivery_id == candidate,
                        GitHubReviewJobRecord.status == "queued",
                        GitHubReviewJobRecord.available_at <= now,
                    )
                    .values(
                        status="leased",
                        attempt_count=GitHubReviewJobRecord.attempt_count + 1,
                        lease_owner=worker_id,
                        lease_expires_at=lease_expires_at,
                        updated_at=now,
                    )
                )
                if result.rowcount != 1:
                    continue
                record = session.get(GitHubReviewJobRecord, candidate)
                session.get(GitHubDeliveryRecord, candidate).status = "processing"
                return ClaimedReviewJob(
                    delivery_id=record.delivery_id,
                    event_payload=dict(record.event_payload),
                    attempt_count=record.attempt_count,
                    max_attempts=record.max_attempts,
                    lease_owner=record.lease_owner,
                    lease_expires_at=_as_utc(record.lease_expires_at),
                )
        return None

    def renew_github_job_lease(
        self,
        delivery_id: str,
        *,
        worker_id: str,
        lease_seconds: float = 300,
    ) -> bool:
        """Extend an active lease, returning false if ownership was lost."""
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session, session.begin():
            result = session.execute(
                update(GitHubReviewJobRecord)
                .where(
                    GitHubReviewJobRecord.delivery_id == delivery_id,
                    GitHubReviewJobRecord.status == "leased",
                    GitHubReviewJobRecord.lease_owner == worker_id,
                    GitHubReviewJobRecord.lease_expires_at > now,
                )
                .values(
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    updated_at=now,
                )
            )
            return result.rowcount == 1

    def complete_github_job(self, delivery_id: str, *, worker_id: str) -> bool:
        """Mark an owned lease successful."""
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session, session.begin():
            result = session.execute(
                update(GitHubReviewJobRecord)
                .where(
                    GitHubReviewJobRecord.delivery_id == delivery_id,
                    GitHubReviewJobRecord.status == "leased",
                    GitHubReviewJobRecord.lease_owner == worker_id,
                )
                .values(
                    status="succeeded",
                    lease_owner="",
                    lease_expires_at=None,
                    last_error="",
                    updated_at=now,
                )
            )
            return result.rowcount == 1

    def fail_github_job(
        self,
        delivery_id: str,
        *,
        worker_id: str,
        error_message: str,
        retry_delay_seconds: float,
        retryable: bool = True,
    ) -> str:
        """Retry or permanently fail an owned job and return its new status."""
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds cannot be negative")
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session, session.begin():
            record = session.get(GitHubReviewJobRecord, delivery_id)
            if record is None:
                raise KeyError(f"Unknown GitHub review job: {delivery_id}")
            if record.status != "leased" or record.lease_owner != worker_id:
                raise RuntimeError(f"GitHub review job lease was lost: {delivery_id}")
            should_retry = retryable and record.attempt_count < record.max_attempts
            record.status = "queued" if should_retry else "dead"
            record.available_at = now + timedelta(seconds=retry_delay_seconds)
            record.lease_owner = ""
            record.lease_expires_at = None
            record.last_error = error_message[:10_000]
            record.updated_at = now
            delivery = session.get(GitHubDeliveryRecord, delivery_id)
            delivery.status = "retrying" if should_retry else "failed"
            delivery.error_message = record.last_error
            delivery.updated_at = now
            return record.status

    def replay_github_job(self, delivery_id: str, *, reset_attempts: bool = True) -> bool:
        """Requeue a dead job for explicit operator-controlled replay."""
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session, session.begin():
            record = session.get(GitHubReviewJobRecord, delivery_id)
            if record is None or record.status != "dead":
                return False
            record.status = "queued"
            if reset_attempts:
                record.attempt_count = 0
            record.available_at = now
            record.lease_owner = ""
            record.lease_expires_at = None
            record.last_error = ""
            record.updated_at = now
            delivery = session.get(GitHubDeliveryRecord, delivery_id)
            delivery.status = "queued"
            delivery.error_message = ""
            delivery.updated_at = now
            return True

    def get_github_job(self, delivery_id: str) -> dict[str, Any] | None:
        """Return a JSON-friendly durable queue record."""
        with Session(self.engine) as session:
            record = session.get(GitHubReviewJobRecord, delivery_id)
            return _job_to_dict(record) if record is not None else None

    def list_github_jobs(
        self,
        *,
        limit: int = 20,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List recent durable jobs."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        statement = (
            select(GitHubReviewJobRecord)
            .order_by(GitHubReviewJobRecord.created_at.desc())
            .limit(limit)
        )
        if status is not None:
            statement = statement.where(GitHubReviewJobRecord.status == status)
        with Session(self.engine) as session:
            return [_job_to_dict(record) for record in session.scalars(statement).all()]

    def get_github_publication(self, delivery_id: str) -> dict[str, Any]:
        """Return retry-safe publication progress, creating it when absent."""
        with Session(self.engine) as session, session.begin():
            record = session.get(GitHubPublicationRecord, delivery_id)
            if record is None:
                if session.get(GitHubDeliveryRecord, delivery_id) is None:
                    raise KeyError(f"Unknown GitHub delivery: {delivery_id}")
                record = GitHubPublicationRecord(delivery_id=delivery_id)
                session.add(record)
                session.flush()
            return {
                "delivery_id": record.delivery_id,
                "check_completed": record.check_completed,
                "comments_completed": record.comments_completed,
                "updated_at": _as_utc(record.updated_at),
            }

    def update_github_publication(
        self,
        delivery_id: str,
        *,
        check_completed: bool | None = None,
        comments_completed: bool | None = None,
    ) -> None:
        """Persist completed publication phases without resetting prior progress."""
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session, session.begin():
            record = session.get(GitHubPublicationRecord, delivery_id)
            if record is None:
                if session.get(GitHubDeliveryRecord, delivery_id) is None:
                    raise KeyError(f"Unknown GitHub delivery: {delivery_id}")
                record = GitHubPublicationRecord(delivery_id=delivery_id)
                session.add(record)
            if check_completed is not None:
                record.check_completed = check_completed
            if comments_completed is not None:
                record.comments_completed = comments_completed
            record.updated_at = now

    def update_github_delivery(
        self,
        delivery_id: str,
        *,
        status: str,
        review_run_id: str | None | object = _UNSET,
        check_run_id: int | None | object = _UNSET,
        error_message: str | object = _UNSET,
    ) -> None:
        """Update processing/publication state for an already claimed delivery."""
        with Session(self.engine) as session, session.begin():
            record = session.get(GitHubDeliveryRecord, delivery_id)
            if record is None:
                raise KeyError(f"Unknown GitHub delivery: {delivery_id}")
            record.status = status
            if review_run_id is not _UNSET:
                record.review_run_id = review_run_id
            if check_run_id is not _UNSET:
                record.check_run_id = check_run_id
            if error_message is not _UNSET:
                record.error_message = str(error_message)
            record.updated_at = datetime.now(timezone.utc)

    def get_github_delivery(self, delivery_id: str) -> dict[str, Any] | None:
        """Return a JSON-friendly delivery record for diagnostics and tests."""
        with Session(self.engine) as session:
            record = session.get(GitHubDeliveryRecord, delivery_id)
            if record is None:
                return None
            return _delivery_to_dict(record)

    def list_github_deliveries(
        self,
        *,
        limit: int = 20,
        status: str | None = None,
        repository_full_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """List recent webhook deliveries without loading review object graphs."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        statement = select(GitHubDeliveryRecord).order_by(GitHubDeliveryRecord.received_at.desc()).limit(limit)
        if status is not None:
            statement = statement.where(GitHubDeliveryRecord.status == status)
        if repository_full_name is not None:
            statement = statement.where(GitHubDeliveryRecord.repository_full_name == repository_full_name)
        with Session(self.engine) as session:
            return [_delivery_to_dict(record) for record in session.scalars(statement).all()]

    def close(self) -> None:
        """Release database connections."""
        self.engine.dispose()

    @staticmethod
    def _select_by_idempotency(session: Session, idempotency_key: str) -> ReviewRunRecord | None:
        return session.scalar(_run_query().where(ReviewRunRecord.idempotency_key == idempotency_key))

    @staticmethod
    def _recover_expired_jobs(session: Session, now: datetime) -> None:
        expired = list(
            session.scalars(
                select(GitHubReviewJobRecord).where(
                    GitHubReviewJobRecord.status == "leased",
                    GitHubReviewJobRecord.lease_expires_at <= now,
                )
            )
        )
        for record in expired:
            record.status = "queued"
            record.available_at = now
            record.lease_owner = ""
            record.lease_expires_at = None
            record.last_error = "Worker lease expired; job recovered"
            record.updated_at = now
            delivery = session.get(GitHubDeliveryRecord, record.delivery_id)
            delivery.status = "retrying"
            delivery.error_message = record.last_error
            delivery.updated_at = now


def _run_query():
    return select(ReviewRunRecord).options(
        selectinload(ReviewRunRecord.changed_files),
        selectinload(ReviewRunRecord.findings),
        selectinload(ReviewRunRecord.analyzer_executions),
    )


def _model_to_record(review_run: ReviewRun) -> ReviewRunRecord:
    record = ReviewRunRecord(
        id=review_run.id,
        idempotency_key=review_run.idempotency_key,
        repository_path=review_run.repository_path,
        repository_path_hash=_path_hash(review_run.repository_path),
        base_revision=review_run.base_revision,
        head_revision=review_run.head_revision,
        resolved_head_revision=review_run.resolved_head_revision,
        effective_base_revision=review_run.effective_base_revision,
        status=review_run.status.value,
        model_name=review_run.model_name,
        config_hash=review_run.config_hash,
        started_at=review_run.started_at,
        finished_at=review_run.finished_at,
        error_message=review_run.error_message,
        static_analysis_requested=review_run.static_analysis_requested,
        summary=review_run.output.summary,
        diagnostics=list(review_run.diagnostics),
    )
    record.changed_files = [
        ChangedFileRecord(
            position=position,
            path=changed_file.path,
            old_path=changed_file.old_path,
            change_type=changed_file.change_type.value,
            language=changed_file.language,
            patch=changed_file.patch,
            hunks=[hunk.model_dump(mode="json") for hunk in changed_file.hunks],
            added_lines=changed_file.added_lines,
            deleted_lines=changed_file.deleted_lines,
            is_binary=changed_file.is_binary,
            is_truncated=changed_file.is_truncated,
        ) for position, changed_file in enumerate(review_run.changed_files)
    ]
    record.findings = [
        FindingRecord(
            position=position,
            rule_id=finding.rule_id,
            severity=finding.severity.value,
            confidence=finding.confidence,
            category=finding.category,
            file_path=finding.file_path,
            start_line=finding.start_line,
            end_line=finding.end_line,
            title=finding.title,
            description=finding.description,
            suggestion=finding.suggestion,
            source=finding.source,
            publishable=finding.publishable,
        ) for position, finding in enumerate(review_run.output.findings)
    ]
    record.analyzer_executions = [
        AnalyzerExecutionRecord(
            position=position,
            tool=execution.tool,
            runtime=execution.runtime,
            command=list(execution.command),
            status=execution.status.value,
            exit_code=execution.exit_code,
            duration_seconds=execution.duration_seconds,
            stdout=execution.stdout,
            stderr=execution.stderr,
            findings_count=execution.findings_count,
        ) for position, execution in enumerate(review_run.analyzer_executions)
    ]
    return record


def _record_to_model(record: ReviewRunRecord) -> ReviewRun:
    return ReviewRun(
        id=record.id,
        idempotency_key=record.idempotency_key,
        repository_path=record.repository_path,
        base_revision=record.base_revision,
        head_revision=record.head_revision,
        resolved_head_revision=record.resolved_head_revision,
        effective_base_revision=record.effective_base_revision,
        status=ReviewStatus(record.status),
        model_name=record.model_name,
        config_hash=record.config_hash,
        started_at=_as_utc(record.started_at),
        finished_at=_as_utc(record.finished_at),
        error_message=record.error_message,
        static_analysis_requested=record.static_analysis_requested,
        changed_files=[
            ChangedFile(
                path=changed_file.path,
                old_path=changed_file.old_path,
                change_type=ChangeType(changed_file.change_type),
                language=changed_file.language,
                patch=changed_file.patch,
                hunks=changed_file.hunks,
                added_lines=changed_file.added_lines,
                deleted_lines=changed_file.deleted_lines,
                is_binary=changed_file.is_binary,
                is_truncated=changed_file.is_truncated,
            ) for changed_file in record.changed_files
        ],
        analyzer_executions=[
            AnalyzerExecution(
                tool=execution.tool,
                runtime=execution.runtime,
                command=execution.command,
                status=AnalyzerStatus(execution.status),
                exit_code=execution.exit_code,
                duration_seconds=execution.duration_seconds,
                stdout=execution.stdout,
                stderr=execution.stderr,
                findings_count=execution.findings_count,
            ) for execution in record.analyzer_executions
        ],
        output=ReviewOutput(
            summary=record.summary,
            findings=[
                Finding(
                    rule_id=finding.rule_id,
                    severity=Severity(finding.severity),
                    confidence=finding.confidence,
                    category=finding.category,
                    file_path=finding.file_path,
                    start_line=finding.start_line,
                    end_line=finding.end_line,
                    title=finding.title,
                    description=finding.description,
                    suggestion=finding.suggestion,
                    source=finding.source,
                    publishable=finding.publishable,
                ) for finding in record.findings
            ],
        ),
        diagnostics=list(record.diagnostics or []),
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _path_hash(path: str) -> str:
    return sha256(path.encode("utf-8")).hexdigest()


def _delivery_to_dict(record: GitHubDeliveryRecord) -> dict[str, Any]:
    return {
        "delivery_id": record.delivery_id,
        "event_name": record.event_name,
        "action": record.action,
        "payload_sha256": record.payload_sha256,
        "repository_full_name": record.repository_full_name,
        "pull_number": record.pull_number,
        "head_sha": record.head_sha,
        "installation_id": record.installation_id,
        "status": record.status,
        "review_run_id": record.review_run_id,
        "check_run_id": record.check_run_id,
        "error_message": record.error_message,
        "received_at": _as_utc(record.received_at),
        "updated_at": _as_utc(record.updated_at),
    }


def _job_to_dict(record: GitHubReviewJobRecord) -> dict[str, Any]:
    return {
        "delivery_id": record.delivery_id,
        "event_payload": dict(record.event_payload),
        "status": record.status,
        "attempt_count": record.attempt_count,
        "max_attempts": record.max_attempts,
        "available_at": _as_utc(record.available_at),
        "lease_owner": record.lease_owner,
        "lease_expires_at": _as_utc(record.lease_expires_at),
        "last_error": record.last_error,
        "created_at": _as_utc(record.created_at),
        "updated_at": _as_utc(record.updated_at),
    }
