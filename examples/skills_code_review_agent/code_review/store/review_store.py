#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""ReviewStore abstraction and SQL implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from code_review.redaction import redact_data

from .models import (
    Base,
    FilterEventModel,
    FindingModel,
    ReportModel,
    ReviewTaskModel,
    SandboxRunModel,
)


DEFAULT_DB_URL = "sqlite:///out/review.db"
DEFAULT_SCHEMA_VERSION = "1.0.0"

_TASK_STATUSES = {
    "running",
    "completed",
    "completed_with_warnings",
    "failed",
}
_RUN_STATUSES = {"ok", "failed", "timeout", "blocked", "error"}


def _ensure_sqlite_parent(db_url: str) -> None:
    """为文件型 SQLite URL 创建其父目录。"""

    url = make_url(db_url)
    if not url.drivername.startswith("sqlite"):
        return
    database = url.database
    if not database or database == ":memory:":
        return
    Path(database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def _enable_sqlite_foreign_keys(
    dbapi_connection: Any,
    _connection_record: Any,
) -> None:
    """在每个 SQLite 连接建立时启用外键约束。"""

    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _versioned_json(
    value: Any,
    *,
    schema_version: str = DEFAULT_SCHEMA_VERSION,
) -> dict[str, Any]:
    """脱敏 JSON 字段并附加持久化 schema 版本。"""

    redacted = redact_data(value)
    if isinstance(redacted, Mapping):
        payload = dict(redacted)
        payload.setdefault("schema_version", schema_version)
        return payload
    return {
        "schema_version": schema_version,
        "value": redacted,
    }


def _model_dict(model: Any) -> dict[str, Any]:
    """将一个 ORM 行转换为脱离会话的 JSON 安全字典。"""

    result: dict[str, Any] = {}
    for column in model.__table__.columns:
        value = getattr(model, column.name)
        if isinstance(value, datetime):
            value = value.isoformat()
        result[column.name] = deepcopy(value)
    return result


class ReviewStore(ABC):
    """Backend-neutral persistence boundary for one review lifecycle."""

    @abstractmethod
    def initialize(self) -> None:
        """在表不存在时初始化业务 schema。"""

    @abstractmethod
    def create_task(self, task: Mapping[str, Any]) -> dict[str, Any]:
        """持久化一条运行中的评审任务。"""

    @abstractmethod
    def update_task(self, task_id: str, **updates: Any) -> dict[str, Any]:
        """更新允许变更的评审任务状态字段。"""

    @abstractmethod
    def add_sandbox_run(
        self,
        task_id: str,
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        """持久化一次受治理的沙箱执行尝试。"""

    @abstractmethod
    def add_filter_event(
        self,
        task_id: str,
        filter_event: Mapping[str, Any],
    ) -> dict[str, Any]:
        """持久化一次沙箱执行前的治理决策。"""

    @abstractmethod
    def add_finding(
        self,
        task_id: str,
        finding: Mapping[str, Any],
    ) -> dict[str, Any]:
        """持久化一条已结构化和脱敏的 finding。"""

    @abstractmethod
    def save_report(
        self,
        task_id: str,
        report: Mapping[str, Any],
    ) -> dict[str, Any]:
        """创建或替换任务对应的 canonical 报告。"""

    @abstractmethod
    def get_task_bundle(self, task_id: str) -> dict[str, Any] | None:
        """按任务 ID 聚合返回任务、运行、事件、finding 和报告。"""

    @abstractmethod
    def list_task_summaries(self) -> list[dict[str, Any]]:
        """返回不含报告正文、输入内容或路径的任务安全摘要列表。"""

    @abstractmethod
    def delete_task(self, task_id: str) -> bool:
        """删除任务及其级联关联的子记录。"""

    @abstractmethod
    def close(self) -> None:
        """释放存储后端持有的连接和资源。"""


class SqlReviewStore(ReviewStore):
    """Synchronous SQLAlchemy implementation with a URL-only backend seam."""

    def __init__(self, db_url: str = DEFAULT_DB_URL) -> None:
        """保存数据库 URL，并延迟创建 SQLAlchemy 引擎。"""

        if not db_url.strip():
            raise ValueError("db_url must not be empty")
        self._db_url = db_url
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None

    @property
    def engine(self) -> Engine:
        """返回已初始化的引擎，供诊断或迁移入口使用。"""

        if self._engine is None:
            raise RuntimeError("review store is not initialized")
        return self._engine

    def initialize(self) -> None:
        """幂等创建五张评审表并准备会话工厂。"""

        if self._engine is not None:
            Base.metadata.create_all(self._engine)
            return

        _ensure_sqlite_parent(self._db_url)
        engine = create_engine(self._db_url)
        if engine.dialect.name == "sqlite":
            event.listen(engine, "connect", _enable_sqlite_foreign_keys)
        Base.metadata.create_all(engine)
        self._engine = engine
        self._session_factory = sessionmaker(
            bind=engine,
            expire_on_commit=False,
        )

    def _session(self) -> Session:
        """返回一个新的数据库会话，未初始化时明确失败。"""

        if self._session_factory is None:
            raise RuntimeError("review store is not initialized")
        return self._session_factory()

    def create_task(self, task: Mapping[str, Any]) -> dict[str, Any]:
        """校验、脱敏并写入一条初始评审任务。"""

        status = str(task.get("status", "running"))
        if status not in _TASK_STATUSES:
            raise ValueError(f"invalid review task status: {status}")

        task_id = str(task.get("id", "")).strip()
        if not task_id:
            raise ValueError("task id must not be empty")
        input_type = str(task.get("input_type", "")).strip()
        if not input_type:
            raise ValueError("input_type must not be empty")

        config = task.get("config", {})
        schema_version = (
            str(config.get("schema_version", DEFAULT_SCHEMA_VERSION))
            if isinstance(config, Mapping)
            else DEFAULT_SCHEMA_VERSION
        )
        row = ReviewTaskModel(
            id=task_id,
            status=status,
            input_type=input_type,
            input_ref=str(redact_data(task.get("input_ref", ""))),
            diff_summary=_versioned_json(
                task.get("diff_summary", {}),
                schema_version=schema_version,
            ),
            config=_versioned_json(config, schema_version=schema_version),
            error_type=redact_data(task.get("error_type")),
            error_message=redact_data(task.get("error_message")),
        )
        with self._session() as session:
            session.add(row)
            session.commit()
            return _model_dict(row)

    def update_task(self, task_id: str, **updates: Any) -> dict[str, Any]:
        """更新白名单内的任务字段，并对可持久化值执行脱敏。"""

        allowed = {
            "status",
            "input_ref",
            "diff_summary",
            "config",
            "error_type",
            "error_message",
        }
        unknown = set(updates) - allowed
        if unknown:
            raise ValueError(f"unsupported task updates: {sorted(unknown)}")
        if "status" in updates and updates["status"] not in _TASK_STATUSES:
            raise ValueError(f"invalid review task status: {updates['status']}")

        with self._session() as session:
            row = session.get(ReviewTaskModel, task_id)
            if row is None:
                raise KeyError(task_id)
            schema_version = str(
                row.config.get("schema_version", DEFAULT_SCHEMA_VERSION)
            )
            for name, value in updates.items():
                if name in {"diff_summary", "config"}:
                    value = _versioned_json(
                        value,
                        schema_version=schema_version,
                    )
                else:
                    value = redact_data(value)
                setattr(row, name, value)
            session.commit()
            return _model_dict(row)

    def add_sandbox_run(
        self,
        task_id: str,
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        """脱敏并保存一次沙箱运行摘要。"""

        status = str(run.get("status", "")).strip()
        if status not in _RUN_STATUSES:
            raise ValueError(f"invalid sandbox run status: {status}")
        row = SandboxRunModel(
            task_id=task_id,
            status=status,
            exit_code=run.get("exit_code"),
            timed_out=bool(run.get("timed_out", False)),
            truncated=bool(run.get("truncated", False)),
            filter_action=redact_data(run.get("filter_action")),
            stdout_excerpt=str(redact_data(run.get("stdout_excerpt", ""))),
            stderr_excerpt=str(redact_data(run.get("stderr_excerpt", ""))),
            error_type=redact_data(run.get("error_type")),
            duration_ms=int(run.get("duration_ms", 0)),
        )
        with self._session() as session:
            session.add(row)
            session.commit()
            return _model_dict(row)

    def add_filter_event(
        self,
        task_id: str,
        filter_event: Mapping[str, Any],
    ) -> dict[str, Any]:
        """脱敏并保存执行前 Filter 决策事件。"""

        schema_version = str(
            filter_event.get("schema_version", DEFAULT_SCHEMA_VERSION)
        )
        row = FilterEventModel(
            task_id=task_id,
            stage=str(redact_data(filter_event.get("stage", ""))),
            target=str(redact_data(filter_event.get("target", ""))),
            action=str(redact_data(filter_event.get("action", ""))),
            rule=str(redact_data(filter_event.get("rule", ""))),
            reasons=_versioned_json(
                filter_event.get("reasons", []),
                schema_version=schema_version,
            ),
        )
        with self._session() as session:
            session.add(row)
            session.commit()
            return _model_dict(row)

    def add_finding(
        self,
        task_id: str,
        finding: Mapping[str, Any],
    ) -> dict[str, Any]:
        """脱敏并保存一条已去重或已分桶的结构化 finding。"""

        schema_version = str(
            finding.get("schema_version", DEFAULT_SCHEMA_VERSION)
        )
        row = FindingModel(
            task_id=task_id,
            severity=str(finding["severity"]),
            category=str(finding["category"]),
            file=str(redact_data(finding["file"])),
            line=int(finding["line"]),
            title=str(redact_data(finding["title"])),
            evidence=str(redact_data(finding["evidence"])),
            recommendation=str(redact_data(finding["recommendation"])),
            confidence=float(finding["confidence"]),
            source=str(finding["source"]),
            rule_id=str(finding.get("rule_id", "")),
            bucket=str(finding.get("bucket", "")),
            dedup_key=str(redact_data(finding.get("dedup_key", ""))),
            extra=_versioned_json(
                finding.get("extra", {}),
                schema_version=schema_version,
            ),
        )
        with self._session() as session:
            session.add(row)
            session.commit()
            return _model_dict(row)

    def save_report(
        self,
        task_id: str,
        report: Mapping[str, Any],
    ) -> dict[str, Any]:
        """按任务 ID upsert 已脱敏的 canonical 报告及其摘要字段。"""

        schema_version = str(
            report.get("schema_version", DEFAULT_SCHEMA_VERSION)
        )
        values = {
            "schema_version": schema_version,
            "rule_pack_version": str(report["rule_pack_version"]),
            "config_digest": str(report["config_digest"]),
            "input_sha256": str(report["input_sha256"]),
            "summary": _versioned_json(
                report.get("summary", {}),
                schema_version=schema_version,
            ),
            "severity_stats": _versioned_json(
                report.get("severity_stats", {}),
                schema_version=schema_version,
            ),
            "filter_summary": _versioned_json(
                report.get("filter_summary", {}),
                schema_version=schema_version,
            ),
            "sandbox_summary": _versioned_json(
                report.get("sandbox_summary", {}),
                schema_version=schema_version,
            ),
            "metrics": _versioned_json(
                report.get("metrics", {}),
                schema_version=schema_version,
            ),
            "report": _versioned_json(
                report.get("report", {}),
                schema_version=schema_version,
            ),
        }
        with self._session() as session:
            row = session.scalar(
                select(ReportModel).where(ReportModel.task_id == task_id)
            )
            if row is None:
                row = ReportModel(task_id=task_id, **values)
                session.add(row)
            else:
                for name, value in values.items():
                    setattr(row, name, value)
            session.commit()
            return _model_dict(row)

    def get_task_bundle(self, task_id: str) -> dict[str, Any] | None:
        """查询任务的五个持久化域，并返回可回放 bundle。"""

        with self._session() as session:
            task = session.get(ReviewTaskModel, task_id)
            if task is None:
                return None
            runs = session.scalars(
                select(SandboxRunModel)
                .where(SandboxRunModel.task_id == task_id)
                .order_by(SandboxRunModel.id)
            ).all()
            events = session.scalars(
                select(FilterEventModel)
                .where(FilterEventModel.task_id == task_id)
                .order_by(FilterEventModel.id)
            ).all()
            findings = session.scalars(
                select(FindingModel)
                .where(FindingModel.task_id == task_id)
                .order_by(
                    FindingModel.file,
                    FindingModel.line,
                    FindingModel.category,
                    FindingModel.id,
                )
            ).all()
            report = session.scalar(
                select(ReportModel).where(ReportModel.task_id == task_id)
            )
            return {
                "task": _model_dict(task),
                "sandbox_runs": [_model_dict(row) for row in runs],
                "filter_events": [_model_dict(row) for row in events],
                "findings": [_model_dict(row) for row in findings],
                "report": _model_dict(report) if report is not None else None,
            }

    def list_task_summaries(self) -> list[dict[str, Any]]:
        """按创建时间倒序返回 CLI 所需最小任务字段，避免调用方接触存储会话。"""

        with self._session() as session:
            tasks = session.scalars(
                select(ReviewTaskModel).order_by(
                    ReviewTaskModel.created_at.desc(),
                    ReviewTaskModel.id,
                )
            ).all()
            return [
                {
                    "id": task.id,
                    "status": task.status,
                    "input_type": task.input_type,
                    "created_at": task.created_at.isoformat(),
                }
                for task in tasks
            ]

    def delete_task(self, task_id: str) -> bool:
        """删除指定任务；不存在时返回 false 而不抛异常。"""

        with self._session() as session:
            row = session.get(ReviewTaskModel, task_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def close(self) -> None:
        """释放当前引擎并清空会话工厂引用。"""

        if self._engine is None:
            return
        self._engine.dispose()
        self._engine = None
        self._session_factory = None
