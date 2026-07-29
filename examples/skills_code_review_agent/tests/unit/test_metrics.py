#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Public-contract tests for review metrics and telemetry boundaries."""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from code_review.metrics import (  # noqa: E402
    TELEMETRY_ATTRIBUTE_ALLOWLIST,
    MetricsCollector,
)


class _Clock:
    """按预置顺序返回单调时间，供指标测试稳定控制耗时。"""

    def __init__(self, *values: float) -> None:
        """保存后续调用依次返回的秒级时间值。"""

        self._values = iter(values)

    def __call__(self) -> float:
        """返回下一个预置时间值。"""

        return next(self._values)


class _RecordingSpan:
    """记录写入属性的最小 span 替身。"""

    def __init__(self) -> None:
        """初始化空属性字典。"""

        self.attributes: dict[str, object] = {}

    def set_attribute(self, key: str, value: object) -> None:
        """记录一次 span 属性写入。"""

        self.attributes[key] = value


class _SpanContext:
    """把记录 span 暴露为上下文管理器。"""

    def __init__(self, span: _RecordingSpan) -> None:
        """保存本上下文应返回的 span。"""

        self._span = span

    def __enter__(self) -> _RecordingSpan:
        """返回用于记录属性的 span。"""

        return self._span

    def __exit__(self, *_args: object) -> None:
        """在退出时不抑制异常。"""

        return None


class _SpanFactory:
    """记录 span 名称并返回可检查的上下文。"""

    def __init__(self) -> None:
        """初始化名称和 span 收集容器。"""

        self.names: list[str] = []
        self.spans: list[_RecordingSpan] = []

    def __call__(self, name: str) -> _SpanContext:
        """创建并保存一个与名称对应的记录 span。"""

        self.names.append(name)
        span = _RecordingSpan()
        self.spans.append(span)
        return _SpanContext(span)


def test_snapshot_freezes_review_metrics_and_distributions() -> None:
    """验证冻结快照保留全部 B3 指标且不受后续记录影响。"""

    collector = MetricsCollector(
        task_id="review-123",
        runtime_type="container",
        clock=_Clock(10.0, 10.125, 10.250),
    )
    collector.record_stage_duration("parse", 7)
    collector.record_stage_duration("llm", 12)
    collector.record_stage_duration("postprocess", 3)
    collector.record_tool_call(2)
    collector.record_sandbox_run(35)
    collector.record_filter_action("deny")
    collector.record_filter_action("needs_human_review")
    collector.record_findings(
        findings=(
            {"severity": "critical", "category": "security"},
            {"severity": "high", "category": "resource-leak"},
        ),
        needs_human_review=(
            {"severity": "medium", "category": "security"},
        ),
        suppressed_count=2,
        warnings=("sandbox_timeout",),
    )
    collector.record_error("sandbox_timeout")

    snapshot = collector.snapshot()
    collector.record_tool_call()

    assert snapshot.to_dict() == {
        "total_duration_ms": 125,
        "sandbox_duration_ms": 35,
        "llm_duration_ms": 12,
        "tool_call_count": 2,
        "sandbox_run_count": 1,
        "filter_block_count": 1,
        "filter_review_count": 1,
        "finding_count": 2,
        "warning_count": 1,
        "needs_human_review_count": 1,
        "suppressed_count": 2,
        "severity_distribution": {
            "critical": 1,
            "high": 1,
            "medium": 1,
        },
        "category_distribution": {
            "resource-leak": 1,
            "security": 2,
        },
        "error_type_distribution": {"sandbox_timeout": 1},
        "runtime_type": "container",
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "platform": platform.system(),
    }
    assert snapshot.tool_call_count == 2
    with pytest.raises(TypeError):
        snapshot.severity_distribution["critical"] = 2


def test_telemetry_spans_use_only_safe_allowlisted_attributes() -> None:
    """验证 span 只接收任务元数据、计数和枚举，且敏感值不外泄。"""

    secret = "ghp_" + "a" * 36
    absolute_path = r"C:\Users\reviewer\secret.diff"
    factory = _SpanFactory()
    collector = MetricsCollector(
        task_id=f"{secret}:{absolute_path}",
        runtime_type="container",
        clock=_Clock(1.0, 1.010),
        span_factory=factory,
    )
    collector.record_tool_call(2)
    collector.record_sandbox_run(30)
    collector.record_filter_action("deny")
    collector.record_findings(
        findings=(
            {"severity": "high", "category": "security"},
        ),
        needs_human_review=(),
        suppressed_count=0,
        warnings=("sandbox_timeout",),
    )

    emitted = collector.emit_span(
        "sandbox",
        status="completed_with_warnings",
        duration_ms=30,
        error_type="sandbox_timeout",
    )

    attributes = factory.spans[0].attributes
    serialized = json.dumps(attributes, ensure_ascii=False, sort_keys=True)
    assert emitted is True
    assert factory.names == ["code_review.sandbox"]
    assert set(attributes).issubset(TELEMETRY_ATTRIBUTE_ALLOWLIST)
    assert attributes["code_review.task_id"] == "redacted_task_id"
    assert attributes["code_review.sandbox_run_count"] == 1
    assert attributes["code_review.finding_count"] == 1
    assert secret not in serialized
    assert absolute_path not in serialized


def test_telemetry_supports_all_required_review_stages() -> None:
    """验证 total 与四个关键子阶段均能生成固定命名的 span。"""

    factory = _SpanFactory()
    collector = MetricsCollector(
        task_id="review-123",
        runtime_type="container",
        clock=_Clock(1.0, 1.001, 1.002, 1.003, 1.004, 1.005),
        span_factory=factory,
    )

    for stage in ("total", "parse", "sandbox", "postprocess", "llm"):
        assert collector.emit_span(
            stage,
            status="completed",
            duration_ms=1,
        ) is True

    assert factory.names == [
        "code_review.total",
        "code_review.parse",
        "code_review.sandbox",
        "code_review.postprocess",
        "code_review.llm",
    ]


def test_unavailable_telemetry_is_a_zero_side_effect() -> None:
    """验证 span 工厂不可用时评审指标仍可正常冻结。"""

    def unavailable_span_factory(_name: str) -> _SpanContext:
        """模拟没有 OTel 可用时的 span 创建失败。"""

        raise ImportError("telemetry_unavailable")

    collector = MetricsCollector(
        task_id="review-123",
        runtime_type="local",
        clock=_Clock(1.0, 1.002, 1.002),
        span_factory=unavailable_span_factory,
    )

    assert collector.emit_span(
        "parse",
        status="completed",
        duration_ms=2,
    ) is False
    assert collector.snapshot().total_duration_ms == 2
