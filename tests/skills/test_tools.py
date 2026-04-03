# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import json
from unittest.mock import Mock

import pytest
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.skills.tools import SkillSelectDocsResult
from trpc_agent_sdk.skills.tools import SkillSelectToolsResult
from trpc_agent_sdk.skills import skill_list
from trpc_agent_sdk.skills import skill_list_docs
from trpc_agent_sdk.skills import skill_list_tools
from trpc_agent_sdk.skills import skill_load
from trpc_agent_sdk.skills import skill_select_docs
from trpc_agent_sdk.skills import skill_select_tools
from trpc_agent_sdk.skills.tools._skill_load import _set_state_delta_for_skill_load
from trpc_agent_sdk.skills.tools._skill_load import _set_state_delta_for_skill_tools
from trpc_agent_sdk.skills import Skill
from trpc_agent_sdk.skills import SkillResource


class TestSkillList:
    """Test suite for skill_list function."""

    def test_skill_list_success(self):
        """Test listing all skills."""
        mock_repository = Mock()
        mock_repository.skill_list.return_value = ["skill1", "skill2", "skill3"]

        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.agent_context = Mock()
        mock_ctx.agent_context.get_metadata = Mock(return_value=mock_repository)

        result = skill_list(mock_ctx)

        assert len(result) == 3
        assert "skill1" in result
        assert "skill2" in result
        assert "skill3" in result

    def test_skill_list_empty(self):
        """Test listing skills when none exist."""
        mock_repository = Mock()
        mock_repository.skill_list.return_value = []

        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.agent_context = Mock()
        mock_ctx.agent_context.get_metadata = Mock(return_value=mock_repository)

        result = skill_list(mock_ctx)

        assert result == []

    def test_skill_list_repository_not_found(self):
        """Test listing skills when repository not found."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.agent_context = Mock()
        mock_ctx.agent_context.get_metadata = Mock(return_value=None)

        with pytest.raises(ValueError, match="repository not found"):
            skill_list(mock_ctx)


class TestSkillListDocs:
    """Test suite for skill_list_docs function."""

    def test_skill_list_docs_success(self):
        """Test listing docs for a skill."""
        mock_repository = Mock()
        skill = Skill(
            resources=[
                SkillResource(path="doc1.md", content="content1"),
                SkillResource(path="doc2.md", content="content2"),
            ]
        )
        mock_repository.get.return_value = skill

        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.agent_context = Mock()
        mock_ctx.agent_context.get_metadata = Mock(return_value=mock_repository)

        result = skill_list_docs(mock_ctx, "test-skill")

        assert len(result["docs"]) == 2
        assert "doc1.md" in result["docs"]
        assert "doc2.md" in result["docs"]

    def test_skill_list_docs_no_resources(self):
        """Test listing docs for skill with no resources."""
        mock_repository = Mock()
        skill = Skill(resources=[])
        mock_repository.get.return_value = skill

        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.agent_context = Mock()
        mock_ctx.agent_context.get_metadata = Mock(return_value=mock_repository)

        result = skill_list_docs(mock_ctx, "test-skill")

        assert result["docs"] == []

    def test_skill_list_docs_skill_not_found(self):
        """Test listing docs for nonexistent skill."""
        mock_repository = Mock()
        mock_repository.get.return_value = None

        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.agent_context = Mock()
        mock_ctx.agent_context.get_metadata = Mock(return_value=mock_repository)

        result = skill_list_docs(mock_ctx, "nonexistent-skill")

        assert result["docs"] == []

    def test_skill_list_docs_repository_not_found(self):
        """Test listing docs when repository not found."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.agent_context = Mock()
        mock_ctx.agent_context.get_metadata = Mock(return_value=None)

        with pytest.raises(ValueError, match="repository not found"):
            skill_list_docs(mock_ctx, "test-skill")


class TestSkillListTools:
    """Test suite for skill_list_tools function."""

    def test_skill_list_tools_success(self):
        """Test listing tools for a skill."""
        mock_repository = Mock()
        skill = Skill(tools=["tool1", "tool2", "tool3"])
        mock_repository.get.return_value = skill

        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.agent_context = Mock()
        mock_ctx.agent_context.get_metadata = Mock(return_value=mock_repository)

        result = skill_list_tools(mock_ctx, "test-skill")

        assert len(result["tools"]) == 3
        assert "tool1" in result["tools"]
        assert "tool2" in result["tools"]
        assert "tool3" in result["tools"]

    def test_skill_list_tools_no_tools(self):
        """Test listing tools for skill with no tools."""
        mock_repository = Mock()
        skill = Skill(tools=[])
        mock_repository.get.return_value = skill

        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.agent_context = Mock()
        mock_ctx.agent_context.get_metadata = Mock(return_value=mock_repository)

        result = skill_list_tools(mock_ctx, "test-skill")

        assert result["tools"] == []

    def test_skill_list_tools_skill_not_found(self):
        """Test listing tools for nonexistent skill."""
        mock_repository = Mock()
        mock_repository.get.return_value = None

        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.agent_context = Mock()
        mock_ctx.agent_context.get_metadata = Mock(return_value=mock_repository)

        result = skill_list_tools(mock_ctx, "nonexistent-skill")

        assert result["tools"] == []

    def test_skill_list_tools_repository_not_found(self):
        """Test listing tools when repository not found."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.agent_context = Mock()
        mock_ctx.agent_context.get_metadata = Mock(return_value=None)

        with pytest.raises(ValueError, match="repository not found"):
            skill_list_tools(mock_ctx, "test-skill")


class TestSkillLoad:
    """Test suite for skill_load function."""

    def test_skill_load_success(self):
        """Test loading a skill."""
        mock_repository = Mock()
        skill = Skill(body="skill body", tools=[])
        mock_repository.get.return_value = skill

        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.agent_context = Mock()
        mock_ctx.agent_context.get_metadata = Mock(return_value=mock_repository)
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = skill_load(mock_ctx, "test-skill")

        assert "loaded" in result
        assert "test-skill" in result

    def test_skill_load_with_tools(self):
        """Test loading a skill with tools."""
        mock_repository = Mock()
        skill = Skill(body="skill body", tools=["tool1", "tool2"])
        mock_repository.get.return_value = skill

        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.agent_context = Mock()
        mock_ctx.agent_context.get_metadata = Mock(return_value=mock_repository)
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = skill_load(mock_ctx, "test-skill")

        assert "loaded" in result
        # Check that tools state was set
        tools_key = "temp:skill:tools:test-skill"
        assert tools_key in mock_ctx.actions.state_delta
        assert json.loads(mock_ctx.actions.state_delta[tools_key]) == ["tool1", "tool2"]

    def test_skill_load_with_docs(self):
        """Test loading a skill with specific docs."""
        mock_repository = Mock()
        skill = Skill(body="skill body", tools=[])
        mock_repository.get.return_value = skill

        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.agent_context = Mock()
        mock_ctx.agent_context.get_metadata = Mock(return_value=mock_repository)
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = skill_load(mock_ctx, "test-skill", docs=["doc1.md"])

        assert "loaded" in result
        docs_key = "temp:skill:docs:test-skill"
        assert docs_key in mock_ctx.actions.state_delta

    def test_skill_load_with_include_all_docs(self):
        """Test loading a skill with include_all_docs=True."""
        mock_repository = Mock()
        skill = Skill(body="skill body", tools=[])
        mock_repository.get.return_value = skill

        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.agent_context = Mock()
        mock_ctx.agent_context.get_metadata = Mock(return_value=mock_repository)
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = skill_load(mock_ctx, "test-skill", include_all_docs=True)

        assert "loaded" in result
        assert mock_ctx.actions.state_delta.get("temp:skill:docs:test-skill") == '*'

    def test_skill_load_skill_not_found(self):
        """Test loading nonexistent skill."""
        mock_repository = Mock()
        mock_repository.get.return_value = None

        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.agent_context = Mock()
        mock_ctx.agent_context.get_metadata = Mock(return_value=mock_repository)

        result = skill_load(mock_ctx, "nonexistent-skill")

        assert "not found" in result

    def test_skill_load_repository_not_found(self):
        """Test loading skill when repository not found."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.agent_context = Mock()
        mock_ctx.agent_context.get_metadata = Mock(return_value=None)

        with pytest.raises(ValueError, match="repository not found"):
            skill_load(mock_ctx, "test-skill")


class TestSkillSelectDocs:
    """Test suite for skill_select_docs function."""

    def test_skill_select_docs_replace_mode(self):
        """Test selecting docs with replace mode."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {}
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = skill_select_docs(mock_ctx, "test-skill", docs=["doc1.md"], mode="replace")

        assert isinstance(result, SkillSelectDocsResult)
        assert result.skill == "test-skill"
        assert result.mode == "replace"
        assert "doc1.md" in result.selected_docs

    def test_skill_select_docs_add_mode(self):
        """Test selecting docs with add mode."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {
            "temp:skill:docs:test-skill": json.dumps(["doc1.md"])
        }
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = skill_select_docs(mock_ctx, "test-skill", docs=["doc2.md"], mode="add")

        assert result.mode == "add"
        assert "doc1.md" in result.selected_docs
        assert "doc2.md" in result.selected_docs

    def test_skill_select_docs_clear_mode(self):
        """Test selecting docs with clear mode."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {
            "temp:skill:docs:test-skill": json.dumps(["doc1.md"])
        }
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = skill_select_docs(mock_ctx, "test-skill", mode="clear")

        assert result.mode == "clear"
        assert result.include_all_docs is False
        assert result.selected_docs == []

    def test_skill_select_docs_with_include_all(self):
        """Test selecting docs with include_all_docs=True."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {}
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = skill_select_docs(mock_ctx, "test-skill", include_all_docs=True, mode="replace")

        assert result.include_all_docs is True
        assert result.selected_docs == []

    def test_skill_select_docs_invalid_mode(self):
        """Test selecting docs with invalid mode defaults to replace."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {}
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = skill_select_docs(mock_ctx, "test-skill", docs=["doc1.md"], mode="invalid")

        assert result.mode == "replace"

    def test_skill_select_docs_previous_all_docs(self):
        """Test selecting docs when previous state has all docs."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {
            "temp:skill:docs:test-skill": '*'
        }
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = skill_select_docs(mock_ctx, "test-skill", docs=["doc1.md"], mode="add")

        assert result.include_all_docs is True


class TestSkillSelectTools:
    """Test suite for skill_select_tools function."""

    def test_skill_select_tools_replace_mode(self):
        """Test selecting tools with replace mode."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {}
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = skill_select_tools(mock_ctx, "test-skill", tools=["tool1"], mode="replace")

        assert isinstance(result, SkillSelectToolsResult)
        assert result.skill == "test-skill"
        assert result.mode == "replace"
        assert "tool1" in result.selected_tools

    def test_skill_select_tools_add_mode(self):
        """Test selecting tools with add mode."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {
            "temp:skill:tools:test-skill": json.dumps(["tool1"])
        }
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = skill_select_tools(mock_ctx, "test-skill", tools=["tool2"], mode="add")

        assert result.mode == "add"
        assert "tool1" in result.selected_tools
        assert "tool2" in result.selected_tools

    def test_skill_select_tools_clear_mode(self):
        """Test selecting tools with clear mode."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {
            "temp:skill:tools:test-skill": json.dumps(["tool1"])
        }
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = skill_select_tools(mock_ctx, "test-skill", mode="clear")

        assert result.mode == "clear"
        assert result.include_all_tools is False
        assert result.selected_tools == []

    def test_skill_select_tools_with_include_all(self):
        """Test selecting tools with include_all_tools=True."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {}
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = skill_select_tools(mock_ctx, "test-skill", include_all_tools=True, mode="replace")

        assert result.include_all_tools is True
        assert result.selected_tools == []

    def test_skill_select_tools_invalid_mode(self):
        """Test selecting tools with invalid mode defaults to replace."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {}
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = skill_select_tools(mock_ctx, "test-skill", tools=["tool1"], mode="invalid")

        assert result.mode == "replace"

    def test_skill_select_tools_previous_all_tools(self):
        """Test selecting tools when previous state has all tools."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {
            "temp:skill:tools:test-skill": '*'
        }
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = skill_select_tools(mock_ctx, "test-skill", tools=["tool1"], mode="add")

        assert result.include_all_tools is True


class TestSkillSelectDocsResult:
    """Test suite for SkillSelectDocsResult class."""

    def test_create_result(self):
        """Test creating SkillSelectDocsResult."""
        result = SkillSelectDocsResult(
            skill="test-skill",
            selected_docs=["doc1.md"],
            include_all_docs=True,
            mode="replace"
        )

        assert result.skill == "test-skill"
        assert result.selected_docs == ["doc1.md"]
        assert result.include_all_docs is True
        assert result.mode == "replace"

    def test_create_result_with_alias_fields(self):
        """Test creating SkillSelectDocsResult with alias fields."""
        result = SkillSelectDocsResult(
            skill="test-skill",
            selected_items=["doc1.md", "doc2.md"],
            include_all=True,
            mode="replace"
        )

        # Alias fields should be mapped to actual fields
        assert result.selected_docs == ["doc1.md", "doc2.md"]
        assert result.include_all_docs is True


class TestSkillSelectToolsResult:
    """Test suite for SkillSelectToolsResult class."""

    def test_create_result(self):
        """Test creating SkillSelectToolsResult."""
        result = SkillSelectToolsResult(
            skill="test-skill",
            selected_tools=["tool1"],
            include_all_tools=True,
            mode="replace"
        )

        assert result.skill == "test-skill"
        assert result.selected_tools == ["tool1"]
        assert result.include_all_tools is True
        assert result.mode == "replace"

    def test_create_result_with_alias_fields(self):
        """Test creating SkillSelectToolsResult with alias fields."""
        result = SkillSelectToolsResult(
            skill="test-skill",
            selected_items=["tool1", "tool2"],
            include_all=True,
            mode="replace"
        )

        # Alias fields should be mapped to actual fields
        assert result.selected_tools == ["tool1", "tool2"]
        assert result.include_all_tools is True


class TestStateDeltaHelpers:
    """Test suite for state delta helper functions."""

    def test_set_state_delta_for_skill_load(self):
        """Test setting state delta for skill load."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        _set_state_delta_for_skill_load(mock_ctx, "test-skill", ["doc1.md"], False)

        loaded_key = "temp:skill:loaded:test-skill"
        docs_key = "temp:skill:docs:test-skill"

        assert loaded_key in mock_ctx.actions.state_delta
        assert docs_key in mock_ctx.actions.state_delta
        assert json.loads(mock_ctx.actions.state_delta[docs_key]) == ["doc1.md"]

    def test_set_state_delta_for_skill_load_include_all(self):
        """Test setting state delta with include_all_docs."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        _set_state_delta_for_skill_load(mock_ctx, "test-skill", [], True)

        docs_key = "temp:skill:docs:test-skill"
        assert mock_ctx.actions.state_delta[docs_key] == '*'

    def test_set_state_delta_for_skill_tools(self):
        """Test setting state delta for skill tools."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        _set_state_delta_for_skill_tools(mock_ctx, "test-skill", ["tool1", "tool2"])

        tools_key = "temp:skill:tools:test-skill"
        assert tools_key in mock_ctx.actions.state_delta
        assert json.loads(mock_ctx.actions.state_delta[tools_key]) == ["tool1", "tool2"]
