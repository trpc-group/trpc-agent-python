#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""自动代码评审的指标汇总与安全 Telemetry 边界。"""

from __future__ import annotations

from contextlib import nullcontext
import platform
import re
import sys
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, ContextManager, Iterable, Mapping


_RUNTIME_TYPES = frozenset({"container", "cube", "fake", "local", "unknown"})
_DURATION_STAGES = frozenset({"parse", "postprocess", "sandbox", "llm"})
_TELEMETRY_STAGES = frozenset(
    {"total", "parse", "sandbox", "postprocess", "llm"}
)
_SEVERITIES = frozenset({"critical", "high", "medium", "low", "info"})
_SAFE_ENUM = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_TASK_STATUSES = frozenset(
    {"completed", "completed_with_warnings", "failed", "running"}
)

TELEMETRY_ATTRIBUTE_ALLOWLIST = frozenset(
    {
        "code_review.duration_ms",
        "code_review.error_type",
        "code_review.filter_block_count",
        "code_review.filter_review_count",
        "code_review.finding_count",
        "code_review.llm_duration_ms",
        "code_review.needs_human_review_count",
        "code_review.runtime_type",
        "code_review.sandbox_duration_ms",
        "code_review.sandbox_run_count",
        "code_review.stage",
        "code_review.status",
        "code_review.suppressed_count",
        "code_review.task_id",
        "code_review.tool_call_count",
        "code_review.total_duration_ms",
        "code_review.warning_count",
    }
)


def _non_negative_count(value: int, name: str) -> int:
    """验证并返回一个非负整数计数。"""

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _duration_ms(value: float | int, name: str) -> int:
    """验证并规范化一个非负毫秒耗时。"""

    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{name} must be a non-negative duration")
    return int(round(value))


def _safe_enum(value: str, name: str) -> str:
    """验证仅可安全记录的枚举值，拒绝路径和自由文本。"""

    if not isinstance(value, str) or not _SAFE_ENUM.fullmatch(value):
        raise ValueError(f"{name} must be a safe enum value")
    return value


def _freeze_distribution(values: Mapping[str, int]) -> Mapping[str, int]:
    """按稳定顺序冻结一份整数分布映射。"""

    return MappingProxyType(dict(sorted(values.items())))


def _sdk_span_factory(name: str) -> ContextManager[Any]:
    """延迟获取 SDK tracer；未安装 OTel 时返回无副作用上下文。"""

    try:
        from trpc_agent_sdk.telemetry import tracer
    except ImportError:
        return nullcontext(None)
    return tracer.start_as_current_span(name)


@dataclass(frozen=True)
class MetricsSnapshot:
    """报告冻结时生成的不可变监控指标快照。"""

    total_duration_ms: int
    sandbox_duration_ms: int
    llm_duration_ms: int
    tool_call_count: int
    sandbox_run_count: int
    filter_block_count: int
    filter_review_count: int
    finding_count: int
    warning_count: int
    needs_human_review_count: int
    suppressed_count: int
    severity_distribution: Mapping[str, int]
    category_distribution: Mapping[str, int]
    error_type_distribution: Mapping[str, int]
    runtime_type: str
    python_version: str
    platform: str

    def to_dict(self) -> dict[str, object]:
        """返回可安全落库和 JSON 序列化的独立快照副本。"""

        return {
            "total_duration_ms": self.total_duration_ms,
            "sandbox_duration_ms": self.sandbox_duration_ms,
            "llm_duration_ms": self.llm_duration_ms,
            "tool_call_count": self.tool_call_count,
            "sandbox_run_count": self.sandbox_run_count,
            "filter_block_count": self.filter_block_count,
            "filter_review_count": self.filter_review_count,
            "finding_count": self.finding_count,
            "warning_count": self.warning_count,
            "needs_human_review_count": self.needs_human_review_count,
            "suppressed_count": self.suppressed_count,
            "severity_distribution": dict(self.severity_distribution),
            "category_distribution": dict(self.category_distribution),
            "error_type_distribution": dict(self.error_type_distribution),
            "runtime_type": self.runtime_type,
            "python_version": self.python_version,
            "platform": self.platform,
        }


class MetricsCollector:
    """聚合单次评审的计数、耗时和安全枚举指标。"""

    def __init__(
        self,
        *,
        task_id: str,
        runtime_type: str,
        clock: Callable[[], float] = time.perf_counter,
        span_factory: Callable[[str], ContextManager[Any]] = _sdk_span_factory,
    ) -> None:
        """初始化一次评审的指标状态、单调起始时间和 span 工厂。"""

        self._task_id = str(task_id)
        self._runtime_type = self._validate_runtime_type(runtime_type)
        self._clock = clock
        self._span_factory = span_factory
        self._started_at = clock()
        self._stage_durations: dict[str, int] = {
            stage: 0 for stage in _DURATION_STAGES
        }
        self._tool_call_count = 0
        self._sandbox_run_count = 0
        self._filter_block_count = 0
        self._filter_review_count = 0
        self._finding_count = 0
        self._warning_count = 0
        self._needs_human_review_count = 0
        self._suppressed_count = 0
        self._severity_distribution: dict[str, int] = {}
        self._category_distribution: dict[str, int] = {}
        self._error_type_distribution: dict[str, int] = {}

    def record_stage_duration(self, stage: str, duration_ms: float | int) -> None:
        """累计一个受支持非沙箱阶段的耗时。"""

        if stage not in _DURATION_STAGES:
            raise ValueError("stage is not supported")
        self._stage_durations[stage] += _duration_ms(
            duration_ms,
            "duration_ms",
        )

    def record_tool_call(self, count: int = 1) -> None:
        """累计已经发生的工具调用次数。"""

        self._tool_call_count += _non_negative_count(count, "count")

    def record_sandbox_run(self, duration_ms: float | int) -> None:
        """累计一次沙箱运行及其耗时。"""

        self._sandbox_run_count += 1
        self._stage_durations["sandbox"] += _duration_ms(
            duration_ms,
            "duration_ms",
        )

    def record_filter_action(self, action: str) -> None:
        """按 Filter 决策累计拦截或人工复核次数。"""

        normalized = _safe_enum(action.lower(), "action")
        if normalized == "deny":
            self._filter_block_count += 1
        elif normalized == "needs_human_review":
            self._filter_review_count += 1

    def record_findings(
        self,
        *,
        findings: Iterable[Mapping[str, str]],
        needs_human_review: Iterable[Mapping[str, str]],
        suppressed_count: int,
        warnings: Iterable[object],
    ) -> None:
        """累计四桶数量及正式/人工复核候选的严重级别和类别。"""

        for finding in findings:
            self._record_finding(finding)
            self._finding_count += 1
        for finding in needs_human_review:
            self._record_finding(finding)
            self._needs_human_review_count += 1
        self._suppressed_count += _non_negative_count(
            suppressed_count,
            "suppressed_count",
        )
        self._warning_count += sum(1 for _ in warnings)

    def record_error(self, error_type: str) -> None:
        """累计一个允许写入 Telemetry 的枚举型错误代码。"""

        normalized = _safe_enum(error_type, "error_type")
        self._error_type_distribution[normalized] = (
            self._error_type_distribution.get(normalized, 0) + 1
        )

    def record_warning(self, count: int = 1) -> None:
        """累计后处理阶段新增的安全 warning，避免重放 finding 统计。"""

        self._warning_count += _non_negative_count(count, "count")

    def emit_span(
        self,
        stage: str,
        *,
        status: str,
        duration_ms: float | int,
        error_type: str | None = None,
    ) -> bool:
        """使用白名单属性写入一个阶段 span，失败时保持零副作用。"""

        if stage not in _TELEMETRY_STAGES:
            raise ValueError("stage is not supported")
        if status not in _TASK_STATUSES:
            raise ValueError("status is invalid")
        normalized_error = (
            _safe_enum(error_type, "error_type")
            if error_type is not None
            else ""
        )
        attributes = self._span_attributes(
            stage=stage,
            status=status,
            duration_ms=_duration_ms(duration_ms, "duration_ms"),
            error_type=normalized_error,
        )
        try:
            with self._span_factory(f"code_review.{stage}") as span:
                if span is None:
                    return False
                for key in sorted(attributes):
                    span.set_attribute(key, attributes[key])
        except Exception:
            return False
        return True

    def snapshot(self) -> MetricsSnapshot:
        """冻结当前指标并返回不可受后续记录影响的快照。"""

        elapsed_ms = _duration_ms(
            max(self._clock() - self._started_at, 0) * 1000,
            "total_duration_ms",
        )
        return MetricsSnapshot(
            total_duration_ms=elapsed_ms,
            sandbox_duration_ms=self._stage_durations["sandbox"],
            llm_duration_ms=self._stage_durations["llm"],
            tool_call_count=self._tool_call_count,
            sandbox_run_count=self._sandbox_run_count,
            filter_block_count=self._filter_block_count,
            filter_review_count=self._filter_review_count,
            finding_count=self._finding_count,
            warning_count=self._warning_count,
            needs_human_review_count=self._needs_human_review_count,
            suppressed_count=self._suppressed_count,
            severity_distribution=_freeze_distribution(
                self._severity_distribution,
            ),
            category_distribution=_freeze_distribution(
                self._category_distribution,
            ),
            error_type_distribution=_freeze_distribution(
                self._error_type_distribution,
            ),
            runtime_type=self._runtime_type,
            python_version=f"{sys.version_info.major}.{sys.version_info.minor}",
            platform=platform.system(),
        )

    def _span_attributes(
        self,
        *,
        stage: str,
        status: str,
        duration_ms: int,
        error_type: str,
    ) -> dict[str, object]:
        """从冻结快照构造唯一允许写入 span 的属性集合。"""

        snapshot = self.snapshot()
        attributes = {
            "code_review.duration_ms": duration_ms,
            "code_review.error_type": error_type,
            "code_review.filter_block_count": snapshot.filter_block_count,
            "code_review.filter_review_count": snapshot.filter_review_count,
            "code_review.finding_count": snapshot.finding_count,
            "code_review.llm_duration_ms": snapshot.llm_duration_ms,
            "code_review.needs_human_review_count": (
                snapshot.needs_human_review_count
            ),
            "code_review.runtime_type": snapshot.runtime_type,
            "code_review.sandbox_duration_ms": snapshot.sandbox_duration_ms,
            "code_review.sandbox_run_count": snapshot.sandbox_run_count,
            "code_review.stage": stage,
            "code_review.status": status,
            "code_review.suppressed_count": snapshot.suppressed_count,
            "code_review.task_id": self._telemetry_task_id(),
            "code_review.tool_call_count": snapshot.tool_call_count,
            "code_review.total_duration_ms": snapshot.total_duration_ms,
            "code_review.warning_count": snapshot.warning_count,
        }
        return {
            key: value
            for key, value in attributes.items()
            if key in TELEMETRY_ATTRIBUTE_ALLOWLIST
        }

    def _telemetry_task_id(self) -> str:
        """返回可安全写入 Telemetry 的任务 ID 或固定脱敏占位符。"""

        if _SAFE_TASK_ID.fullmatch(self._task_id):
            return self._task_id
        return "redacted_task_id"

    def _record_finding(self, finding: Mapping[str, str]) -> None:
        """验证候选的公开枚举字段并更新对应分布。"""

        severity = finding.get("severity")
        category = finding.get("category")
        if severity not in _SEVERITIES:
            raise ValueError("finding severity is invalid")
        normalized_category = _safe_enum(category, "finding category")
        self._severity_distribution[severity] = (
            self._severity_distribution.get(severity, 0) + 1
        )
        self._category_distribution[normalized_category] = (
            self._category_distribution.get(normalized_category, 0) + 1
        )

    @staticmethod
    def _validate_runtime_type(runtime_type: str) -> str:
        """验证锁定 runtime 枚举，避免环境信息进入指标。"""

        if runtime_type not in _RUNTIME_TYPES:
            raise ValueError("runtime_type is invalid")
        return runtime_type


__all__ = [
    "MetricsCollector",
    "MetricsSnapshot",
    "TELEMETRY_ATTRIBUTE_ALLOWLIST",
]
