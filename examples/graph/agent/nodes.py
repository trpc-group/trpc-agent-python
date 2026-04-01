# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Node functions for the minimal graph workflow."""
import json
from typing import Any
from typing import Dict

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.dsl.graph import AsyncEventWriter
from trpc_agent_sdk.dsl.graph import STATE_KEY_LAST_RESPONSE
from trpc_agent_sdk.dsl.graph import STATE_KEY_LAST_TOOL_RESPONSE
from trpc_agent_sdk.dsl.graph import STATE_KEY_NODE_RESPONSES
from trpc_agent_sdk.dsl.graph import STATE_KEY_USER_INPUT
from trpc_agent_sdk.dsl.graph import State

from .state import DocumentState

ROUTE_PREVIEW = "preview"
ROUTE_SUMMARIZE = "summarize"
ROUTE_SUBGRAPH = "subgraph"
ROUTE_LLM_AGENT = "llm_agent"
ROUTE_TOOL = "tool"
ROUTE_CODE = "code"
ROUTE_MCP = "mcp"
ROUTE_KNOWLEDGE = "knowledge"
WORD_COUNT_SUMMARY_THRESHOLD = 40


def _normalize_text(text: str) -> str:
    return text.strip() if text else ""


def _truncate_text(text: str, max_len: int = 80) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def _format_value(value: Any, max_len: int = 80) -> str:
    text = repr(value)
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def _log_node(node_name: str, message: str) -> None:
    print(f"[node_execute:{node_name}] {message}")


async def extract_document(state: State) -> Dict[str, Any]:
    """Read user input and store it on the document state.

    Uses the base State type to show that generic nodes work with any schema.
    """
    user_text = _normalize_text(state.get(STATE_KEY_USER_INPUT, ""))
    if not user_text:
        raise ValueError("No user_input provided")

    _log_node("extract", f"args.user_input={_truncate_text(user_text)}")

    result = {
        "document": user_text,
        "word_count": len(user_text.split()),
    }
    _log_node("extract", f"return={_format_value(result)}")
    return result


async def decide_route(state: DocumentState, ctx: InvocationContext) -> Dict[str, Any]:
    """Decide which branch to take based on input and context.

    Demonstrates the InvocationContext parameter.
    """
    document = _normalize_text(state.get("document", ""))
    word_count = state.get("word_count", 0)

    _log_node("decide", f"args.document={_truncate_text(document)} word_count={word_count}")

    route = ROUTE_PREVIEW
    if document.lower().startswith("subgraph:"):
        route = ROUTE_SUBGRAPH
        document = _normalize_text(document[len("subgraph:"):])
    elif document.lower().startswith("llm_agent:"):
        route = ROUTE_LLM_AGENT
        document = _normalize_text(document[len("llm_agent:"):])
    elif document.lower().startswith("tool:"):
        route = ROUTE_TOOL
        document = _normalize_text(document[len("tool:"):])
    elif document.lower().startswith("code:"):
        route = ROUTE_CODE
        document = _normalize_text(document[len("code:"):])
    elif document.lower().startswith("mcp:"):
        route = ROUTE_MCP
        document = _normalize_text(document[len("mcp:"):])
    elif document.lower().startswith("knowledge:"):
        route = ROUTE_KNOWLEDGE
        document = _normalize_text(document[len("knowledge:"):])
    elif word_count >= WORD_COUNT_SUMMARY_THRESHOLD:
        route = ROUTE_SUMMARIZE

    result = {
        "route": route,
        "document": document,
        STATE_KEY_USER_INPUT: document,
        "context_note": f"user={ctx.user_id} session={ctx.session_id}",
        "word_count": len(document.split()) if document else word_count,
    }
    _log_node("decide", f"return={_format_value(result)}")
    return result


def create_route_choice(available_routes: set[str]):
    """Create a routing function that falls back to preview for unavailable routes."""

    def route_choice(state: DocumentState) -> str:
        route = state.get("route", ROUTE_PREVIEW)
        if route in available_routes:
            return route
        return ROUTE_PREVIEW

    return route_choice


async def stream_preview(state: DocumentState, async_writer: AsyncEventWriter) -> Dict[str, Any]:
    """Stream a quick preview using AsyncEventWriter.

    `async_writer` is awaitable and preserves write ordering by waiting for
    stream consumption. Use `writer` for fire-and-forget/high-frequency text.
    """
    document = _normalize_text(state.get("document", ""))
    preview = document[:120]

    _log_node("preview", f"args.document_len={len(document)}")

    if preview:
        await async_writer.write_text("[event_writer]: preview\n")
        await async_writer.write_text(f"[event_writer]: {preview}\n")

    result = {
        "preview": preview,
    }
    _log_node("preview", f"return={_format_value(result)}")
    return result


# ---------------------------------------------------------------------------
# MCP helper: prepare request arguments for the MCP node
# ---------------------------------------------------------------------------


async def prepare_mcp_request(state: DocumentState) -> Dict[str, Any]:
    """Parse the document text as MCP request arguments.

    Expects JSON after the ``mcp:`` prefix (e.g. ``mcp: {"a": 3, "b": 5}``).
    Falls back to ``{"input": <text>}`` if parsing fails.
    """
    document = _normalize_text(state.get("document", ""))
    _log_node("prepare_mcp_request", f"args.document={_truncate_text(document)}")

    try:
        args = json.loads(document)
        if not isinstance(args, dict):
            args = {"input": document}
    except (json.JSONDecodeError, TypeError):
        args = {"input": document}

    _log_node("prepare_mcp_request", f"return.args={_format_value(args)}")
    return {
        STATE_KEY_NODE_RESPONSES: {
            "prepare_mcp_request": args,
        },
    }


# ---------------------------------------------------------------------------
# Knowledge helper: resolve search query from state
# ---------------------------------------------------------------------------


def resolve_knowledge_query(state: DocumentState) -> str:
    """Return the document text as the knowledge search query."""
    return _normalize_text(state.get("document", ""))


# ---------------------------------------------------------------------------
# Final output formatter
# ---------------------------------------------------------------------------


async def format_output(state: DocumentState) -> Dict[str, Any]:
    """Format the final output shown to the user."""
    route = state.get("route", ROUTE_PREVIEW)
    word_count = state.get("word_count", 0)
    context_note = state.get("context_note", "")
    node_responses = state.get(STATE_KEY_NODE_RESPONSES, {})

    content = ""
    if route == ROUTE_SUMMARIZE:
        content = state.get(STATE_KEY_LAST_RESPONSE, "")
        if not content:
            content = node_responses.get("summarize", "")
    elif route == ROUTE_TOOL:
        tool_payload = state.get(STATE_KEY_LAST_TOOL_RESPONSE, "")
        content = tool_payload or "(Tool did not return a result)"
    elif route == ROUTE_SUBGRAPH:
        content = state.get("subgraph_reply", "")
    elif route == ROUTE_LLM_AGENT:
        content = state.get("query_reply", "")
    elif route == ROUTE_CODE:
        content = node_responses.get("code_exec", "(Code execution did not return a result)")
    elif route == ROUTE_MCP:
        mcp_result = node_responses.get("mcp_call", "")
        content = str(mcp_result) if mcp_result else "(MCP did not return a result)"
    elif route == ROUTE_KNOWLEDGE:
        knowledge_result = node_responses.get("knowledge_search", {})
        if isinstance(knowledge_result, dict):
            documents = knowledge_result.get("documents", [])
            if documents:
                doc_lines = []
                for i, doc in enumerate(documents[:3], 1):
                    text = doc.get("text", "")[:120]
                    score = doc.get("score", 0.0)
                    doc_lines.append(f"  [{i}] (score={score:.2f}) {text}")
                content = f"Found {len(documents)} documents:\n" + "\n".join(doc_lines)
            else:
                content = "(No documents found)"
        else:
            content = str(knowledge_result)
    else:
        content = state.get("preview", "") or state.get("document", "")

    if not content:
        content = "(No content produced)"

    execution_flow = _format_execution_flow(state.get("node_execution_history", []))

    final_output = f"""
==============================
 Graph Result
==============================

{content}

------------------------------
 Processing
------------------------------
Route: {route}
Word Count: {word_count}
Context: {context_note}{execution_flow}
"""

    result = {
        STATE_KEY_LAST_RESPONSE: final_output.strip(),
    }
    _log_node(
        "format_output",
        f"return.last_response_len={len(result[STATE_KEY_LAST_RESPONSE])}",
    )
    return result


def _format_execution_flow(history: list[dict[str, Any]]) -> str:
    if not history:
        return ""

    lines = ["Execution Flow:"]
    for idx, entry in enumerate(history, start=1):
        name = entry.get("node_name", entry.get("node_id", "unknown"))
        node_type = entry.get("node_type", "")
        duration = entry.get("execution_time", 0.0)
        lines.append(f"  {idx}. {name} ({node_type}) - {duration:.3f}s")

    return "\n" + "\n".join(lines)
