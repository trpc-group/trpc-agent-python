# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for graph state key constants and is_unsafe_state_key."""

from trpc_agent_sdk.dsl.graph._constants import (
    END,
    METADATA_KEY_AGENT_NAME,
    METADATA_KEY_BRANCH,
    METADATA_KEY_INVOCATION_ID,
    METADATA_KEY_SESSION_ID,
    NODE_TYPE_AGENT,
    NODE_TYPE_CODE,
    NODE_TYPE_FUNCTION,
    NODE_TYPE_KNOWLEDGE,
    NODE_TYPE_LLM,
    NODE_TYPE_TOOL,
    ROLE_FUNCTION,
    ROLE_MODEL,
    ROLE_SYSTEM,
    ROLE_USER,
    START,
    STATE_KEY_AGENT_CALLBACKS,
    STATE_KEY_CHECKPOINT_BLOBS,
    STATE_KEY_CHECKPOINT_WRITES,
    STATE_KEY_CHECKPOINTS,
    STATE_KEY_CURRENT_NODE_ID,
    STATE_KEY_EXEC_CONTEXT,
    STATE_KEY_LAST_RESPONSE,
    STATE_KEY_LAST_RESPONSE_ID,
    STATE_KEY_LAST_TOOL_RESPONSE,
    STATE_KEY_LONG_RUNNING_PREFIX,
    STATE_KEY_MESSAGES,
    STATE_KEY_METADATA,
    STATE_KEY_MODEL_CALLBACKS,
    STATE_KEY_NODE_CALLBACKS,
    STATE_KEY_NODE_RESPONSES,
    STATE_KEY_ONE_SHOT_MESSAGES,
    STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE,
    STATE_KEY_PENDING_INTERRUPT,
    STATE_KEY_PENDING_INTERRUPT_AUTHOR,
    STATE_KEY_PENDING_INTERRUPT_BRANCH,
    STATE_KEY_PENDING_INTERRUPT_ID,
    STATE_KEY_SESSION,
    STATE_KEY_STEP_NUMBER,
    STATE_KEY_TOOL_CALLBACKS,
    STATE_KEY_USER_INPUT,
    STREAM_KEY_ACK,
    STREAM_KEY_EVENT,
    UNSAFE_STATE_KEYS,
    is_unsafe_state_key,
)


class TestStateKeyValues:
    """Verify state key string literals are stable (guards against accidental renames)."""

    def test_core_state_keys(self):
        assert STATE_KEY_USER_INPUT == "user_input"
        assert STATE_KEY_MESSAGES == "messages"
        assert STATE_KEY_LAST_RESPONSE == "last_response"
        assert STATE_KEY_LAST_RESPONSE_ID == "last_response_id"
        assert STATE_KEY_LAST_TOOL_RESPONSE == "last_tool_response"
        assert STATE_KEY_NODE_RESPONSES == "node_responses"

    def test_one_shot_message_keys(self):
        assert STATE_KEY_ONE_SHOT_MESSAGES == "one_shot_messages"
        assert STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE == "one_shot_messages_by_node"

    def test_metadata_and_context_keys(self):
        assert STATE_KEY_METADATA == "metadata"
        assert STATE_KEY_SESSION == "session"
        assert STATE_KEY_CURRENT_NODE_ID == "current_node_id"
        assert STATE_KEY_EXEC_CONTEXT == "exec_context"

    def test_callback_keys(self):
        assert STATE_KEY_TOOL_CALLBACKS == "tool_callbacks"
        assert STATE_KEY_MODEL_CALLBACKS == "model_callbacks"
        assert STATE_KEY_AGENT_CALLBACKS == "agent_callbacks"
        assert STATE_KEY_NODE_CALLBACKS == "node_callbacks"

    def test_metadata_sub_keys(self):
        assert METADATA_KEY_INVOCATION_ID == "invocation_id"
        assert METADATA_KEY_SESSION_ID == "session_id"
        assert METADATA_KEY_BRANCH == "branch"
        assert METADATA_KEY_AGENT_NAME == "agent_name"

    def test_node_type_values(self):
        assert NODE_TYPE_FUNCTION == "function"
        assert NODE_TYPE_LLM == "llm"
        assert NODE_TYPE_TOOL == "tool"
        assert NODE_TYPE_AGENT == "agent"
        assert NODE_TYPE_CODE == "code"
        assert NODE_TYPE_KNOWLEDGE == "knowledge"

    def test_graph_boundary_constants(self):
        assert START == "__start__"
        assert END == "__end__"

    def test_step_and_stream_keys(self):
        assert STATE_KEY_STEP_NUMBER == "step_number"
        assert STREAM_KEY_EVENT == "_trpc_graph_event"
        assert STREAM_KEY_ACK == "_trpc_graph_ack"

    def test_checkpoint_keys(self):
        assert STATE_KEY_CHECKPOINTS == "_trpc_graph_checkpoints"
        assert STATE_KEY_CHECKPOINT_WRITES == "_trpc_graph_checkpoint_writes"
        assert STATE_KEY_CHECKPOINT_BLOBS == "_trpc_graph_checkpoint_blobs"

    def test_interrupt_keys(self):
        assert STATE_KEY_PENDING_INTERRUPT == "_trpc_graph_pending_interrupt"
        assert STATE_KEY_PENDING_INTERRUPT_ID == "_trpc_graph_pending_interrupt_id"
        assert STATE_KEY_PENDING_INTERRUPT_AUTHOR == "_trpc_graph_pending_interrupt_author"
        assert STATE_KEY_PENDING_INTERRUPT_BRANCH == "_trpc_graph_pending_interrupt_branch"
        assert STATE_KEY_LONG_RUNNING_PREFIX == "__trpc_graph_long_running__"

    def test_role_values(self):
        assert ROLE_USER == "user"
        assert ROLE_MODEL == "model"
        assert ROLE_FUNCTION == "function"
        assert ROLE_SYSTEM == "system"


class TestUnsafeStateKeys:
    """Tests for the UNSAFE_STATE_KEYS set and is_unsafe_state_key function."""

    def test_unsafe_keys_is_frozenset(self):
        assert isinstance(UNSAFE_STATE_KEYS, frozenset)

    def test_unsafe_keys_contains_expected_members(self):
        expected = {
            STATE_KEY_SESSION,
            STATE_KEY_EXEC_CONTEXT,
            STATE_KEY_CURRENT_NODE_ID,
            STATE_KEY_TOOL_CALLBACKS,
            STATE_KEY_MODEL_CALLBACKS,
            STATE_KEY_AGENT_CALLBACKS,
            STATE_KEY_NODE_CALLBACKS,
            STATE_KEY_CHECKPOINTS,
            STATE_KEY_CHECKPOINT_WRITES,
            STATE_KEY_CHECKPOINT_BLOBS,
            STATE_KEY_PENDING_INTERRUPT,
            STATE_KEY_PENDING_INTERRUPT_ID,
            STATE_KEY_PENDING_INTERRUPT_AUTHOR,
            STATE_KEY_PENDING_INTERRUPT_BRANCH,
        }
        assert UNSAFE_STATE_KEYS == expected

    def test_is_unsafe_state_key_returns_true_for_unsafe_keys(self):
        for key in UNSAFE_STATE_KEYS:
            assert is_unsafe_state_key(key) is True, f"Expected {key!r} to be unsafe"

    def test_is_unsafe_state_key_returns_false_for_safe_keys(self):
        safe_keys = [
            STATE_KEY_USER_INPUT,
            STATE_KEY_MESSAGES,
            STATE_KEY_LAST_RESPONSE,
            STATE_KEY_NODE_RESPONSES,
            STATE_KEY_METADATA,
            STATE_KEY_STEP_NUMBER,
            "arbitrary_custom_key",
            "",
        ]
        for key in safe_keys:
            assert is_unsafe_state_key(key) is False, f"Expected {key!r} to be safe"

    def test_serializable_keys_are_not_unsafe(self):
        """Keys that represent user-visible data should never appear in UNSAFE_STATE_KEYS."""
        serializable = {
            STATE_KEY_USER_INPUT, STATE_KEY_MESSAGES, STATE_KEY_LAST_RESPONSE,
            STATE_KEY_LAST_RESPONSE_ID, STATE_KEY_LAST_TOOL_RESPONSE,
            STATE_KEY_NODE_RESPONSES, STATE_KEY_ONE_SHOT_MESSAGES,
            STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE, STATE_KEY_METADATA,
            STATE_KEY_STEP_NUMBER,
        }
        assert serializable.isdisjoint(UNSAFE_STATE_KEYS)
