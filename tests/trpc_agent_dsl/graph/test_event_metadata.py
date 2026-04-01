# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Public metadata extraction tests for graph events."""

from datetime import datetime

from trpc_agent_sdk.dsl.graph._events import EventBuilder
from trpc_agent_sdk.dsl.graph._events import EventUtils
from trpc_agent_sdk.dsl.graph._events import ExecutionPhase


class TestEventMetadata:
    """Tests metadata availability through EventBuilder and EventUtils."""

    def test_node_events_expose_typed_node_metadata(self):
        """Node start/complete events should carry structured node metadata."""
        builder = EventBuilder(invocation_id="inv-1", author="worker", branch="root.worker")
        start_event = builder.node_start(
            node_id="worker",
            node_type="function",
            step_number=2,
            input_keys=["question"],
        )
        complete_event = builder.node_complete(
            node_id="worker",
            node_type="function",
            step_number=2,
            start_time=datetime.now(),
            output_keys=["answer"],
        )

        start_meta = EventUtils.get_node_metadata(start_event)
        complete_meta = EventUtils.get_node_metadata(complete_event)

        assert start_meta is not None
        assert start_meta.node_id == "worker"
        assert start_meta.phase == ExecutionPhase.START.value
        assert start_meta.input_keys == ["question"]

        assert complete_meta is not None
        assert complete_meta.node_id == "worker"
        assert complete_meta.phase == ExecutionPhase.COMPLETE.value
        assert complete_meta.output_keys == ["answer"]

    def test_model_and_tool_events_expose_typed_metadata(self):
        """Model/tool completion events should expose typed metadata with phase info."""
        builder = EventBuilder(invocation_id="inv-1", author="worker", branch="root.worker")
        model_event = builder.model_complete(
            model_name="mock-model",
            node_id="worker",
            start_time=datetime.now(),
            input_text="hello",
            output_text="world",
        )
        tool_event = builder.tool_complete(
            tool_name="search",
            tool_id="tool-1",
            node_id="worker",
            start_time=datetime.now(),
            input_args='{"q":"x"}',
            output_result='{"ok":true}',
        )

        model_meta = EventUtils.get_model_metadata(model_event)
        tool_meta = EventUtils.get_tool_metadata(tool_event)

        assert model_meta is not None
        assert model_meta.model_name == "mock-model"
        assert model_meta.phase == ExecutionPhase.COMPLETE.value

        assert tool_meta is not None
        assert tool_meta.tool_name == "search"
        assert tool_meta.phase == ExecutionPhase.COMPLETE.value
