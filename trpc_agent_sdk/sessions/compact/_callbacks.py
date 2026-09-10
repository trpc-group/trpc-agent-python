# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared callback installation and stage ordering for Advanced Memory."""

from __future__ import annotations

from typing import Any

from ._runtime import AdvancedMemoryRuntime


def install_staged_callback(
    agent: Any,
    callback: Any,
    *,
    callback_type: type,
    component_attribute: str,
    memory_runtime: AdvancedMemoryRuntime,
    conflict_message: str,
) -> Any | None:
    """Install a staged callback idempotently and validate runtime ownership."""
    existing = agent.before_model_callback
    callbacks = existing if isinstance(existing, list) else ([existing] if existing else [])
    for item in callbacks:
        if not isinstance(item, callback_type):
            continue
        component = getattr(item, component_attribute)
        if component.runtime is not memory_runtime:
            raise ValueError(conflict_message)
        return component
    stage = getattr(callback, "advanced_memory_stage", None)
    if not isinstance(stage, int):
        raise TypeError("advanced_memory_stage must be an integer")

    def get_stage(item: Any) -> int:
        item_stage = getattr(item, "advanced_memory_stage", 0)
        return item_stage if isinstance(item_stage, int) else 0

    insertion_index = next(
        (index for index, item in enumerate(callbacks) if get_stage(item) > stage),
        len(callbacks),
    )
    agent.before_model_callback = [
        *callbacks[:insertion_index],
        callback,
        *callbacks[insertion_index:],
    ]
    return None
