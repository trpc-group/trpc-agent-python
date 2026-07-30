# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""占位符归一化:消除 timestamp / 自动 id / invocation_id / 序列化顺序等非业务差异。

保留字段存在性(用占位符替换,而非 pop 删除),剥离 ``temp:`` 临时状态,
memory 结果按确定性键排序并把 entry.timestamp 归一化。
"""

from __future__ import annotations

import json
from typing import Any

from .harness import NORMALIZED
from .harness import ReplaySnapshot

# Event 顶层非业务字段(由后端/session 自动分配,跨后端必然不同)。
VOLATILE_KEYS = ("id", "timestamp", "invocation_id")


def normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    """替换事件顶层非业务字段为占位符,保留键的存在性。"""
    out = dict(event)
    for key in VOLATILE_KEYS:
        if key in out:
            out[key] = NORMALIZED
    # long_running_tool_ids: InMemory=None vs SQL=set() 的良性序列化差异,统一空值;
    # 一方有值一方空的真丢失仍会被检出。
    lr = out.get("long_running_tool_ids")
    if lr is None or (hasattr(lr, "__len__") and len(lr) == 0):
        out["long_running_tool_ids"] = None
    return out


def _normalize_memory_entries(value: Any) -> Any:
    """memory 检索结果:先归一化 entry.timestamp(来自 event,跨后端不同),再确定性排序。"""
    if not isinstance(value, list):
        return value
    cleaned: list[Any] = []
    for entry in value:
        if isinstance(entry, dict):
            entry = dict(entry)
            if "timestamp" in entry:
                entry["timestamp"] = NORMALIZED
        cleaned.append(entry)
    return sorted(cleaned, key=lambda i: json.dumps(i, sort_keys=True, ensure_ascii=True))


def normalize_snapshot(snapshot: ReplaySnapshot) -> ReplaySnapshot:
    """返回归一化后的快照副本(不改原对象)。"""
    out = snapshot.model_copy(deep=True)
    out.events = [normalize_event(e) for e in out.events]
    out.historical_events = [normalize_event(e) for e in out.historical_events]
    # 剥离 temp: 临时状态(不持久化,比较时排除)。
    out.state = {k: v for k, v in out.state.items() if not k.startswith("temp:")}
    # memory 检索结果:归一化 timestamp + 确定性排序。
    out.memory = {k: _normalize_memory_entries(v) for k, v in out.memory.items()}
    return out
