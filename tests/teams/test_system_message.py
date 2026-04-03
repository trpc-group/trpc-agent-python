# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for system message generation."""

from unittest.mock import Mock

from trpc_agent_sdk.teams.core import generate_team_leader_system_message
from trpc_agent_sdk.teams.core import get_member_info_list


class TestGenerateTeamLeaderSystemMessage:
    """Tests for generate_team_leader_system_message function."""

    def test_basic_generation(self):
        """Test basic system message generation."""
        message = generate_team_leader_system_message(
            team_name="test_team",
            team_instruction="You are a helpful team",
            members=[{
                "name": "researcher",
                "description": "Does research"
            }],
        )

        assert "test_team" in message
        assert "You are a helpful team" in message
        assert "researcher" in message
        assert "Does research" in message

    def test_team_name_in_message(self):
        """Test that team name appears in leader role statement."""
        message = generate_team_leader_system_message(
            team_name="content_team",
            team_instruction="",
            members=[],
        )

        assert "leader of team 'content_team'" in message

    def test_empty_instruction(self):
        """Test generation with empty instruction."""
        message = generate_team_leader_system_message(
            team_name="team",
            team_instruction="",
            members=[{
                "name": "member",
                "description": "desc"
            }],
        )

        # Should still work without instruction
        assert "team" in message
        assert "member" in message

    def test_multiple_members(self):
        """Test generation with multiple members."""
        members = [
            {
                "name": "researcher",
                "description": "Researches topics"
            },
            {
                "name": "writer",
                "description": "Writes content"
            },
            {
                "name": "editor",
                "description": "Edits documents"
            },
        ]
        message = generate_team_leader_system_message(
            team_name="content_team",
            team_instruction="Create content",
            members=members,
        )

        for member in members:
            assert member["name"] in message
            assert member["description"] in message

    def test_member_numbering(self):
        """Test that members are numbered."""
        members = [
            {
                "name": "first",
                "description": "desc1"
            },
            {
                "name": "second",
                "description": "desc2"
            },
        ]
        message = generate_team_leader_system_message(
            team_name="team",
            team_instruction="",
            members=members,
        )

        assert "1. first" in message
        assert "2. second" in message

    def test_delegation_instructions_included(self):
        """Test that delegation instructions are included."""
        message = generate_team_leader_system_message(
            team_name="team",
            team_instruction="",
            members=[{
                "name": "member",
                "description": "desc"
            }],
        )

        assert "delegate_to_member" in message
        assert "Delegation Instructions" in message

    def test_team_members_section(self):
        """Test that Team Members section is included."""
        message = generate_team_leader_system_message(
            team_name="team",
            team_instruction="",
            members=[{
                "name": "member",
                "description": "desc"
            }],
        )

        assert "## Team Members" in message

    def test_empty_members_list(self):
        """Test generation with no members."""
        message = generate_team_leader_system_message(
            team_name="team",
            team_instruction="Do stuff",
            members=[],
        )

        # Should still generate basic message
        assert "team" in message
        assert "Team Members" in message

    def test_member_missing_description(self):
        """Test member with missing description uses default."""
        members = [{"name": "researcher"}]  # No description
        message = generate_team_leader_system_message(
            team_name="team",
            team_instruction="",
            members=members,
        )

        assert "researcher" in message
        assert "No description provided" in message

    def test_member_missing_name(self):
        """Test member with missing name uses default."""
        members = [{"description": "Does research"}]  # No name
        message = generate_team_leader_system_message(
            team_name="team",
            team_instruction="",
            members=members,
        )

        # Should use default name
        assert "member_1" in message
        assert "Does research" in message


class TestGetMemberInfoList:
    """Tests for get_member_info_list function."""

    def test_extract_basic_info(self):
        """Test extracting basic member information."""
        mock_member = Mock()
        mock_member.name = "researcher"
        mock_member.description = "Researches topics"
        mock_member.tools = None

        result = get_member_info_list([mock_member])

        assert len(result) == 1
        assert result[0]["name"] == "researcher"
        assert result[0]["description"] == "Researches topics"

    def test_extract_multiple_members(self):
        """Test extracting info from multiple members."""
        member1 = Mock()
        member1.name = "researcher"
        member1.description = "Researches"
        member1.tools = None

        member2 = Mock()
        member2.name = "writer"
        member2.description = "Writes"
        member2.tools = None

        result = get_member_info_list([member1, member2])

        assert len(result) == 2
        assert result[0]["name"] == "researcher"
        assert result[1]["name"] == "writer"

    def test_missing_description(self):
        """Test member without description attribute."""
        mock_member = Mock(spec=["name"])  # Only name attribute
        mock_member.name = "researcher"

        result = get_member_info_list([mock_member])

        assert result[0]["description"] == "No description"

    def test_empty_description(self):
        """Test member with empty description."""
        mock_member = Mock()
        mock_member.name = "researcher"
        mock_member.description = ""
        mock_member.tools = None

        result = get_member_info_list([mock_member])

        assert result[0]["description"] == "No description"

    def test_none_description(self):
        """Test member with None description."""
        mock_member = Mock()
        mock_member.name = "researcher"
        mock_member.description = None
        mock_member.tools = None

        result = get_member_info_list([mock_member])

        assert result[0]["description"] == "No description"

    def test_extract_tool_names(self):
        """Test extracting tool names from member."""
        mock_tool1 = Mock()
        mock_tool1.name = "search_tool"

        mock_tool2 = Mock()
        mock_tool2.name = "calculate_tool"

        mock_member = Mock()
        mock_member.name = "researcher"
        mock_member.description = "Researches"
        mock_member.tools = [mock_tool1, mock_tool2]

        result = get_member_info_list([mock_member])

        assert "tools" in result[0]
        assert "search_tool" in result[0]["tools"]
        assert "calculate_tool" in result[0]["tools"]

    def test_no_tools(self):
        """Test member without tools."""
        mock_member = Mock()
        mock_member.name = "researcher"
        mock_member.description = "Researches"
        mock_member.tools = []

        result = get_member_info_list([mock_member])

        # Should not have tools key when empty
        assert "tools" not in result[0]

    def test_tool_without_name_attribute(self):
        """Test tool that doesn't have name attribute."""
        mock_tool = Mock(spec=[])  # No name attribute

        mock_member = Mock()
        mock_member.name = "researcher"
        mock_member.description = "Researches"
        mock_member.tools = [mock_tool]

        result = get_member_info_list([mock_member])

        # Should have empty tools list (tool name couldn't be extracted)
        assert result[0].get("tools", []) == []

    def test_empty_member_list(self):
        """Test with empty member list."""
        result = get_member_info_list([])
        assert result == []

    def test_preserves_order(self):
        """Test that member order is preserved."""
        members = []
        for i in range(5):
            m = Mock()
            m.name = f"member_{i}"
            m.description = f"desc_{i}"
            m.tools = None
            members.append(m)

        result = get_member_info_list(members)

        for i in range(5):
            assert result[i]["name"] == f"member_{i}"


class TestSystemMessageContent:
    """Tests for specific content in system messages."""

    def test_coordinator_role_description(self):
        """Test that coordinator role is described."""
        message = generate_team_leader_system_message(
            team_name="team",
            team_instruction="",
            members=[{
                "name": "m",
                "description": "d"
            }],
        )

        assert "coordinator" in message.lower()

    def test_synthesize_instruction(self):
        """Test that instruction to synthesize responses is included."""
        message = generate_team_leader_system_message(
            team_name="team",
            team_instruction="",
            members=[{
                "name": "m",
                "description": "d"
            }],
        )

        assert "synthesize" in message.lower() or "final response" in message.lower()

    def test_tips_section(self):
        """Test that tips section is included."""
        message = generate_team_leader_system_message(
            team_name="team",
            team_instruction="",
            members=[{
                "name": "m",
                "description": "d"
            }],
        )

        assert "Tips" in message or "tips" in message.lower()

    def test_analyze_request_instruction(self):
        """Test that instruction to analyze request is included."""
        message = generate_team_leader_system_message(
            team_name="team",
            team_instruction="",
            members=[{
                "name": "m",
                "description": "d"
            }],
        )

        assert "analyze" in message.lower()
