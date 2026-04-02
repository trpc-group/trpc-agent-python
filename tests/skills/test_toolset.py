# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from unittest.mock import Mock

import pytest
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.skills._registry import SKILL_REGISTRY
from trpc_agent_sdk.skills import SkillToolSet


class TestSkillToolSet:
    """Test suite for SkillToolSet class."""

    def setup_method(self):
        """Set up test fixtures before each test."""
        SKILL_REGISTRY.clear()
        self.mock_ctx = Mock(spec=InvocationContext)
        self.mock_ctx.agent_context = Mock()
        self.mock_ctx.agent_context.with_metadata = Mock(return_value=self.mock_ctx.agent_context)

    def teardown_method(self):
        """Clean up after each test."""
        SKILL_REGISTRY.clear()

    def test_init_defaults(self):
        """Test initialization with defaults."""
        toolset = SkillToolSet()

        assert toolset.name == "skill_toolset"
        assert toolset._repository is not None
        assert toolset._run_tool is not None
        assert len(toolset._tools) == 6  # skill_load, skill_list, skill_list_docs, skill_list_tools, skill_select_docs, skill_select_tools

    def test_init_with_paths(self):
        """Test initialization with paths."""
        paths = ["/path/to/skills1", "/path/to/skills2"]
        toolset = SkillToolSet(paths=paths)

        assert toolset._repository is not None

    def test_init_with_repository(self):
        """Test initialization with custom repository."""
        mock_repository = Mock()
        toolset = SkillToolSet(repository=mock_repository)

        assert toolset._repository == mock_repository

    def test_repository_property(self):
        """Test repository property."""
        mock_repository = Mock()
        toolset = SkillToolSet(repository=mock_repository)

        assert toolset.repository == mock_repository

    @pytest.mark.asyncio
    async def test_get_tools_with_registered_skills(self):
        """Test getting tools includes registered skills."""
        def test_skill():
            return "test"

        SKILL_REGISTRY.register("test-skill", test_skill)
        toolset = SkillToolSet()

        tools = await toolset.get_tools(self.mock_ctx)

        assert len(tools) >= 1
        # Should include run_tool and registered skills
        assert any(tool.name == "test-skill" or hasattr(tool, 'func') for tool in tools)

    @pytest.mark.asyncio
    async def test_get_tools_includes_builtin_tools(self):
        """Test getting tools includes builtin tools."""
        toolset = SkillToolSet()

        tools = await toolset.get_tools(self.mock_ctx)

        # Should include run_tool and builtin tools (skill_load, skill_list_docs, skill_select_docs)
        assert len(tools) >= 1

    @pytest.mark.asyncio
    async def test_get_tools_sets_metadata(self):
        """Test that get_tools sets metadata in agent context."""
        toolset = SkillToolSet()

        await toolset.get_tools(self.mock_ctx)

        # Verify metadata was set
        assert self.mock_ctx.agent_context.with_metadata.call_count >= 2

    @pytest.mark.asyncio
    async def test_get_tools_includes_run_tool(self):
        """Test that get_tools includes run_tool."""
        toolset = SkillToolSet()

        tools = await toolset.get_tools(self.mock_ctx)

        # Should include run_tool
        assert len(tools) >= 1

    @pytest.mark.asyncio
    async def test_get_tools_handles_exceptions(self):
        """Test that get_tools handles exceptions gracefully."""
        def failing_skill():
            raise Exception("Skill error")

        SKILL_REGISTRY.register("failing-skill", failing_skill)
        toolset = SkillToolSet()

        # Should not raise exception
        tools = await toolset.get_tools(self.mock_ctx)

        assert isinstance(tools, list)

    @pytest.mark.asyncio
    async def test_get_tools_with_tool_filter(self):
        """Test getting tools with tool filter."""
        def test_skill():
            return "test"

        SKILL_REGISTRY.register("test-skill", test_skill)
        tool_filter = ["test-skill"]
        toolset = SkillToolSet(tool_filter=tool_filter)

        tools = await toolset.get_tools(self.mock_ctx)

        assert isinstance(tools, list)

    @pytest.mark.asyncio
    async def test_get_tools_with_run_tool_kwargs(self):
        """Test getting tools with run_tool_kwargs."""
        run_tool_kwargs = {"timeout": 30}
        toolset = SkillToolSet(run_tool_kwargs=run_tool_kwargs)

        tools = await toolset.get_tools(self.mock_ctx)

        assert isinstance(tools, list)

    @pytest.mark.asyncio
    async def test_get_tools_empty_registry(self):
        """Test getting tools when registry is empty."""
        toolset = SkillToolSet()

        tools = await toolset.get_tools(self.mock_ctx)

        # Should still include builtin tools and run_tool
        assert len(tools) >= 1

