# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for graph state reducers and helpers."""

from google.genai.types import Content
from google.genai.types import Part
from trpc_agent_sdk.dsl.graph._constants import METADATA_KEY_INVOCATION_ID
from trpc_agent_sdk.dsl.graph._constants import METADATA_KEY_SESSION_ID
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_LAST_RESPONSE
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_LAST_RESPONSE_ID
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_LAST_TOOL_RESPONSE
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_MESSAGES
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_METADATA
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_NODE_RESPONSES
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_ONE_SHOT_MESSAGES
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_SESSION
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_STEP_NUMBER
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_USER_INPUT
from trpc_agent_sdk.dsl.graph._state import StateUtils
from trpc_agent_sdk.dsl.graph._state import append_list
from trpc_agent_sdk.dsl.graph._state import merge_dict
from trpc_agent_sdk.dsl.graph._state import messages_reducer


class TestStateReducers:
    """Tests for reducer behavior."""

    def test_messages_reducer_appends_single_or_list_messages(self):
        """Reducer should support both single Content and list inputs."""
        first = Content(role="user", parts=[Part.from_text(text="hello")])
        second = Content(role="model", parts=[Part.from_text(text="world")])

        with_single = messages_reducer([first], second)
        with_list = messages_reducer([first], [second])

        assert with_single == [first, second]
        assert with_list == [first, second]

    def test_merge_dict_and_append_list_handle_none_and_mixed_input(self):
        """Base reducers should gracefully handle None and scalar/list updates."""
        assert merge_dict(None, {"a": 1}) == {"a": 1}
        assert merge_dict({"a": 1}, None) == {"a": 1}
        assert merge_dict({"a": 1}, {"a": 2, "b": 3}) == {"a": 2, "b": 3}

        assert append_list(None, None) == []
        assert append_list([1], 2) == [1, 2]
        assert append_list([1], [2, 3]) == [1, 2, 3]


class TestStateUtilsOneShot:
    """Tests for one-shot message consume flow."""

    def test_consume_one_shot_messages_combines_and_clears_consumed_entries(self):
        """Global and node-scoped one-shot messages should be consumed together."""
        global_msg = Content(role="user", parts=[Part.from_text(text="global")])
        node_msg = Content(role="user", parts=[Part.from_text(text="node")])
        other_node_msg = Content(role="user", parts=[Part.from_text(text="other")])

        state = {
            STATE_KEY_ONE_SHOT_MESSAGES: [global_msg],
            STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE: {
                "node_a": [node_msg],
                "node_b": [other_node_msg],
            },
        }

        consumed, state_update = StateUtils.consume_one_shot_messages(state, "node_a")

        assert consumed == [global_msg, node_msg]
        assert state_update[STATE_KEY_ONE_SHOT_MESSAGES] == []
        assert state_update[STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE] == {"node_b": [other_node_msg]}

    def test_consume_one_shot_messages_returns_empty_update_when_nothing_to_clear(self):
        """No one-shot content should produce no state mutation."""
        consumed, state_update = StateUtils.consume_one_shot_messages({}, "node_a")

        assert consumed == []
        assert state_update == {}


class TestStateUtilsClone:
    """Tests for deep clone helper."""

    def test_clone_creates_deep_copy(self):
        """Mutating nested objects in clone must not affect source state."""
        source = {
            "nested": {
                "items": [1, 2],
            }
        }

        cloned = StateUtils.clone(source)
        cloned["nested"]["items"].append(3)

        assert source["nested"]["items"] == [1, 2]
        assert cloned["nested"]["items"] == [1, 2, 3]


class TestStateUtilsGetters:
    """Tests for state getter convenience methods."""

    def test_getters_cover_present_and_default_paths(self):
        """Getter APIs should return values when present and sensible defaults otherwise."""
        message = Content(role="user", parts=[Part.from_text(text="hello")])
        session = object()
        state = {
            STATE_KEY_USER_INPUT: "input",
            STATE_KEY_LAST_RESPONSE: "response",
            STATE_KEY_LAST_RESPONSE_ID: "resp-1",
            STATE_KEY_LAST_TOOL_RESPONSE: "tool-out",
            STATE_KEY_NODE_RESPONSES: {
                "n1": "v1"
            },
            STATE_KEY_METADATA: {
                METADATA_KEY_INVOCATION_ID: "inv-1",
                METADATA_KEY_SESSION_ID: "sess-1",
            },
            STATE_KEY_MESSAGES: [message],
            STATE_KEY_SESSION: session,
            STATE_KEY_ONE_SHOT_MESSAGES: [message],
            STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE: {
                "n1": [message]
            },
            STATE_KEY_STEP_NUMBER: 9,
        }

        assert StateUtils.get_user_input(state) == "input"
        assert StateUtils.get_last_response(state) == "response"
        assert StateUtils.get_last_response_id(state) == "resp-1"
        assert StateUtils.get_last_tool_response(state) == "tool-out"
        assert StateUtils.get_node_response(state, "n1") == "v1"
        assert StateUtils.get_metadata(state)[METADATA_KEY_INVOCATION_ID] == "inv-1"
        assert StateUtils.get_invocation_id(state) == "inv-1"
        assert StateUtils.get_session_id(state) == "sess-1"
        assert StateUtils.get_messages(state) == [message]
        assert StateUtils.get_session(state) is session
        assert StateUtils.get_one_shot_messages(state) == [message]
        assert StateUtils.get_one_shot_messages_for_node(state, "n1") == [message]
        assert StateUtils.get_step_number(state) == 9

        empty: dict = {}
        assert StateUtils.get_user_input(empty) == ""
        assert StateUtils.get_last_response(empty) == ""
        assert StateUtils.get_last_response_id(empty) == ""
        assert StateUtils.get_last_tool_response(empty) == ""
        assert StateUtils.get_node_response(empty, "missing") is None
        assert StateUtils.get_invocation_id(empty) == ""
        assert StateUtils.get_session_id(empty) == ""
        assert StateUtils.get_messages(empty) == []
        assert StateUtils.get_session(empty) is None
        assert StateUtils.get_one_shot_messages(empty) == []
        assert StateUtils.get_one_shot_messages_for_node(empty, "n1") == []
        assert StateUtils.get_step_number(empty) == 0
