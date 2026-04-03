# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Node functions for graph multi-turn workflow."""
from typing import Any
from typing import Dict

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.dsl.graph import STATE_KEY_LAST_RESPONSE
from trpc_agent_sdk.dsl.graph import STATE_KEY_USER_INPUT

from .state import MultiTurnState

ROUTE_LLM = "llm"
ROUTE_AGENT = "agent"


def _normalize_text(text: str) -> str:
    return text.strip() if text else ""


def _truncate_text(text: str, max_len: int = 80) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def _log_node(node_name: str, message: str) -> None:
    print(f"[node_execute:{node_name}] {message}")


async def decide_route(state: MultiTurnState, ctx: InvocationContext) -> Dict[str, Any]:
    """Select branch based on query prefix: `llm:` or `agent:`."""
    query_text = _normalize_text(state.get(STATE_KEY_USER_INPUT, ""))
    route = ROUTE_LLM
    if not query_text:
        raise ValueError("No user_input provided")

    lower_text = query_text.lower()
    if lower_text.startswith("agent:"):
        route = ROUTE_AGENT
        query_text = _normalize_text(query_text[len("agent:"):])
    elif lower_text.startswith("llm:"):
        route = ROUTE_LLM
        query_text = _normalize_text(query_text[len("llm:"):])

    turn_count = sum(1 for event in ctx.session.events if event.author == "user")

    result = {
        "route": route,
        "query_text": query_text,
        STATE_KEY_USER_INPUT: query_text,
        "context_note": f"user={ctx.user_id} session={ctx.session_id} turn={turn_count}",
    }
    _log_node("decide", f"return={result}")
    return result


def route_choice(state: MultiTurnState) -> str:
    """Route function used by conditional edges."""
    return state.get("route", ROUTE_LLM)


async def format_output(state: MultiTurnState) -> Dict[str, Any]:
    """Format the final output for current turn."""
    route = state.get("route", ROUTE_LLM)
    if route == ROUTE_AGENT:
        reply = state.get("agent_reply", "")
    else:
        reply = state.get("llm_reply", "")

    if not reply:
        reply = state.get(STATE_KEY_LAST_RESPONSE, "")
    if not reply:
        reply = "(No response generated)"

    flow = _format_execution_flow(state.get("node_execution_history", []))
    result_text = f"""
==============================
 Graph Multi-Turn Result
==============================

Branch: {route}
Context: {state.get('context_note', '')}

{reply}
{flow}
""".strip()

    result = {STATE_KEY_LAST_RESPONSE: result_text}
    _log_node("format_output", f"return.last_response_len={len(result_text)}")
    return result


def _format_execution_flow(history: list[dict[str, Any]]) -> str:
    if not history:
        return ""

    lines = ["", "Execution Flow:"]
    for idx, entry in enumerate(history, start=1):
        name = entry.get("node_name", entry.get("node_id", "unknown"))
        node_type = entry.get("node_type", "")
        duration = entry.get("execution_time", 0.0)
        lines.append(f"  {idx}. {name} ({node_type}) - {duration:.3f}s")
    return "\n".join(lines)
