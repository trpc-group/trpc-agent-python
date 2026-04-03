# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for member message filters."""

import pytest
from trpc_agent_sdk.teams.core import keep_all_member_message
from trpc_agent_sdk.teams.core import keep_last_member_message
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


class TestKeepAllMemberMessage:
    """Tests for keep_all_member_message filter."""

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        """Test with empty message list."""
        result = await keep_all_member_message([])
        assert result == ""

    @pytest.mark.asyncio
    async def test_single_text_message(self):
        """Test with single text message."""
        content = Content(role="model", parts=[Part.from_text(text="Hello world")])
        result = await keep_all_member_message([content])
        assert result == "Hello world"

    @pytest.mark.asyncio
    async def test_multiple_text_messages(self):
        """Test with multiple text messages."""
        messages = [
            Content(role="model", parts=[Part.from_text(text="First message")]),
            Content(role="model", parts=[Part.from_text(text="Second message")]),
        ]
        result = await keep_all_member_message(messages)
        assert "First message" in result
        assert "Second message" in result

    @pytest.mark.asyncio
    async def test_multiple_parts_in_message(self):
        """Test message with multiple parts."""
        content = Content(
            role="model",
            parts=[
                Part.from_text(text="Part 1"),
                Part.from_text(text="Part 2"),
            ],
        )
        result = await keep_all_member_message([content])
        assert "Part 1" in result
        assert "Part 2" in result

    @pytest.mark.asyncio
    async def test_skips_thought_content(self):
        """Test that thought content is skipped."""
        content = Content(
            role="model",
            parts=[
                Part(text="Visible text", thought=False),
                Part(text="Hidden thought", thought=True),
            ],
        )
        result = await keep_all_member_message([content])
        assert "Visible text" in result
        assert "Hidden thought" not in result

    @pytest.mark.asyncio
    async def test_includes_function_call_as_text(self):
        """Test that function calls are converted to text."""
        from trpc_agent_sdk.types import FunctionCall

        function_call = FunctionCall(name="search", args={"query": "test"})
        content = Content(
            role="model",
            parts=[Part(function_call=function_call)],
        )
        result = await keep_all_member_message([content])
        assert "[Tool Call:" in result
        assert "search" in result

    @pytest.mark.asyncio
    async def test_includes_function_response_as_text(self):
        """Test that function responses are converted to text."""
        from trpc_agent_sdk.types import FunctionResponse

        function_response = FunctionResponse(
            name="search",
            response={"result": "found"},
            id="123",
        )
        content = Content(
            role="model",
            parts=[Part(function_response=function_response)],
        )
        result = await keep_all_member_message([content])
        assert "[Tool Result:" in result

    @pytest.mark.asyncio
    async def test_none_content_in_list(self):
        """Test handling of None content in list."""
        messages = [
            None,
            Content(role="model", parts=[Part.from_text(text="Valid message")]),
        ]
        result = await keep_all_member_message(messages)
        assert "Valid message" in result

    @pytest.mark.asyncio
    async def test_content_with_no_parts(self):
        """Test content with no parts."""
        content = Content(role="model", parts=[])
        result = await keep_all_member_message([content])
        assert result == ""

    @pytest.mark.asyncio
    async def test_content_with_none_parts(self):
        """Test content with None parts."""
        content = Content(role="model", parts=None)
        result = await keep_all_member_message([content])
        assert result == ""

    @pytest.mark.asyncio
    async def test_messages_joined_with_newlines(self):
        """Test that messages are joined with newlines."""
        messages = [
            Content(role="model", parts=[Part.from_text(text="Line 1")]),
            Content(role="model", parts=[Part.from_text(text="Line 2")]),
        ]
        result = await keep_all_member_message(messages)
        assert "\n" in result


class TestKeepLastMemberMessage:
    """Tests for keep_last_member_message filter."""

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        """Test with empty message list."""
        result = await keep_last_member_message([])
        assert result == ""

    @pytest.mark.asyncio
    async def test_single_text_message(self):
        """Test with single text message."""
        content = Content(role="model", parts=[Part.from_text(text="Only message")])
        result = await keep_last_member_message([content])
        assert result == "Only message"

    @pytest.mark.asyncio
    async def test_returns_last_text_message(self):
        """Test that only the last text message is returned."""
        messages = [
            Content(role="model", parts=[Part.from_text(text="First message")]),
            Content(role="model", parts=[Part.from_text(text="Second message")]),
            Content(role="model", parts=[Part.from_text(text="Last message")]),
        ]
        result = await keep_last_member_message(messages)
        assert result == "Last message"
        assert "First message" not in result
        assert "Second message" not in result

    @pytest.mark.asyncio
    async def test_skips_tool_only_messages(self):
        """Test that it finds last message with actual text, skipping tool-only."""
        from trpc_agent_sdk.types import FunctionCall, FunctionResponse

        messages = [
            Content(role="model", parts=[Part.from_text(text="Real response")]),
            Content(
                role="model",
                parts=[Part(function_call=FunctionCall(name="tool", args={}))],
            ),
            Content(
                role="model",
                parts=[Part(function_response=FunctionResponse(name="tool", response={}, id="1"))],
            ),
        ]
        result = await keep_last_member_message(messages)
        # Should return the text message, not the tool messages
        assert result == "Real response"

    @pytest.mark.asyncio
    async def test_skips_thought_content(self):
        """Test that thought content is skipped."""
        content = Content(
            role="model",
            parts=[
                Part(text="Visible", thought=False),
                Part(text="Hidden thought", thought=True),
            ],
        )
        result = await keep_last_member_message([content])
        assert "Visible" in result
        assert "Hidden thought" not in result

    @pytest.mark.asyncio
    async def test_multiple_parts_in_last_message(self):
        """Test last message with multiple text parts."""
        messages = [
            Content(role="model", parts=[Part.from_text(text="Ignored")]),
            Content(
                role="model",
                parts=[
                    Part.from_text(text="Part A"),
                    Part.from_text(text="Part B"),
                ],
            ),
        ]
        result = await keep_last_member_message(messages)
        assert "Part A" in result
        assert "Part B" in result
        assert "Ignored" not in result

    @pytest.mark.asyncio
    async def test_none_content_in_list(self):
        """Test handling of None content in list."""
        messages = [
            Content(role="model", parts=[Part.from_text(text="Valid")]),
            None,
        ]
        result = await keep_last_member_message(messages)
        # Should fall back to the valid message
        assert result == "Valid"

    @pytest.mark.asyncio
    async def test_content_with_no_parts(self):
        """Test content with empty parts."""
        messages = [
            Content(role="model", parts=[Part.from_text(text="First")]),
            Content(role="model", parts=[]),
        ]
        result = await keep_last_member_message(messages)
        # Should fall back to the first message
        assert result == "First"

    @pytest.mark.asyncio
    async def test_all_tool_messages_returns_empty(self):
        """Test when all messages are tool-only, returns empty."""
        from trpc_agent_sdk.types import FunctionCall

        messages = [
            Content(
                role="model",
                parts=[Part(function_call=FunctionCall(name="tool1", args={}))],
            ),
            Content(
                role="model",
                parts=[Part(function_call=FunctionCall(name="tool2", args={}))],
            ),
        ]
        result = await keep_last_member_message(messages)
        assert result == ""

    @pytest.mark.asyncio
    async def test_text_parts_joined(self):
        """Test that multiple text parts in last message are joined."""
        content = Content(
            role="model",
            parts=[
                Part.from_text(text="Line 1"),
                Part.from_text(text="Line 2"),
            ],
        )
        result = await keep_last_member_message([content])
        assert "\n" in result
        assert "Line 1" in result
        assert "Line 2" in result


class TestFilterComparison:
    """Tests comparing keep_all vs keep_last filters."""

    @pytest.mark.asyncio
    async def test_all_vs_last_different_results(self):
        """Test that the two filters give different results."""
        messages = [
            Content(role="model", parts=[Part.from_text(text="First")]),
            Content(role="model", parts=[Part.from_text(text="Second")]),
            Content(role="model", parts=[Part.from_text(text="Third")]),
        ]

        all_result = await keep_all_member_message(messages)
        last_result = await keep_last_member_message(messages)

        # All should contain all three
        assert "First" in all_result
        assert "Second" in all_result
        assert "Third" in all_result

        # Last should only contain the last one
        assert last_result == "Third"
        assert "First" not in last_result
        assert "Second" not in last_result

    @pytest.mark.asyncio
    async def test_all_vs_last_same_for_single_message(self):
        """Test that both filters give same result for single message."""
        messages = [Content(role="model", parts=[Part.from_text(text="Only one")])]

        all_result = await keep_all_member_message(messages)
        last_result = await keep_last_member_message(messages)

        assert all_result == last_result == "Only one"
