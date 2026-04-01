# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import json
from unittest.mock import AsyncMock
from unittest.mock import Mock
from unittest.mock import patch

import pytest
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.skills import SKILL_LOADED_STATE_KEY_PREFIX
from trpc_agent_sdk.skills import SKILL_TOOLS_STATE_KEY_PREFIX
from trpc_agent_sdk.skills import DynamicSkillToolSet
from trpc_agent_sdk.skills import Skill
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools import BaseToolSet


class MockTool(BaseTool):
    """Mock tool for testing."""

    def __init__(self, name: str):
        self._name = name
        self._description = f"Mock tool {name}"

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    async def _run_async_impl(self, **kwargs):
        """Implementation of the abstract method."""
        return f"Executed {self._name}"

    async def execute(self, **kwargs):
        return await self._run_async_impl(**kwargs)


class MockToolSet(BaseToolSet):
    """Mock toolset for testing."""

    def __init__(self, name: str, tools: list[BaseTool]):
        super().__init__(name=name)
        self._tools = tools

    async def get_tools(self, ctx=None):
        return self._tools


class TestDynamicSkillToolSet:
    """Test suite for DynamicSkillToolSet class."""

    def setup_method(self):
        """Set up test fixtures before each test."""
        self.mock_repository = Mock()
        self.mock_ctx = Mock(spec=InvocationContext)
        self.mock_ctx.session_state = {}
        self.mock_ctx.actions = Mock()
        self.mock_ctx.actions.state_delta = {}

    def test_init_with_no_tools(self):
        """Test initialization with no tools."""
        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=None,
            only_active_skills=True
        )

        assert toolset.name == "dynamic_skill_tools"
        assert toolset._skill_repository == self.mock_repository
        assert len(toolset._available_tools) == 0
        assert len(toolset._available_toolsets) == 0
        assert toolset._only_active_skills is True

    def test_init_with_tool_list(self):
        """Test initialization with list of tools."""
        tool1 = MockTool("tool1")
        tool2 = MockTool("tool2")

        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=[tool1, tool2]
        )

        assert len(toolset._available_tools) == 2
        assert "tool1" in toolset._available_tools
        assert "tool2" in toolset._available_tools

    def test_init_with_toolset_list(self):
        """Test initialization with list of toolsets."""
        tool1 = MockTool("tool1")
        tool2 = MockTool("tool2")
        mock_toolset = MockToolSet("test_toolset", [tool1, tool2])

        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=[mock_toolset]
        )

        assert len(toolset._available_toolsets) == 1
        assert toolset._available_toolsets[0] == mock_toolset

    def test_init_with_mixed_tools(self):
        """Test initialization with mixed tools and toolsets."""
        tool1 = MockTool("tool1")
        tool2 = MockTool("tool2")
        mock_toolset = MockToolSet("test_toolset", [tool2])

        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=[tool1, mock_toolset]
        )

        assert len(toolset._available_tools) == 1
        assert len(toolset._available_toolsets) == 1

    def test_find_tool_by_type_with_base_tool(self):
        """Test finding tool by type with BaseTool."""
        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=None
        )

        tool = MockTool("test_tool")
        result = toolset._find_tool_by_type(tool)

        assert result is True
        assert "test_tool" in toolset._available_tools

    def test_find_tool_by_type_with_base_toolset(self):
        """Test finding tool by type with BaseToolSet."""
        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=None
        )

        mock_toolset = MockToolSet("test_toolset", [])
        result = toolset._find_tool_by_type(mock_toolset)

        assert result is True
        assert len(toolset._available_toolsets) == 1

    def test_find_tool_by_type_with_invalid_type(self):
        """Test finding tool by type with invalid type."""
        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=None
        )

        result = toolset._find_tool_by_type("not_a_tool")

        assert result is False

    @pytest.mark.asyncio
    async def test_resolve_tool_from_available_tools(self):
        """Test resolving tool from available_tools."""
        tool = MockTool("tool1")
        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=[tool]
        )

        resolved = await toolset._resolve_tool("tool1", self.mock_ctx)

        assert resolved is not None
        assert resolved.name == "tool1"

    @pytest.mark.asyncio
    async def test_resolve_tool_from_cache(self):
        """Test resolving tool from cache."""
        tool = MockTool("tool1")
        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=[tool]
        )

        # First call should cache the tool
        resolved1 = await toolset._resolve_tool("tool1", self.mock_ctx)
        # Second call should use cache
        resolved2 = await toolset._resolve_tool("tool1", self.mock_ctx)

        assert resolved1 is resolved2

    @pytest.mark.asyncio
    async def test_resolve_tool_from_toolset(self):
        """Test resolving tool from available toolsets."""
        tool1 = MockTool("tool1")
        tool2 = MockTool("tool2")
        mock_toolset = MockToolSet("test_toolset", [tool1, tool2])

        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=[mock_toolset]
        )

        resolved = await toolset._resolve_tool("tool1", self.mock_ctx)

        assert resolved is not None
        assert resolved.name == "tool1"

    @pytest.mark.asyncio
    async def test_resolve_tool_not_found(self):
        """Test resolving tool that doesn't exist."""
        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=None
        )

        with patch('trpc_agent.skills._dynamic_toolset.get_tool', return_value=None):
            resolved = await toolset._resolve_tool("nonexistent", self.mock_ctx)

        assert resolved is None

    @pytest.mark.asyncio
    async def test_resolve_tool_from_global_registry(self):
        """Test resolving tool from global registry."""
        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=None
        )

        global_tool = MockTool("global_tool")
        with patch('trpc_agent.skills._dynamic_toolset.get_tool', return_value=global_tool):
            resolved = await toolset._resolve_tool("global_tool", self.mock_ctx)

        assert resolved is not None
        assert resolved.name == "global_tool"

    @pytest.mark.asyncio
    async def test_get_tools_no_loaded_skills(self):
        """Test get_tools with no loaded skills."""
        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=None
        )

        tools = await toolset.get_tools(self.mock_ctx)

        assert tools == []

    @pytest.mark.asyncio
    async def test_get_tools_with_active_skills(self):
        """Test get_tools with active skills."""
        tool1 = MockTool("tool1")
        tool2 = MockTool("tool2")

        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=[tool1, tool2],
            only_active_skills=True
        )

        # Set up state: skill is loaded and has tools selected
        self.mock_ctx.actions.state_delta = {
            f"{SKILL_LOADED_STATE_KEY_PREFIX}test-skill": "1",
            f"{SKILL_TOOLS_STATE_KEY_PREFIX}test-skill": json.dumps(["tool1", "tool2"])
        }

        tools = await toolset.get_tools(self.mock_ctx)

        assert len(tools) == 2
        assert any(t.name == "tool1" for t in tools)
        assert any(t.name == "tool2" for t in tools)

    @pytest.mark.asyncio
    async def test_get_tools_with_all_loaded_skills(self):
        """Test get_tools with only_active_skills=False."""
        tool1 = MockTool("tool1")

        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=[tool1],
            only_active_skills=False
        )

        # Set up state: skill is loaded in session state
        self.mock_ctx.session_state = {
            f"{SKILL_LOADED_STATE_KEY_PREFIX}test-skill": "1",
            f"{SKILL_TOOLS_STATE_KEY_PREFIX}test-skill": json.dumps(["tool1"])
        }

        tools = await toolset.get_tools(self.mock_ctx)

        assert len(tools) == 1
        assert tools[0].name == "tool1"

    @pytest.mark.asyncio
    async def test_get_tools_removes_duplicates(self):
        """Test that get_tools removes duplicate tools."""
        tool1 = MockTool("tool1")

        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=[tool1],
            only_active_skills=True
        )

        # Set up state: two skills requiring the same tool
        self.mock_ctx.actions.state_delta = {
            f"{SKILL_LOADED_STATE_KEY_PREFIX}skill1": "1",
            f"{SKILL_TOOLS_STATE_KEY_PREFIX}skill1": json.dumps(["tool1"]),
            f"{SKILL_LOADED_STATE_KEY_PREFIX}skill2": "1",
            f"{SKILL_TOOLS_STATE_KEY_PREFIX}skill2": json.dumps(["tool1"])
        }

        tools = await toolset.get_tools(self.mock_ctx)

        # Should only return one instance of tool1
        assert len(tools) == 1
        assert tools[0].name == "tool1"

    @pytest.mark.asyncio
    async def test_get_tools_with_unresolvable_tool(self):
        """Test get_tools when a tool cannot be resolved."""
        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=None
        )

        # Set up state: skill requires a tool that doesn't exist
        self.mock_ctx.actions.state_delta = {
            f"{SKILL_LOADED_STATE_KEY_PREFIX}test-skill": "1",
            f"{SKILL_TOOLS_STATE_KEY_PREFIX}test-skill": json.dumps(["nonexistent_tool"])
        }

        with patch('trpc_agent.skills._dynamic_toolset.get_tool', return_value=None):
            tools = await toolset.get_tools(self.mock_ctx)

        # Should return empty list (tool not found)
        assert tools == []

    def test_get_loaded_skills_from_state(self):
        """Test getting loaded skills from state."""
        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=None
        )

        # Set up state
        self.mock_ctx.session_state = {
            f"{SKILL_LOADED_STATE_KEY_PREFIX}skill1": "1",
            f"{SKILL_LOADED_STATE_KEY_PREFIX}skill2": "1",
        }
        self.mock_ctx.actions.state_delta = {
            f"{SKILL_LOADED_STATE_KEY_PREFIX}skill3": "1",
        }

        skills = toolset._get_loaded_skills_from_state(self.mock_ctx)

        assert len(skills) == 3
        assert "skill1" in skills
        assert "skill2" in skills
        assert "skill3" in skills

    def test_get_loaded_skills_from_state_empty(self):
        """Test getting loaded skills when none are loaded."""
        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=None
        )

        skills = toolset._get_loaded_skills_from_state(self.mock_ctx)

        assert skills == []

    def test_get_active_skills_from_delta_loaded(self):
        """Test getting active skills when they are just loaded."""
        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=None
        )

        # Set up state delta: skill just loaded
        self.mock_ctx.actions.state_delta = {
            f"{SKILL_LOADED_STATE_KEY_PREFIX}test-skill": "1"
        }

        skills = toolset._get_active_skills_from_delta(self.mock_ctx)

        assert len(skills) == 1
        assert "test-skill" in skills

    def test_get_active_skills_from_delta_tools_modified(self):
        """Test getting active skills when their tools are modified."""
        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=None
        )

        # Set up state delta: skill tools modified
        self.mock_ctx.actions.state_delta = {
            f"{SKILL_TOOLS_STATE_KEY_PREFIX}test-skill": json.dumps(["tool1"])
        }

        skills = toolset._get_active_skills_from_delta(self.mock_ctx)

        assert len(skills) == 1
        assert "test-skill" in skills

    def test_get_active_skills_from_delta_both(self):
        """Test getting active skills with both loaded and tools modified."""
        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=None
        )

        # Set up state delta: one skill loaded, another's tools modified
        self.mock_ctx.actions.state_delta = {
            f"{SKILL_LOADED_STATE_KEY_PREFIX}skill1": "1",
            f"{SKILL_TOOLS_STATE_KEY_PREFIX}skill2": json.dumps(["tool1"])
        }

        skills = toolset._get_active_skills_from_delta(self.mock_ctx)

        assert len(skills) == 2
        assert "skill1" in skills
        assert "skill2" in skills

    def test_get_active_skills_from_delta_empty(self):
        """Test getting active skills when none are active."""
        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=None
        )

        skills = toolset._get_active_skills_from_delta(self.mock_ctx)

        assert skills == []

    def test_get_tools_selection_json(self):
        """Test getting tools selection from JSON."""
        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=None
        )

        # Set up state
        self.mock_ctx.session_state = {
            f"{SKILL_TOOLS_STATE_KEY_PREFIX}test-skill": json.dumps(["tool1", "tool2"])
        }

        tools = toolset._get_tools_selection(self.mock_ctx, "test-skill")

        assert tools == ["tool1", "tool2"]

    def test_get_tools_selection_all(self):
        """Test getting tools selection with '*' (all tools)."""
        skill = Skill(tools=["tool1", "tool2", "tool3"])
        self.mock_repository.get.return_value = skill

        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=None
        )

        # Set up state with '*' to select all
        self.mock_ctx.session_state = {
            f"{SKILL_TOOLS_STATE_KEY_PREFIX}test-skill": "*"
        }

        tools = toolset._get_tools_selection(self.mock_ctx, "test-skill")

        assert tools == ["tool1", "tool2", "tool3"]

    def test_get_tools_selection_all_skill_not_found(self):
        """Test getting tools selection with '*' when skill not found."""
        self.mock_repository.get.return_value = None

        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=None
        )

        # Set up state with '*'
        self.mock_ctx.session_state = {
            f"{SKILL_TOOLS_STATE_KEY_PREFIX}test-skill": "*"
        }

        tools = toolset._get_tools_selection(self.mock_ctx, "test-skill")

        assert tools == []

    def test_get_tools_selection_not_found(self):
        """Test getting tools selection when not found."""
        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=None
        )

        tools = toolset._get_tools_selection(self.mock_ctx, "test-skill")

        assert tools == []

    def test_get_tools_selection_invalid_json(self):
        """Test getting tools selection with invalid JSON."""
        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=None
        )

        # Set up state with invalid JSON
        self.mock_ctx.session_state = {
            f"{SKILL_TOOLS_STATE_KEY_PREFIX}test-skill": "invalid json"
        }

        tools = toolset._get_tools_selection(self.mock_ctx, "test-skill")

        assert tools == []

    def test_get_tools_selection_bytes_value(self):
        """Test getting tools selection when value is bytes."""
        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=None
        )

        # Set up state with bytes value
        self.mock_ctx.session_state = {
            f"{SKILL_TOOLS_STATE_KEY_PREFIX}test-skill": json.dumps(["tool1"]).encode('utf-8')
        }

        tools = toolset._get_tools_selection(self.mock_ctx, "test-skill")

        assert tools == ["tool1"]

    def test_get_tools_selection_from_state_delta(self):
        """Test that tools selection prefers state_delta over session_state."""
        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=None
        )

        # Set up both session_state and state_delta
        self.mock_ctx.session_state = {
            f"{SKILL_TOOLS_STATE_KEY_PREFIX}test-skill": json.dumps(["old_tool"])
        }
        self.mock_ctx.actions.state_delta = {
            f"{SKILL_TOOLS_STATE_KEY_PREFIX}test-skill": json.dumps(["new_tool"])
        }

        tools = toolset._get_tools_selection(self.mock_ctx, "test-skill")

        assert tools == ["new_tool"]

    @pytest.mark.asyncio
    async def test_get_tools_fallback_to_all_loaded(self):
        """Test that get_tools falls back to all loaded skills when no active skills."""
        tool1 = MockTool("tool1")

        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=[tool1],
            only_active_skills=True
        )

        # Set up state: skill loaded in previous turn (session_state only)
        self.mock_ctx.session_state = {
            f"{SKILL_LOADED_STATE_KEY_PREFIX}test-skill": "1",
            f"{SKILL_TOOLS_STATE_KEY_PREFIX}test-skill": json.dumps(["tool1"])
        }
        # No state_delta (no active skills in current turn)
        self.mock_ctx.actions.state_delta = {}

        tools = await toolset.get_tools(self.mock_ctx)

        # Should fallback to all loaded skills
        assert len(tools) == 1
        assert tools[0].name == "tool1"

    @pytest.mark.asyncio
    async def test_get_tools_toolset_error_handling(self):
        """Test that get_tools handles toolset errors gracefully."""
        # Create a toolset that raises an error
        error_toolset = Mock(spec=BaseToolSet)
        error_toolset.name = "error_toolset"
        error_toolset.get_tools = AsyncMock(side_effect=Exception("Test error"))

        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=[error_toolset]
        )

        # Set up state
        self.mock_ctx.actions.state_delta = {
            f"{SKILL_LOADED_STATE_KEY_PREFIX}test-skill": "1",
            f"{SKILL_TOOLS_STATE_KEY_PREFIX}test-skill": json.dumps(["tool1"])
        }

        with patch('trpc_agent.skills._dynamic_toolset.get_tool', return_value=None):
            tools = await toolset.get_tools(self.mock_ctx)

        # Should return empty list (no tools resolved)
        assert tools == []

    @pytest.mark.asyncio
    async def test_get_tools_skill_repository_error(self):
        """Test that get_tools handles skill repository errors gracefully."""
        self.mock_repository.get.side_effect = Exception("Repository error")

        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=None
        )

        # Set up state with '*' (requires repository access)
        self.mock_ctx.session_state = {
            f"{SKILL_LOADED_STATE_KEY_PREFIX}test-skill": "1",
            f"{SKILL_TOOLS_STATE_KEY_PREFIX}test-skill": "*"
        }

        tools = await toolset.get_tools(self.mock_ctx)

        # Should handle error and return empty list
        assert tools == []

    def test_init_with_string_tools(self):
        """Test initialization with string tool names."""
        with patch('trpc_agent.skills._dynamic_toolset.get_tool') as mock_get_tool, \
             patch('trpc_agent.skills._dynamic_toolset.get_tool_set') as mock_get_toolset:

            tool1 = MockTool("tool1")
            mock_get_tool.return_value = tool1
            mock_get_toolset.return_value = None

            toolset = DynamicSkillToolSet(
                skill_repository=self.mock_repository,
                available_tools=["tool1"]
            )

            assert len(toolset._available_tools) == 1
            assert "tool1" in toolset._available_tools
            mock_get_tool.assert_called_once_with("tool1")

    def test_find_tool_by_name_finds_tool(self):
        """Test finding tool by name successfully."""
        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=None
        )

        tool = MockTool("test_tool")
        with patch('trpc_agent.skills._dynamic_toolset.get_tool', return_value=tool):
            result = toolset._find_tool_by_name("test_tool")

        assert result is True
        assert "test_tool" in toolset._available_tools

    def test_find_tool_by_name_finds_toolset(self):
        """Test finding toolset by name successfully."""
        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=None
        )

        mock_toolset = MockToolSet("test_toolset", [])
        with patch('trpc_agent.skills._dynamic_toolset.get_tool', return_value=None), \
             patch('trpc_agent.skills._dynamic_toolset.get_tool_set', return_value=mock_toolset):
            result = toolset._find_tool_by_name("test_toolset")

        assert result is True
        assert len(toolset._available_toolsets) == 1

    def test_find_tool_by_name_not_found(self):
        """Test finding tool by name when it doesn't exist."""
        toolset = DynamicSkillToolSet(
            skill_repository=self.mock_repository,
            available_tools=None
        )

        with patch('trpc_agent.skills._dynamic_toolset.get_tool', return_value=None), \
             patch('trpc_agent.skills._dynamic_toolset.get_tool_set', return_value=None):
            result = toolset._find_tool_by_name("nonexistent")

        assert result is False

