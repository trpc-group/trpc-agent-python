#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""脱敏 trace 公共出口的防御性单元测试。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from code_review.trace import emit_trace


def test_emit_trace_keeps_only_allowlisted_safe_fields() -> None:
    """验证 trace 只输出白名单字段和安全值，不携带路径或请求内容。"""

    received: list[tuple[str, Mapping[str, object]]] = []

    def sink(event: str, details: Mapping[str, object]) -> None:
        """收集已脱敏事件，作为公共 trace 出口的观察值。"""

        received.append((event, details))

    emit_trace(
        sink,
        "pipeline.sandbox_finished",
        status="completed",
        candidate_count=2,
        timed_out=False,
        file=r"E:\private\service.py",
        query="please review this source",
        warning_count=-1,
        finding_count=1_000_001,
        source_kind=["fixture"],
    )

    assert received == [
        (
            "pipeline.sandbox_finished",
            {
                "status": "completed",
                "candidate_count": 2,
                "timed_out": False,
            },
        )
    ]


def test_emit_trace_rejects_invalid_event_names() -> None:
    """验证非法事件名不会触发 sink，也不会产生部分事件。"""

    received: list[str] = []

    def sink(event: str, details: Mapping[str, object]) -> None:
        """记录可能出现的事件名，详情在本断言中无需使用。"""

        del details
        received.append(event)

    emit_trace(sink, "pipeline;unsafe", status="completed")
    emit_trace(sink, "Pipeline.Started", status="completed")
    emit_trace(sink, "pipeline", status="completed")

    assert received == []


def test_emit_trace_never_propagates_sink_failures() -> None:
    """验证终端 trace sink 故障不会中断评审主流程。"""

    def failing_sink(event: str, details: Mapping[str, object]) -> None:
        """模拟日志输出端故障，并确保异常被 trace 边界吸收。"""

        del event, details
        raise RuntimeError("terminal unavailable")

    emit_trace(failing_sink, "review.completed", status="completed")
