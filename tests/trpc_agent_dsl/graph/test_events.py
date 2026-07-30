# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for graph event building and extraction."""

from datetime import datetime
from datetime import timedelta

from google.genai.types import Content
from google.genai.types import Part
from trpc_agent_sdk.dsl.graph._events import EventBuilder
from trpc_agent_sdk.dsl.graph._events import EventUtils
from trpc_agent_sdk.dsl.graph._events import ExecutionPhase
from trpc_agent_sdk.dsl.graph._events import GraphEventType
from trpc_agent_sdk.dsl.graph._events import METADATA_KEY_NODE
from trpc_agent_sdk.dsl.graph._events import METADATA_KEY_STATE
from trpc_agent_sdk.dsl.graph._events import StateUpdateMetadata
from trpc_agent_sdk.dsl.graph._events._metadata import ModelExecutionMetadata
from trpc_agent_sdk.dsl.graph._events._metadata import NodeExecutionMetadata
from trpc_agent_sdk.dsl.graph._events._metadata import ToolExecutionMetadata
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.types import EventActions


class TestEventBuilder:
    """Tests for event creation behavior."""

    def setup_method(self):
        self.builder = EventBuilder(
            invocation_id="inv-1",
            author="planner",
            branch="root.planner",
        )

    def test_node_start_contains_structured_metadata_and_truncates_model_input(self):
        """Node start should carry metadata and expose graph object type."""
        event = self.builder.node_start(
            node_id="planner",
            step_number=2,
            input_keys=["messages"],
            model_name="test-model",
            model_input="x" * 700,
        )

        metadata = EventUtils.get_node_metadata(event)

        assert metadata is not None
        assert metadata.node_id == "planner"
        assert metadata.phase == ExecutionPhase.START.value
        assert metadata.input_keys == ["messages"]
        assert len(metadata.model_input or "") == 500
        assert event.object == GraphEventType.GRAPH_NODE_START
        assert event.partial is True

    def test_model_complete_uses_error_phase_and_truncates_payloads(self):
        """Model completion should switch to error phase when error is provided."""
        start_time = datetime.now() - timedelta(milliseconds=20)
        event = self.builder.model_complete(
            model_name="test-model",
            node_id="planner",
            start_time=start_time,
            input_text="i" * 800,
            output_text="o" * 700,
            error="timeout",
        )

        metadata = EventUtils.get_model_metadata(event)

        assert metadata is not None
        assert metadata.phase == ExecutionPhase.ERROR.value
        assert metadata.error == "timeout"
        assert len(metadata.input_text or "") == 500
        assert len(metadata.output_text or "") == 500
        assert EventUtils.get_duration_ms(event) > 0
        assert "failed" in event.get_text()

    def test_tool_complete_tracks_duration_and_truncates_arguments(self):
        """Tool completion should include bounded args/result fields and duration."""
        start_time = datetime.now() - timedelta(milliseconds=10)
        event = self.builder.tool_complete(
            tool_name="search",
            tool_id="tool-1",
            node_id="planner",
            start_time=start_time,
            input_args="a" * 1200,
            output_result="b" * 1400,
        )

        metadata = EventUtils.get_tool_metadata(event)

        assert metadata is not None
        assert metadata.phase == ExecutionPhase.COMPLETE.value
        assert len(metadata.input_args or "") == 1000
        assert len(metadata.output_result or "") == 1000
        assert metadata.duration_ms > 0
        assert event.object == GraphEventType.GRAPH_NODE_EXECUTION

    def test_state_update_builds_state_metadata_and_removed_count_text(self):
        """State update event should expose updated/removed keys in metadata and text."""
        event = self.builder.state_update(
            updated_keys=["a", "b"],
            removed_keys=["c"],
            state_size=3,
        )

        metadata = EventUtils.get_metadata(event, METADATA_KEY_STATE, StateUpdateMetadata)

        assert metadata is not None
        assert metadata.updated_keys == ["a", "b"]
        assert metadata.removed_keys == ["c"]
        assert metadata.state_size == 3
        assert event.object == GraphEventType.GRAPH_STATE_UPDATE
        assert event.partial is True
        assert "1 removed" in event.get_text()

    def test_model_start_sets_partial_execution_event(self):
        """Model start should emit partial graph execution event metadata."""
        event = self.builder.model_start(
            model_name="test-model",
            node_id="planner",
            input_text="prompt text",
            step_number=7,
        )

        metadata = EventUtils.get_model_metadata(event)

        assert metadata is not None
        assert metadata.model_name == "test-model"
        assert metadata.phase == ExecutionPhase.START.value
        assert metadata.step_number == 7
        assert event.partial is True
        assert event.object == GraphEventType.GRAPH_NODE_EXECUTION

    def test_graph_complete_handles_success_and_error_payloads(self):
        """Graph completion should report phase, keys, and optional error details."""
        start_time = datetime.now() - timedelta(milliseconds=50)

        success = self.builder.graph_complete(
            total_steps=3,
            start_time=start_time,
            final_state={"a": 1},
            state_delta={"b": 2},
            error=None,
        )
        failed = self.builder.graph_complete(
            total_steps=2,
            start_time=start_time,
            final_state={},
            state_delta={},
            error="boom",
        )

        assert success.object == GraphEventType.GRAPH_EXECUTION
        assert success.actions.state_delta["phase"] == ExecutionPhase.COMPLETE.value
        assert success.actions.state_delta["final_state_keys"] == ["a"]
        assert success.actions.state_delta["state_delta_keys"] == ["b"]
        assert failed.actions.state_delta["phase"] == ExecutionPhase.ERROR.value
        assert failed.actions.state_delta["error"] == "boom"
        assert "failed" in failed.get_text()


class TestEventUtils:
    """Tests for graph event extraction helpers."""

    def test_utils_detect_graph_events_by_object_type(self):
        """Helpers should detect graph events via object type."""
        event = Event(
            invocation_id="inv-1",
            author="planner",
            object="graph.node.start",
            content=Content(role="model", parts=[Part.from_text(text="legacy")]),
            actions=EventActions(state_delta={
                "node_id": "node-x",
                "duration_ms": 12.5,
            }),
        )

        assert EventUtils.is_graph_event(event) is True
        assert EventUtils.get_event_type(event) == "graph.node.start"
        assert EventUtils.get_node_id(event) == "node-x"
        assert EventUtils.get_duration_ms(event) == 12.5

    def test_get_node_metadata_returns_none_on_invalid_metadata_shape(self):
        """Invalid metadata payloads should fail safely instead of raising."""
        event = Event(
            invocation_id="inv-1",
            author="planner",
            content=Content(role="model", parts=[Part.from_text(text="bad")]),
            actions=EventActions(state_delta={
                METADATA_KEY_NODE: "invalid",
            }),
        )

        assert EventUtils.get_node_metadata(event) is None

    def test_utils_fall_back_to_non_graph_defaults(self):
        """Helpers should return safe defaults for non-graph/non-metadata events."""
        event = Event(
            invocation_id="inv-1",
            author="planner",
            content=Content(role="model", parts=[Part.from_text(text="plain")]),
        )

        assert EventUtils.is_graph_event(event) is False
        assert EventUtils.get_event_type(event) is None
        assert EventUtils.get_node_id(event) is None
        assert EventUtils.get_duration_ms(event) == 0.0

    def test_get_node_id_prefers_tool_and_model_metadata_then_legacy_fallback(self):
        """Node id extraction should support tool/model metadata and legacy keys."""
        tool_event = Event(
            invocation_id="inv-1",
            author="planner",
            content=Content(role="model", parts=[Part.from_text(text="tool")]),
            actions=EventActions(state_delta={"_tool_metadata": {
                "node_id": "tool-node",
            }}),
        )
        model_event = Event(
            invocation_id="inv-1",
            author="planner",
            content=Content(role="model", parts=[Part.from_text(text="model")]),
            actions=EventActions(state_delta={"_model_metadata": {
                "node_id": "model-node",
            }}),
        )
        legacy_event = Event(
            invocation_id="inv-1",
            author="planner",
            content=Content(role="model", parts=[Part.from_text(text="legacy")]),
            actions=EventActions(state_delta={"node_id": "legacy-node"}),
        )

        assert EventUtils.get_node_id(tool_event) == "tool-node"
        assert EventUtils.get_node_id(model_event) == "model-node"
        assert EventUtils.get_node_id(legacy_event) == "legacy-node"

    def test_get_metadata_returns_none_for_invalid_dict_payload(self):
        """Malformed metadata dicts should fail closed without raising."""
        event = Event(
            invocation_id="inv-1",
            author="planner",
            content=Content(role="model", parts=[Part.from_text(text="bad")]),
            actions=EventActions(state_delta={"_model_metadata": {
                "model_name": "x",
            }}),
        )

        assert EventUtils.get_metadata(event, "_model_metadata", ModelExecutionMetadata) is None

    def test_node_metadata_from_event_converts_phase_string_to_enum(self):
        """Dataclass helper should normalize serialized enum values from events."""
        event = Event(
            invocation_id="inv-1",
            author="planner",
            content=Content(role="model", parts=[Part.from_text(text="ok")]),
            actions=EventActions(
                state_delta={METADATA_KEY_NODE: {
                    "node_id": "node-x",
                    "node_type": "function",
                    "phase": "complete",
                }}),
        )

        metadata = NodeExecutionMetadata.from_event(event)

        assert metadata is not None
        assert metadata.phase == ExecutionPhase.COMPLETE

    def test_tool_and_model_metadata_from_event_cover_success_and_failure_paths(self):
        """Tool/model metadata extractors should parse valid payloads and reject invalid ones."""
        tool_event = Event(
            invocation_id="inv-1",
            author="planner",
            content=Content(role="model", parts=[Part.from_text(text="tool")]),
            actions=EventActions(state_delta={
                "_tool_metadata": {
                    "tool_name": "search",
                    "tool_id": "t-1",
                    "node_id": "planner",
                    "phase": "start",
                }
            }),
        )
        invalid_tool_event = Event(
            invocation_id="inv-1",
            author="planner",
            content=Content(role="model", parts=[Part.from_text(text="tool")]),
            actions=EventActions(state_delta={"_tool_metadata": "invalid"}),
        )

        model_event = Event(
            invocation_id="inv-1",
            author="planner",
            content=Content(role="model", parts=[Part.from_text(text="model")]),
            actions=EventActions(
                state_delta={"_model_metadata": {
                    "model_name": "gpt",
                    "node_id": "planner",
                    "phase": "error",
                }}),
        )
        invalid_model_event = Event(
            invocation_id="inv-1",
            author="planner",
            content=Content(role="model", parts=[Part.from_text(text="model")]),
            actions=EventActions(state_delta={"_model_metadata": {
                "phase": "invalid"
            }}),
        )

        tool_metadata = ToolExecutionMetadata.from_event(tool_event)
        model_metadata = ModelExecutionMetadata.from_event(model_event)

        assert tool_metadata is not None
        assert tool_metadata.phase == ExecutionPhase.START
        assert ToolExecutionMetadata.from_event(invalid_tool_event) is None
        assert model_metadata is not None
        assert model_metadata.phase == ExecutionPhase.ERROR
        assert ModelExecutionMetadata.from_event(invalid_model_event) is None
