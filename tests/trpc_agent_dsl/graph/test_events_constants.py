# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for graph event constants and enums."""

from trpc_agent_sdk.dsl.graph._events._constants import (
    GRAPH_EXECUTION_KEY_END_TIME,
    GRAPH_EXECUTION_KEY_ERROR,
    GRAPH_EXECUTION_KEY_FINAL_STATE_KEYS,
    GRAPH_EXECUTION_KEY_PHASE,
    GRAPH_EXECUTION_KEY_START_TIME,
    GRAPH_EXECUTION_KEY_STATE_DELTA_KEYS,
    GRAPH_EXECUTION_KEY_TOTAL_DURATION_MS,
    GRAPH_EXECUTION_KEY_TOTAL_STEPS,
    METADATA_KEY_MODEL,
    METADATA_KEY_NODE,
    METADATA_KEY_STATE,
    METADATA_KEY_TOOL,
    ExecutionPhase,
    GraphEventType,
)


class TestMetadataKeyConstants:
    """Verify metadata key string literals are stable."""

    def test_metadata_key_values(self):
        assert METADATA_KEY_NODE == "_node_metadata"
        assert METADATA_KEY_TOOL == "_tool_metadata"
        assert METADATA_KEY_MODEL == "_model_metadata"
        assert METADATA_KEY_STATE == "_state_metadata"


class TestGraphExecutionKeyConstants:
    """Verify graph execution state delta key string literals."""

    def test_execution_key_values(self):
        assert GRAPH_EXECUTION_KEY_PHASE == "phase"
        assert GRAPH_EXECUTION_KEY_TOTAL_STEPS == "total_steps"
        assert GRAPH_EXECUTION_KEY_TOTAL_DURATION_MS == "total_duration_ms"
        assert GRAPH_EXECUTION_KEY_START_TIME == "start_time"
        assert GRAPH_EXECUTION_KEY_END_TIME == "end_time"
        assert GRAPH_EXECUTION_KEY_FINAL_STATE_KEYS == "final_state_keys"
        assert GRAPH_EXECUTION_KEY_STATE_DELTA_KEYS == "state_delta_keys"
        assert GRAPH_EXECUTION_KEY_ERROR == "error"


class TestGraphEventType:
    """Tests for GraphEventType enum."""

    def test_is_string_enum(self):
        assert isinstance(GraphEventType.GRAPH_EXECUTION, str)

    def test_enum_values(self):
        assert GraphEventType.GRAPH_EXECUTION == "graph.execution"
        assert GraphEventType.GRAPH_NODE_EXECUTION == "graph.node.execution"
        assert GraphEventType.GRAPH_NODE_START == "graph.node.start"
        assert GraphEventType.GRAPH_NODE_COMPLETE == "graph.node.complete"
        assert GraphEventType.GRAPH_NODE_ERROR == "graph.node.error"
        assert GraphEventType.GRAPH_STATE_UPDATE == "graph.state.update"

    def test_all_members_start_with_graph_prefix(self):
        for member in GraphEventType:
            assert member.value.startswith("graph."), f"{member.name} should start with 'graph.'"

    def test_member_count(self):
        assert len(GraphEventType) == 6

    def test_can_be_constructed_from_value(self):
        assert GraphEventType("graph.execution") is GraphEventType.GRAPH_EXECUTION
        assert GraphEventType("graph.node.start") is GraphEventType.GRAPH_NODE_START

    def test_string_comparison(self):
        assert GraphEventType.GRAPH_EXECUTION == "graph.execution"
        assert "graph.node.error" == GraphEventType.GRAPH_NODE_ERROR


class TestExecutionPhase:
    """Tests for ExecutionPhase enum."""

    def test_is_string_enum(self):
        assert isinstance(ExecutionPhase.START, str)

    def test_enum_values(self):
        assert ExecutionPhase.START == "start"
        assert ExecutionPhase.COMPLETE == "complete"
        assert ExecutionPhase.ERROR == "error"

    def test_member_count(self):
        assert len(ExecutionPhase) == 3

    def test_can_be_constructed_from_value(self):
        assert ExecutionPhase("start") is ExecutionPhase.START
        assert ExecutionPhase("complete") is ExecutionPhase.COMPLETE
        assert ExecutionPhase("error") is ExecutionPhase.ERROR

    def test_string_comparison(self):
        assert ExecutionPhase.START == "start"
        assert "error" == ExecutionPhase.ERROR
