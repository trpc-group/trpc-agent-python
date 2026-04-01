# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for delegation tools."""

import pytest
from trpc_agent_sdk.teams.core import DELEGATION_SIGNAL_MARKER
from trpc_agent_sdk.teams.core import DelegationSignal
from trpc_agent_sdk.teams.core import DELEGATE_TOOL_NAME
from trpc_agent_sdk.teams.core import create_delegate_to_member_tool
from trpc_agent_sdk.tools import FunctionTool


class TestDelegateToolName:
    """Tests for delegation tool name constant."""

    def test_tool_name_value(self):
        """Test that tool name has expected value."""
        assert DELEGATE_TOOL_NAME == "delegate_to_member"


class TestCreateDelegateToMemberTool:
    """Tests for create_delegate_to_member_tool factory function."""

    def test_returns_function_tool(self):
        """Test that factory returns a FunctionTool."""
        tool = create_delegate_to_member_tool(["researcher", "writer"])
        assert isinstance(tool, FunctionTool)

    def test_tool_has_correct_name(self):
        """Test that created tool has correct name."""
        tool = create_delegate_to_member_tool(["researcher"])
        # Get the underlying function name
        assert tool.func.__name__ == "delegate_to_member"

    def test_tool_docstring_includes_members(self):
        """Test that tool docstring includes member names."""
        members = ["researcher", "writer", "editor"]
        tool = create_delegate_to_member_tool(members)

        docstring = tool.func.__doc__
        for member in members:
            assert member in docstring

    def test_tool_docstring_with_single_member(self):
        """Test tool docstring with single member."""
        tool = create_delegate_to_member_tool(["only_member"])
        assert "only_member" in tool.func.__doc__


class TestDelegateToMemberToolExecution:
    """Tests for executing the delegate_to_member tool."""

    def test_returns_delegation_signal(self):
        """Test that tool execution returns DelegationSignal."""
        tool = create_delegate_to_member_tool(["researcher", "writer"])
        result = tool.func("researcher", "Find information")

        assert isinstance(result, DelegationSignal)

    def test_signal_has_correct_action(self):
        """Test that returned signal has correct action."""
        tool = create_delegate_to_member_tool(["researcher"])
        result = tool.func("researcher", "Task")

        assert result.action == "delegate_to_member"

    def test_signal_has_correct_member_name(self):
        """Test that returned signal has correct member name."""
        tool = create_delegate_to_member_tool(["researcher", "writer"])
        result = tool.func("writer", "Write article")

        assert result.member_name == "writer"

    def test_signal_has_correct_task(self):
        """Test that returned signal has correct task."""
        tool = create_delegate_to_member_tool(["researcher"])
        task = "Research the history of machine learning"
        result = tool.func("researcher", task)

        assert result.task == task

    def test_signal_has_marker(self):
        """Test that returned signal has the delegation marker."""
        tool = create_delegate_to_member_tool(["researcher"])
        result = tool.func("researcher", "Task")

        assert result.marker == DELEGATION_SIGNAL_MARKER


class TestDelegateToMemberToolWithVaryingInputs:
    """Tests for delegate_to_member tool with various inputs."""

    def test_empty_task(self):
        """Test delegation with empty task."""
        tool = create_delegate_to_member_tool(["researcher"])
        result = tool.func("researcher", "")

        assert result.task == ""
        assert result.member_name == "researcher"

    def test_long_task(self):
        """Test delegation with long task description."""
        tool = create_delegate_to_member_tool(["researcher"])
        long_task = "A" * 10000  # 10k character task
        result = tool.func("researcher", long_task)

        assert result.task == long_task

    def test_task_with_special_characters(self):
        """Test delegation with special characters in task."""
        tool = create_delegate_to_member_tool(["researcher"])
        special_task = "Find info about <tag> & 'quotes' \"double\" \n\t special"
        result = tool.func("researcher", special_task)

        assert result.task == special_task

    def test_task_with_unicode(self):
        """Test delegation with unicode characters in task."""
        tool = create_delegate_to_member_tool(["researcher"])
        unicode_task = "Recherche sur l'IA et les modeles"
        result = tool.func("researcher", unicode_task)

        assert result.task == unicode_task

    def test_member_name_not_in_list(self):
        """Test that tool works even with member name not in list."""
        # Note: The tool doesn't validate member names, TeamAgent does
        tool = create_delegate_to_member_tool(["researcher"])
        result = tool.func("unknown_member", "Task")

        # Should still return a valid signal
        assert result.member_name == "unknown_member"
        assert isinstance(result, DelegationSignal)


class TestDelegateToMemberToolSignalDetection:
    """Tests for detecting delegation signal from tool result."""

    def test_signal_is_detectable(self):
        """Test that returned signal is detectable."""
        tool = create_delegate_to_member_tool(["researcher"])
        result = tool.func("researcher", "Task")

        # Convert to dict and check detection
        result_dict = result.model_dump()
        assert DelegationSignal.is_delegation_signal(result_dict) is True

    def test_signal_roundtrip(self):
        """Test signal can be serialized and deserialized."""
        tool = create_delegate_to_member_tool(["researcher", "writer"])
        original = tool.func("writer", "Write an essay")

        # Serialize and deserialize
        data = original.model_dump()
        restored = DelegationSignal.from_response(data)

        assert restored.member_name == original.member_name
        assert restored.task == original.task
        assert restored.action == original.action


class TestCreateMultipleTools:
    """Tests for creating multiple delegation tools."""

    def test_different_member_lists(self):
        """Test creating tools with different member lists."""
        tool1 = create_delegate_to_member_tool(["member_alpha", "member_beta"])
        tool2 = create_delegate_to_member_tool(["member_gamma", "member_delta", "member_epsilon"])

        # Each tool should have its own docstring with their specific members
        assert "member_alpha" in tool1.func.__doc__
        assert "member_gamma" in tool2.func.__doc__
        # Members from one list should not be in other tool's docstring
        assert "member_gamma" not in tool1.func.__doc__
        assert "member_alpha" not in tool2.func.__doc__

    def test_tools_are_independent(self):
        """Test that created tools are independent instances."""
        tool1 = create_delegate_to_member_tool(["member1"])
        tool2 = create_delegate_to_member_tool(["member2"])

        # Results should be independent
        result1 = tool1.func("member1", "Task 1")
        result2 = tool2.func("member2", "Task 2")

        assert result1.member_name == "member1"
        assert result2.member_name == "member2"
