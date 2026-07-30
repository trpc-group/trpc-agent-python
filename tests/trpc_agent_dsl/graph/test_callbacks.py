# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for graph node callback utilities."""

from datetime import datetime

from trpc_agent_sdk.dsl.graph._callbacks import NodeCallbackContext
from trpc_agent_sdk.dsl.graph._callbacks import NodeCallbacks
from trpc_agent_sdk.dsl.graph._callbacks import create_logging_callbacks
from trpc_agent_sdk.dsl.graph._callbacks import merge_callbacks


class _InMemoryLogger:
    """Simple logger double that records info logs."""

    def __init__(self):
        self.messages: list[str] = []

    def info(self, message: str) -> None:
        self.messages.append(message)


class TestNodeCallbackContext:
    """Tests for callback execution context."""

    def test_post_init_populates_default_fields(self):
        """Context should auto-fill node_name and execution_start_time."""
        ctx = NodeCallbackContext(node_id="planner", node_name="")

        assert ctx.node_name == "planner"
        assert isinstance(ctx.execution_start_time, datetime)


class TestCallbackMerging:
    """Tests for global/node callback merge behavior."""

    def test_merge_callbacks_respects_phase_specific_order(self):
        """Before/error callbacks run global-first while after runs node-first."""

        async def global_before(ctx, state):
            del ctx, state
            return None

        async def node_before(ctx, state):
            del ctx, state
            return None

        async def global_after(ctx, state, result, error):
            del ctx, state, result, error
            return None

        async def node_after(ctx, state, result, error):
            del ctx, state, result, error
            return None

        async def global_error(ctx, state, error):
            del ctx, state, error

        async def node_error(ctx, state, error):
            del ctx, state, error

        async def global_agent_event(ctx, state, event):
            del ctx, state, event

        async def node_agent_event(ctx, state, event):
            del ctx, state, event

        global_callbacks = NodeCallbacks(
            before_node=[global_before],
            after_node=[global_after],
            on_error=[global_error],
            agent_event=[global_agent_event],
        )
        node_callbacks = NodeCallbacks(
            before_node=[node_before],
            after_node=[node_after],
            on_error=[node_error],
            agent_event=[node_agent_event],
        )

        merged = merge_callbacks(global_callbacks, node_callbacks)

        assert merged is not None
        assert merged.before_node == [global_before, node_before]
        assert merged.after_node == [node_after, global_after]
        assert merged.on_error == [global_error, node_error]
        assert merged.agent_event == [global_agent_event, node_agent_event]


class TestLoggingCallbacks:
    """Tests for convenience logging callback factory."""

    async def test_create_logging_callbacks_emit_expected_messages(self):
        """Generated callbacks should log before/after/error lifecycle points."""
        logger = _InMemoryLogger()
        callbacks = create_logging_callbacks(logger=logger)
        ctx = NodeCallbackContext(node_id="summarizer")

        await callbacks.before_node[0](ctx, {})
        await callbacks.after_node[0](ctx, {}, {"ok": True}, None)
        await callbacks.on_error[0](ctx, {}, RuntimeError("boom"))

        assert len(logger.messages) == 3
        assert "Starting node execution" in logger.messages[0]
        assert "Completed in" in logger.messages[1]
        assert "Error: boom" in logger.messages[2]

    async def test_create_logging_callbacks_without_logger_uses_print_path(self):
        """When logger is absent, callbacks should fall back to print()."""
        callbacks = create_logging_callbacks(logger=None, log_after=False, log_errors=False)
        ctx = NodeCallbackContext(node_id="printer")

        await callbacks.before_node[0](ctx, {})
