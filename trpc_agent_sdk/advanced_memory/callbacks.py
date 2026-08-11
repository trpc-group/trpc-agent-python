"""Shared callback installation and stage ordering for Advanced Memory."""

from __future__ import annotations

from typing import Any

from .runtime import AdvancedMemoryRuntime


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
    stage = getattr(callback, "advanced_memory_stage")
    insertion_index = next(
        (index for index, item in enumerate(callbacks) if getattr(item, "advanced_memory_stage", 0) > stage),
        len(callbacks),
    )
    agent.before_model_callback = [
        *callbacks[:insertion_index],
        callback,
        *callbacks[insertion_index:],
    ]
    return None
