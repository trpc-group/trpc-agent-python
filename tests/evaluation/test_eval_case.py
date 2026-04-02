# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for evaluation case (_eval_case)."""

import pytest

pytest.importorskip("trpc_agent_sdk._runners", reason="trpc_agent_sdk._runners not yet implemented")

from trpc_agent_sdk.evaluation import ConversationScenario
from trpc_agent_sdk.evaluation import EvalCase
from trpc_agent_sdk.evaluation import IntermediateData
from trpc_agent_sdk.evaluation import SessionInput
from trpc_agent_sdk.evaluation import get_all_tool_calls
from trpc_agent_sdk.evaluation import get_all_tool_responses
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import FunctionResponse


class TestSessionInput:
    """Test suite for SessionInput."""

    def test_session_input(self):
        """Test SessionInput creation."""
        s = SessionInput(app_name="my_app", user_id="u1", state={"k": "v"})
        assert s.app_name == "my_app"
        assert s.user_id == "u1"
        assert s.state == {"k": "v"}


class TestConversationScenario:
    """Test suite for ConversationScenario."""

    def test_conversation_scenario(self):
        """Test ConversationScenario creation."""
        c = ConversationScenario(
            starting_prompt="Hello",
            conversation_plan="Ask about weather",
        )
        assert c.starting_prompt == "Hello"
        assert c.conversation_plan == "Ask about weather"


class TestGetAllToolCalls:
    """Test suite for get_all_tool_calls."""

    def test_none_returns_empty(self):
        """Test None intermediate_data returns empty list."""
        assert get_all_tool_calls(None) == []

    def test_intermediate_data_with_tool_uses(self):
        """Test IntermediateData with tool_uses returns them."""
        fc = FunctionCall.model_validate({"name": "get_weather", "args": {"city": "北京"}})
        data = IntermediateData(tool_uses=[fc])
        result = get_all_tool_calls(data)
        assert len(result) == 1
        assert result[0].name == "get_weather"
        assert result[0].args == {"city": "北京"}

    def test_intermediate_data_empty_tool_uses(self):
        """Test IntermediateData with empty tool_uses."""
        data = IntermediateData(tool_uses=[])
        assert get_all_tool_calls(data) == []


class TestGetAllToolResponses:
    """Test suite for get_all_tool_responses."""

    def test_none_returns_empty(self):
        """Test None intermediate_data returns empty list."""
        assert get_all_tool_responses(None) == []

    def test_intermediate_data_with_tool_responses(self):
        """Test IntermediateData with tool_responses returns them."""
        fr = FunctionResponse.model_validate({"name": "get_weather", "response": {"temperature": 20}})
        data = IntermediateData(tool_responses=[fr])
        result = get_all_tool_responses(data)
        assert len(result) == 1
        assert result[0].name == "get_weather"


class TestEvalCase:
    """Test suite for EvalCase."""

    def test_eval_case_requires_conversation_or_scenario(self):
        """Test EvalCase validates exactly one of conversation or conversation_scenario."""
        with pytest.raises(ValueError):
            EvalCase(
                eval_id="c1",
                conversation=None,
                conversation_scenario=None,
                session_input=SessionInput(app_name="a", user_id="u", state={}),
            )
        with pytest.raises(ValueError):
            EvalCase(
                eval_id="c1",
                conversation=[],
                conversation_scenario=ConversationScenario(
                    starting_prompt="x",
                    conversation_plan="y",
                ),
                session_input=SessionInput(app_name="a", user_id="u", state={}),
            )
