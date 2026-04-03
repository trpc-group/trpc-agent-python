# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Node functions for graph interrupt workflow."""
from typing import Any
from typing import Dict

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.dsl.graph import STATE_KEY_LAST_RESPONSE
from trpc_agent_sdk.dsl.graph import STATE_KEY_NODE_RESPONSES
from trpc_agent_sdk.dsl.graph import STATE_KEY_USER_INPUT
from trpc_agent_sdk.dsl.graph import interrupt

from .state import InterruptState

ROUTE_APPROVED = "approved"
ROUTE_REJECTED = "rejected"


def _normalize_text(text: str) -> str:
    return text.strip() if text else ""


def _truncate_text(text: str, max_len: int = 100) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def _log_node(node_name: str, message: str) -> None:
    print(f"[node_execute:{node_name}] {message}")


async def approval_gate(state: InterruptState, ctx: InvocationContext) -> Dict[str, Any]:
    """Pause execution and wait for user approval via graph interrupt."""
    request_text = _normalize_text(state.get(STATE_KEY_USER_INPUT, ""))
    suggested_action = _normalize_text(state.get(STATE_KEY_LAST_RESPONSE, ""))
    if not suggested_action:
        suggested_action = _normalize_text(state.get(STATE_KEY_NODE_RESPONSES, {}).get("draft_action", ""))
    if not suggested_action:
        suggested_action = "Proceed with a minimal safe default action."

    payload = {
        "title": "Approval Required",
        "request": request_text,
        "suggested_action": suggested_action,
        "options": ["approved", "rejected"],
        "tip": "Provide status in FunctionResponse.response, e.g. {'status':'approved','note':'...'}",
    }

    _log_node("approval_gate", f"interrupt_payload={payload}")
    decision = interrupt(payload)

    status = "rejected"
    note = ""
    if isinstance(decision, dict):
        status_value = str(decision.get("status", "approved")).strip().lower()
        if status_value in {"approved", "rejected"}:
            status = status_value
        note = str(decision.get("note", "")).strip()
    elif isinstance(decision, str):
        status_value = decision.strip().lower()
        if status_value in {"approved", "rejected"}:
            status = status_value

    summary_request = (f"User request: {request_text}\n"
                       f"Approved action: {suggested_action}\n"
                       f"Approval note: {note or '(none)'}\n"
                       "Summarize what was approved and what will happen next in 1-2 short sentences.")

    result = {
        "suggested_action": suggested_action,
        "approval_status": status,
        "approval_note": note,
        "summary_request": summary_request,
        "context_note": f"user={ctx.user_id} session={ctx.session_id}",
    }
    _log_node("approval_gate", f"resume_decision={result}")
    return result


def route_after_approval(state: InterruptState) -> str:
    """Route to summary agent only when user approved."""
    status = _normalize_text(state.get("approval_status", ROUTE_APPROVED)).lower()
    if status == ROUTE_APPROVED:
        return ROUTE_APPROVED
    return ROUTE_REJECTED


async def finalize_output(state: InterruptState) -> Dict[str, Any]:
    """Build final output based on approval decision."""
    status = state.get("approval_status", "approved")
    note = state.get("approval_note", "")
    suggested_action = state.get("suggested_action", "")
    approval_summary = _normalize_text(state.get("approval_summary", ""))

    if status == "approved":
        summary = f"Decision: approved\nAction: {suggested_action}"
        if approval_summary:
            summary = f"{summary}\nSummary: {approval_summary}"
    else:
        summary = f"Decision: rejected\nAction blocked: {suggested_action}"

    if note:
        summary = f"{summary}\nNote: {note}"

    output = f"""
==============================
 Graph Interrupt Result
==============================

{summary}
Context: {state.get('context_note', '')}
""".strip()

    result = {STATE_KEY_LAST_RESPONSE: output}
    _log_node("finalize_output", f"return.last_response_len={len(output)}")
    return result
