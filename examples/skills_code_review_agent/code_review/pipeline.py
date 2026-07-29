#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""One deterministic eight-stage review pipeline shared by every entry point."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from code_review.config import ReviewConfig
from code_review.dedup import BucketedFindings, route_findings
from code_review.inputs import InputResult, InputValidationError, load_input
from code_review.llm_enhancer import LlmEnhancer
from code_review.metrics import MetricsCollector
from code_review.redaction import contains_plaintext_secret, redact_data
from code_review.report import (
    CanonicalReportWriter,
    ReportValidationError,
    ReportWriteError,
)
from code_review.store import ReviewStore
from code_review.trace import TraceSink, emit_trace

if TYPE_CHECKING:
    from lib.diff_parser import ChangeSet


_FILTER_ACTIONS = {"allow", "deny", "needs_human_review"}
_RUN_STATUSES = {"ok", "failed", "timeout", "blocked", "error"}
_RUNTIME_TYPES = {"container", "cube", "local", "fake"}
_INPUT_NAMES = ("diff_file", "repo_path", "files", "fixture")
_LOGGER = logging.getLogger("code_review_agent")


class PipelineFatalError(RuntimeError):
    """表示输入、持久化或报告等无法形成可交付结果的致命失败。"""


class GovernancePort(Protocol):
    """定义 C1 Filter 实现需要满足的 pipeline 前置决策端口。"""

    def decide(
        self,
        *,
        task_id: str,
        change_set: "ChangeSet",
        config: ReviewConfig,
    ) -> Mapping[str, Any]:
        """返回 allow、deny 或 needs_human_review 及其脱敏审计数据。"""


class SandboxPort(Protocol):
    """定义 C2 runtime 实现需要满足的受控检查与清理端口。"""

    runtime_type: str

    def execute(
        self,
        *,
        task_id: str,
        change_set: "ChangeSet",
        config: ReviewConfig,
    ) -> Mapping[str, Any]:
        """在已获准的隔离任务域执行注册脚本并返回结构化结果。"""

    def cleanup(self, *, task_id: str) -> None:
        """删除本次任务 workspace；失败由 pipeline 转为无路径 warning。"""


InputLoader = Callable[..., InputResult]
TaskIdFactory = Callable[[], str]


@dataclass(frozen=True)
class PipelineResult:
    """一次成功评审的公开结果，只包含脱敏报告及输出路径。"""

    task_id: str
    status: str
    report: dict[str, Any]
    json_path: Path
    markdown_path: Path


def _new_task_id() -> str:
    """生成不含主机信息、可安全进入存储和 Telemetry 的任务标识。"""

    return f"review-{uuid4().hex}"


def _safe_code(value: object, *, fallback: str) -> str:
    """将端口提供的标识收敛为短枚举样式，避免路径或原文进入出口。"""

    if not isinstance(value, str):
        return fallback
    candidate = value.strip().lower().replace("-", "_")
    if not candidate or len(candidate) > 80:
        return fallback
    if not all(character.isascii() and (character.isalnum() or character == "_") for character in candidate):
        return fallback
    return candidate


def _warning(code: object, *, stage: str) -> dict[str, str]:
    """构造不含端口原始消息、可安全持久化的运行告警。"""

    normalized_code = _safe_code(code, fallback="pipeline_warning")
    return {
        "code": normalized_code,
        "message": f"{normalized_code} occurred during {stage}",
        "stage": stage,
    }


def _source_input_type(input_options: Mapping[str, Any]) -> str:
    """在读取任何原始输入前验证唯一输入形态并返回安全类型标识。"""

    selected = []
    for name in _INPUT_NAMES:
        value = input_options.get(name)
        if name == "files":
            if value:
                selected.append(name)
        elif value is not None:
            selected.append(name)
    if len(selected) != 1:
        raise PipelineFatalError("pipeline_input_selection_invalid")
    return selected[0]


def _input_summary(change_set: "ChangeSet") -> dict[str, Any]:
    """从 ChangeSet 构造仅含元数据和脱敏路径的报告/数据库输入摘要。"""

    files = [
        {
            "path": file_change.normalized_path,
            "status": file_change.status,
            "review_scope": file_change.review_scope,
        }
        for file_change in change_set.files
    ]
    return redact_data(
        {
            "source_kind": change_set.source_kind,
            "file_count": change_set.file_count,
            "hunk_count": change_set.hunk_count,
            "additions": change_set.additions,
            "deletions": change_set.deletions,
            "files": files,
            "parse_warnings": [
                _safe_code(warning, fallback="parse_warning")
                for warning in change_set.parse_warnings
            ],
        }
    )


def _diff_summary(change_set: "ChangeSet") -> dict[str, Any]:
    """构造禁止包含原始补丁或代码行的任务落库摘要。"""

    scope_counts: dict[str, int] = {}
    for file_change in change_set.files:
        scope = _safe_code(file_change.review_scope, fallback="unknown_scope")
        scope_counts[scope] = scope_counts.get(scope, 0) + 1
    summary = _input_summary(change_set)
    return {
        "input_sha256": change_set.input_sha256,
        "file_count": change_set.file_count,
        "hunk_count": change_set.hunk_count,
        "additions": change_set.additions,
        "deletions": change_set.deletions,
        "review_scopes": dict(sorted(scope_counts.items())),
        "files": [item["path"] for item in summary["files"]],
    }


def _sanitize_filter_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """将 Filter 事件收敛为枚举/代码型字段，拒绝端口带入的原始原因文本。"""

    reasons = event.get("reasons", ())
    if not isinstance(reasons, (list, tuple)):
        reasons = ()
    return {
        "stage": _safe_code(event.get("stage"), fallback="pre_execution"),
        "target": _safe_code(event.get("target"), fallback="registered_script"),
        "action": _safe_code(event.get("action"), fallback="deny"),
        "rule": _safe_code(event.get("rule"), fallback="governance"),
        "reasons": [
            _safe_code(reason, fallback="redacted_reason") for reason in reasons
        ],
    }


def _sanitize_sandbox_run(result: Mapping[str, Any]) -> dict[str, Any]:
    """二次脱敏沙箱结果并仅保留持久化契约允许的运行字段。"""

    status = _safe_code(result.get("status"), fallback="error")
    if status not in _RUN_STATUSES:
        status = "error"
    duration_value = result.get("duration_ms", 0)
    duration_ms = duration_value if isinstance(duration_value, int) and duration_value >= 0 else 0
    exit_code = result.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        exit_code = None
    return redact_data(
        {
            "status": status,
            "exit_code": exit_code,
            "timed_out": bool(result.get("timed_out", False)),
            "truncated": bool(result.get("truncated", False)),
            "filter_action": "allow",
            "stdout_excerpt": result.get("stdout_excerpt", ""),
            "stderr_excerpt": result.get("stderr_excerpt", ""),
            "error_type": _safe_code(
                result.get("error_type"), fallback="sandbox_error"
            )
            if result.get("error_type")
            else None,
            "duration_ms": duration_ms,
        }
    )


def _safe_candidates(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """提取并二次脱敏沙箱 finding 候选，忽略非对象载荷。"""

    findings = result.get("findings", ())
    if not isinstance(findings, (list, tuple)):
        return []
    return [
        redact_data(dict(finding))
        for finding in findings
        if isinstance(finding, Mapping)
    ]


def _suppressed_summary(bucketed: BucketedFindings) -> dict[str, Any]:
    """把 suppressed 候选折叠为报告 schema 要求的计数与固定原因摘要。"""

    return {
        "count": len(bucketed.suppressed),
        "reasons": {"low_confidence": len(bucketed.suppressed)}
        if bucketed.suppressed
        else {},
    }


def _final_conclusion(bucketed: BucketedFindings) -> dict[str, Any]:
    """仅从已脱敏、去重后的四桶结果生成确定性结论与建议。"""

    recommendations: list[str] = []
    for finding in bucketed.findings:
        recommendation = finding["recommendation"]
        if recommendation not in recommendations:
            recommendations.append(recommendation)
    if bucketed.findings:
        summary = f"发现 {len(bucketed.findings)} 条需要处理的正式问题。"
    elif bucketed.needs_human_review:
        summary = "未发现高置信正式问题，但存在需要人工复核的候选。"
    else:
        summary = "未发现需要处理的正式问题。"
    return {
        "summary": summary,
        "recommendations": recommendations,
    }


class ReviewPipeline:
    """编排输入、治理、沙箱、后处理、持久化与报告的唯一检测链路。"""

    def __init__(
        self,
        *,
        store: ReviewStore,
        governance: GovernancePort,
        sandbox: SandboxPort,
        output_dir: Path,
        config: ReviewConfig | None = None,
        report_writer: CanonicalReportWriter | None = None,
        input_loader: InputLoader = load_input,
        task_id_factory: TaskIdFactory = _new_task_id,
        model_mode: str = "off",
        llm_enhancer: LlmEnhancer | None = None,
        model_environment: Mapping[str, str] | None = None,
    ) -> None:
        """注入持久化、隔离和可选文本增强端口；检测规则始终保持唯一。"""

        if model_mode not in {"off", "fake", "real"}:
            raise ValueError("model_mode_invalid")
        if llm_enhancer is not None and llm_enhancer.mode != model_mode:
            raise ValueError("model_mode_and_enhancer_mismatch")
        runtime_type = getattr(sandbox, "runtime_type", None)
        if runtime_type not in _RUNTIME_TYPES:
            raise ValueError("sandbox runtime_type is invalid")
        self._store = store
        self._governance = governance
        self._sandbox = sandbox
        self._output_dir = Path(output_dir)
        self._config = ReviewConfig() if config is None else config
        self._report_writer = report_writer or CanonicalReportWriter()
        self._input_loader = input_loader
        self._task_id_factory = task_id_factory
        self._llm_enhancer = llm_enhancer or LlmEnhancer(
            mode=model_mode,
            environ=model_environment,
        )

    def run(
        self,
        *,
        entrypoint_tool_call_count: int = 0,
        trace: TraceSink | None = None,
        **input_options: Any,
    ) -> PipelineResult:
        """执行八阶段评审，并把入口已发生的受控工具调用计入 canonical 审计指标。"""

        task_id = self._task_id_factory()
        if not isinstance(task_id, str) or not task_id.strip():
            raise PipelineFatalError("pipeline_task_id_invalid")
        input_type = _source_input_type(input_options)
        metrics = MetricsCollector(task_id=task_id, runtime_type=self._sandbox.runtime_type)
        metrics.record_tool_call(entrypoint_tool_call_count)
        emit_trace(
            trace,
            "pipeline.started",
            input_type=input_type,
            runtime_type=self._sandbox.runtime_type,
        )
        _LOGGER.info("Pipeline started: input_type=%s runtime=%s", input_type, self._sandbox.runtime_type)
        warnings: list[dict[str, str]] = []
        filter_events: list[dict[str, Any]] = []
        sandbox_runs: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        change_set: "ChangeSet" | None = None

        self._store.initialize()
        self._store.create_task(
            {
                "id": task_id,
                "status": "running",
                "input_type": input_type,
                "input_ref": input_type,
                "diff_summary": {},
                "config": self._config.to_dict(),
            }
        )

        try:
            parse_started = perf_counter()
            try:
                input_result = self._input_loader(
                    config=self._config,
                    **input_options,
                )
            except InputValidationError as exc:
                self._store.update_task(
                    task_id,
                    status="failed",
                    error_type="input_validation_error",
                    error_message=_safe_code(exc.args[0] if exc.args else None, fallback="input_unavailable"),
                )
                raise PipelineFatalError("pipeline_input_unavailable") from exc
            change_set = input_result.change_set
            emit_trace(
                trace,
                "pipeline.input_loaded",
                source_kind=change_set.source_kind,
            )
            _LOGGER.info(
                "Input loaded: source=%s files=%s hunks=%s changed_lines=%s",
                change_set.source_kind,
                change_set.file_count,
                change_set.hunk_count,
                change_set.additions,
            )
            metrics.record_stage_duration(
                "parse",
                (perf_counter() - parse_started) * 1000,
            )
            self._store.update_task(
                task_id,
                input_ref=change_set.source_kind,
                diff_summary=_diff_summary(change_set),
            )
            warnings.extend(
                _warning(code, stage="parse") for code in input_result.warnings
            )

            decision = self._governance.decide(
                task_id=task_id,
                change_set=change_set,
                config=self._config,
            )
            action = _safe_code(decision.get("action"), fallback="deny")
            if action not in _FILTER_ACTIONS:
                action = "deny"
                warnings.append(_warning("invalid_filter_action", stage="governance"))
            metrics.record_filter_action(action)
            emit_trace(trace, "pipeline.filter_decision", action=action)
            _LOGGER.info("Filter decision: action=%s", action.upper())
            raw_events = decision.get("events", ())
            if isinstance(raw_events, (list, tuple)):
                filter_events.extend(
                    _sanitize_filter_event(event)
                    for event in raw_events
                    if isinstance(event, Mapping)
                )
            warnings.extend(
                _warning(code, stage="governance")
                for code in decision.get("warnings", ())
                if isinstance(code, str)
            )

            if action == "allow":
                sandbox_started = perf_counter()
                emit_trace(
                    trace,
                    "pipeline.sandbox_started",
                    runtime_type=self._sandbox.runtime_type,
                )
                _LOGGER.info("Sandbox started: runtime=%s", self._sandbox.runtime_type)
                raw_sandbox_result = self._sandbox.execute(
                    task_id=task_id,
                    change_set=change_set,
                    config=self._config,
                )
                sandbox_run = _sanitize_sandbox_run(raw_sandbox_result)
                if sandbox_run["duration_ms"] == 0:
                    sandbox_run["duration_ms"] = max(
                        int((perf_counter() - sandbox_started) * 1000),
                        0,
                    )
                sandbox_runs.append(sandbox_run)
                emit_trace(
                    trace,
                    "pipeline.sandbox_finished",
                    status=sandbox_run["status"],
                    candidate_count=len(_safe_candidates(raw_sandbox_result)),
                    timed_out=sandbox_run["timed_out"],
                    truncated=sandbox_run["truncated"],
                )
                _LOGGER.info(
                    "Sandbox finished: status=%s duration_ms=%s timed_out=%s truncated=%s",
                    sandbox_run["status"],
                    sandbox_run["duration_ms"],
                    sandbox_run["timed_out"],
                    sandbox_run["truncated"],
                )
                metrics.record_sandbox_run(sandbox_run["duration_ms"])
                if sandbox_run["error_type"]:
                    metrics.record_error(sandbox_run["error_type"])
                candidates.extend(_safe_candidates(raw_sandbox_result))
                if sandbox_run["status"] != "ok":
                    warnings.append(
                        _warning(
                            f"sandbox_{sandbox_run['status']}",
                            stage="sandbox",
                        )
                    )
                if sandbox_run["timed_out"]:
                    warnings.append(_warning("sandbox_timeout", stage="sandbox"))
                if sandbox_run["truncated"]:
                    warnings.append(_warning("sandbox_output_truncated", stage="sandbox"))
            else:
                warnings.append(_warning(f"filter_{action}", stage="governance"))
        except PipelineFatalError:
            raise
        except Exception as exc:
            warnings.append(
                _warning(_safe_code(type(exc).__name__, fallback="pipeline_error"), stage="sandbox")
            )
        finally:
            try:
                self._sandbox.cleanup(task_id=task_id)
            except Exception:
                warnings.append(_warning("workspace_cleanup_error", stage="cleanup"))

        if change_set is None:
            raise PipelineFatalError("pipeline_change_set_missing")

        try:
            bucketed = route_findings(candidates, warnings=warnings)
        except ValueError as exc:
            self._store.update_task(
                task_id,
                status="failed",
                error_type="postprocess_error",
                error_message="postprocess_error",
            )
            raise PipelineFatalError("pipeline_postprocess_failed") from exc

        metrics.record_findings(
            findings=bucketed.findings,
            needs_human_review=bucketed.needs_human_review,
            suppressed_count=len(bucketed.suppressed),
            warnings=bucketed.warnings,
        )
        status = "completed_with_warnings" if bucketed.warnings else "completed"
        report = {
            "schema_version": self._config.schema_version,
            "rule_pack_version": self._config.rule_pack_version,
            "config_digest": self._config.config_digest,
            "input_sha256": change_set.input_sha256,
            "task_id": task_id,
            "status": status,
            "input_summary": _input_summary(change_set),
            "findings": list(bucketed.findings),
            "needs_human_review": list(bucketed.needs_human_review),
            "warnings": list(bucketed.warnings),
            "suppressed": _suppressed_summary(bucketed),
            "filter_summary": {
                "allow_count": sum(
                    event["action"] == "allow" for event in filter_events
                ),
                "deny_count": sum(
                    event["action"] == "deny" for event in filter_events
                ),
                "needs_human_review_count": sum(
                    event["action"] == "needs_human_review"
                    for event in filter_events
                ),
                "events": filter_events,
            },
            "sandbox_summary": {
                "runtime_type": self._sandbox.runtime_type,
                "run_count": len(sandbox_runs),
                "runs": sandbox_runs,
            },
            "metrics": metrics.snapshot().to_dict(),
            "final_conclusion": _final_conclusion(bucketed),
        }
        if self._llm_enhancer.mode != "off":
            llm_started = perf_counter()
            try:
                report = self._llm_enhancer.enhance(report)
            except Exception:
                report["warnings"] = [
                    *report["warnings"],
                    _warning("llm_enhancement_failed", stage="llm"),
                ]
                status = "completed_with_warnings"
                report["status"] = status
                metrics.record_warning()
                metrics.record_error("llm_enhancement_failed")
            llm_duration_ms = (perf_counter() - llm_started) * 1000
            metrics.record_stage_duration("llm", llm_duration_ms)
            report["metrics"] = metrics.snapshot().to_dict()
            metrics.emit_span(
                "llm",
                status=report["status"],
                duration_ms=llm_duration_ms,
                error_type="llm_enhancement_failed"
                if report["status"] == "completed_with_warnings"
                else None,
            )

        try:
            canonical = self._report_writer.validate(report)
            persistence_payload = {
                "events": filter_events,
                "runs": sandbox_runs,
                "findings": [
                    *canonical["findings"],
                    *canonical["needs_human_review"],
                ],
                "report": canonical,
            }
            if contains_plaintext_secret(persistence_payload):
                raise ReportValidationError("pipeline outbound payload contains a plaintext secret")

            for event in filter_events:
                self._store.add_filter_event(task_id, event)
            for sandbox_run in sandbox_runs:
                self._store.add_sandbox_run(task_id, sandbox_run)
            for finding in persistence_payload["findings"]:
                self._store.add_finding(task_id, finding)
            self._store.save_report(task_id, self._report_writer.to_store_payload(canonical))
            written = self._report_writer.write(canonical, self._output_dir)
            self._store.update_task(task_id, status=status)
            emit_trace(
                trace,
                "pipeline.report_persisted",
                status=status,
                finding_count=len(canonical["findings"]),
                needs_human_review_count=len(canonical["needs_human_review"]),
                warning_count=len(canonical["warnings"]),
            )
            _LOGGER.info(
                "Canonical report persisted: findings=%s warnings=%s needs_human_review=%s",
                len(canonical["findings"]),
                len(canonical["warnings"]),
                len(canonical["needs_human_review"]),
            )
        except (
            ReportValidationError,
            ReportWriteError,
            OSError,
            RuntimeError,
            SQLAlchemyError,
            ValueError,
            KeyError,
        ) as exc:
            self._store.update_task(
                task_id,
                status="failed",
                error_type="report_or_persistence_error",
                error_message="report_or_persistence_error",
            )
            raise PipelineFatalError("pipeline_report_or_persistence_failed") from exc

        metrics.emit_span(
            "total",
            status=status,
            duration_ms=metrics.snapshot().total_duration_ms,
        )
        return PipelineResult(
            task_id=task_id,
            status=status,
            report=written.report,
            json_path=written.json_path,
            markdown_path=written.markdown_path,
        )


__all__ = [
    "GovernancePort",
    "PipelineFatalError",
    "PipelineResult",
    "ReviewPipeline",
    "SandboxPort",
]
