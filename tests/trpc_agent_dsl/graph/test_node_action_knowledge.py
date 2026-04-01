# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Execution-path tests for KnowledgeNodeAction."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from trpc_agent_sdk.dsl.graph._define import STATE_KEY_LAST_RESPONSE
from trpc_agent_sdk.dsl.graph._define import STATE_KEY_NODE_RESPONSES
from trpc_agent_sdk.dsl.graph._event_writer import AsyncEventWriter
from trpc_agent_sdk.dsl.graph._event_writer import EventWriter
from trpc_agent_sdk.dsl.graph._node_action._knowledge import KnowledgeNodeAction


def _build_action(query: Any, tool: Any, *, ctx: Any = None) -> KnowledgeNodeAction:
    """Create KnowledgeNodeAction with concrete event writers."""
    writer = EventWriter(
        writer=lambda payload: None,
        invocation_id="inv-1",
        author="knowledge-node",
        branch="root.knowledge-node",
    )
    async_writer = AsyncEventWriter(
        writer=lambda payload: None,
        invocation_id="inv-1",
        author="knowledge-node",
        branch="root.knowledge-node",
    )
    return KnowledgeNodeAction(
        name="knowledge-node",
        query=query,
        tool=tool,
        writer=writer,
        async_writer=async_writer,
        ctx=ctx,
    )


class TestKnowledgeNodeActionExecute:
    """Tests for knowledge-node query resolution and output mapping."""

    async def test_execute_requires_invocation_context(self):
        """Knowledge node should fail when invocation context is missing."""
        tool = SimpleNamespace(run_async=AsyncMock(return_value={"documents": []}))
        action = _build_action("query", tool, ctx=None)

        with pytest.raises(RuntimeError, match="requires InvocationContext"):
            await action.execute({})

    async def test_execute_normalizes_mixed_document_formats(self):
        """execute() should normalize mixed document payloads and preserve message."""
        ctx = SimpleNamespace(session=SimpleNamespace(id="session-1"))
        tool = SimpleNamespace(
            run_async=AsyncMock(return_value={
                "documents": [
                    {
                        "text": "doc-plain",
                        "score": 0.9
                    },
                    {
                        "text": "doc-with-meta",
                        "score": 0.8,
                        "metadata": {
                            "source": "kb-1"
                        }
                    },
                    {
                        "document": {
                            "page_content": "doc-from-document",
                            "metadata": {
                                "id": 7
                            }
                        },
                        "score": 0.7
                    },
                    {
                        "document": "not-a-dict",
                        "score": 0.6
                    },
                    "ignored-item",
                ],
                "message": "query finished",
            }))
        action = _build_action(lambda state: state["topic_id"], tool, ctx=ctx)

        result = await action.execute({"topic_id": 123})
        payload = result[STATE_KEY_LAST_RESPONSE]

        assert payload["message"] == "query finished"
        assert payload["documents"] == [
            {
                "text": "doc-plain",
                "score": 0.9
            },
            {
                "text": "doc-with-meta",
                "score": 0.8,
                "metadata": {
                    "source": "kb-1"
                }
            },
            {
                "text": "doc-from-document",
                "score": 0.7,
                "metadata": {
                    "id": 7
                }
            },
            {
                "text": "",
                "score": 0.6
            },
        ]
        assert result[STATE_KEY_NODE_RESPONSES] == {"knowledge-node": payload}
        tool.run_async.assert_awaited_once_with(tool_context=ctx, args={"query": "123"})

    async def test_execute_handles_non_dict_or_invalid_documents_payload(self):
        """Invalid tool payload shapes should normalize to empty documents without message."""
        ctx = SimpleNamespace(session=SimpleNamespace(id="session-2"))

        tool_raw = SimpleNamespace(run_async=AsyncMock(return_value="invalid"))
        action_raw = _build_action("query text", tool_raw, ctx=ctx)
        result_raw = await action_raw.execute({})
        assert result_raw[STATE_KEY_LAST_RESPONSE] == {"documents": []}

        tool_bad_docs = SimpleNamespace(run_async=AsyncMock(return_value={
            "documents": "not-a-list",
            "message": 999,
        }))
        action_bad_docs = _build_action("query text", tool_bad_docs, ctx=ctx)
        result_bad_docs = await action_bad_docs.execute({})
        assert result_bad_docs[STATE_KEY_LAST_RESPONSE] == {"documents": []}
