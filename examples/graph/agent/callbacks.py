# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Node callbacks for execution tracking and debug logging."""
from datetime import datetime
from typing import Any
from typing import Optional
from typing import Protocol

from trpc_agent_sdk.dsl.graph import NodeCallbacks
from trpc_agent_sdk.dsl.graph import STATE_KEY_LAST_RESPONSE


class CallbackContext(Protocol):
    node_id: str
    node_name: str
    node_type: str
    step_number: int
    execution_start_time: Optional[datetime]
    invocation_id: str
    session_id: str


def _truncate_text(value: str, max_len: int = 120) -> str:
    if len(value) <= max_len:
        return value
    return value[:max_len - 3] + "..."


def _extract_event_text(event: Any) -> str:
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None)
    if not parts:
        return ""

    text_parts = []
    for part in parts:
        text = getattr(part, "text", "")
        if isinstance(text, str) and text:
            text_parts.append(text)
    return "".join(text_parts)


def create_node_callbacks() -> NodeCallbacks:
    """Create callbacks for execution flow tracking and detailed logs."""
    callbacks = NodeCallbacks()

    async def before_node_callback(
        ctx: CallbackContext,
        state: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        route = state.get("route", "")
        print(f"\n[callback:before] step={ctx.step_number} node={ctx.node_name} type={ctx.node_type} "
              f"state_keys={list(state.keys())} route={route}")
        return None

    async def after_node_callback(
        ctx: CallbackContext,
        state: dict[str, Any],
        result: Any,
        node_err: Optional[Exception],
    ) -> Any:
        if node_err is not None:
            return result

        start_time = ctx.execution_start_time or datetime.now()
        execution_time = (datetime.now() - start_time).total_seconds()

        history_entry = {
            "node_id": ctx.node_id,
            "node_name": ctx.node_name,
            "node_type": ctx.node_type,
            "step_number": ctx.step_number,
            "execution_time": execution_time,
        }

        if isinstance(result, dict):
            result["node_execution_history"] = [history_entry]
            last_response = result.get(STATE_KEY_LAST_RESPONSE, "")
            response_len = len(last_response) if isinstance(last_response, str) else 0
            print(f"[callback:after ] step={ctx.step_number} node={ctx.node_name} type={ctx.node_type} "
                  f"duration={execution_time:.3f}s output_keys={list(result.keys())} "
                  f"last_response_len={response_len}")
        else:
            print(f"[callback:after ] step={ctx.step_number} node={ctx.node_name} type={ctx.node_type} "
                  f"duration={execution_time:.3f}s output_type={type(result).__name__}")
        return result

    async def on_error_callback(
        ctx: CallbackContext,
        state: dict[str, Any],
        err: Exception,
    ) -> None:
        print(f"[callback:error ] step={ctx.step_number} node={ctx.node_name} type={ctx.node_type} "
              f"state_keys={list(state.keys())}")
        print(f"[callback:error ] details={err}")

    async def agent_event_callback(
        ctx: CallbackContext,
        state: dict[str, Any],
        event: Any,
    ) -> None:
        # Opearte what your need here
        # text = _extract_event_text(event)
        # actions = getattr(event, "actions", None)
        # state_delta = getattr(actions, "state_delta", None) if actions is not None else None
        # delta_keys = list(state_delta.keys()) if isinstance(state_delta, dict) else []

        # print(
        #     f"[callback:agent] node={ctx.node_name} author={getattr(event, 'author', '')} "
        #     f"branch={getattr(event, 'branch', '')} partial={getattr(event, 'partial', False)} "
        #     f"delta_keys={delta_keys} text={_truncate_text(text)}"
        # )
        pass

    callbacks.before_node = [before_node_callback]
    callbacks.after_node = [after_node_callback]
    callbacks.on_error = [on_error_callback]
    callbacks.agent_event = [agent_event_callback]

    return callbacks
