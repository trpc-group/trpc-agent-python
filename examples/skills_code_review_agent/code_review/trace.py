#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""用于终端流式可观测性的脱敏 trace 边界。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import re


TraceSink = Callable[[str, Mapping[str, object]], None]

_EVENT_PATTERN = re.compile(r"^[a-z]+(?:[._][a-z]+)+$")
_SAFE_STRING_PATTERN = re.compile(r"^[a-z0-9_.:-]{1,80}$")
_SAFE_FIELDS = frozenset(
    {
        "action",
        "candidate_count",
        "entrypoint",
        "finding_count",
        "input_type",
        "model_mode",
        "needs_human_review_count",
        "runtime_type",
        "source_kind",
        "status",
        "timed_out",
        "tool",
        "truncated",
        "warning_count",
    }
)


def emit_trace(sink: TraceSink | None, event: str, **details: object) -> None:
    """向可选终端 sink 发出白名单事件，任何 trace 故障都不得影响评审。"""

    if sink is None or not _EVENT_PATTERN.fullmatch(event):
        return
    sanitized = {
        name: value
        for name, value in details.items()
        if name in _SAFE_FIELDS and _safe_trace_value(value)
    }
    try:
        sink(event, sanitized)
    except Exception:
        return


def _safe_trace_value(value: object) -> bool:
    """只允许固定枚举、计数和布尔值进入流式 trace。"""

    if isinstance(value, bool):
        return True
    if isinstance(value, int):
        return 0 <= value <= 1_000_000
    if isinstance(value, str):
        return bool(_SAFE_STRING_PATTERN.fullmatch(value))
    return False


__all__ = ["TraceSink", "emit_trace"]
