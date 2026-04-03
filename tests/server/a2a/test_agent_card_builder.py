# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.server.a2a._agent_card_builder."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.types import AgentCapabilities, AgentCard, AgentExtension, AgentSkill

from trpc_agent_sdk.agents import BaseAgent, ChainAgent, CycleAgent, LlmAgent, ParallelAgent
from trpc_agent_sdk.server.a2a._agent_card_builder import (
    AgentCardBuilder,
    _build_agent_description,
    _build_code_executor_skill,
    _build_llm_agent_description_with_instructions,
    _build_llm_agent_skills,
    _build_loop_description,
    _build_non_llm_agent_skills,
    _build_orchestration_skill,
    _build_parallel_description,
    _build_planner_skill,
    _build_primary_skills,
    _build_sequential_description,
    _build_sub_agent_skills,
    _build_tool_skills,
    _capabilities_with_trpc_extension,
    _get_agent_skill_name,
    _get_agent_type,
    _get_default_description,
    _get_input_modes,
    _get_output_modes,
    _get_workflow_description,
    _replace_pronouns,
)
from trpc_agent_sdk.server.a2a._constants import EXTENSION_TRPC_A2A_VERSION, INTERACTION_SPEC_VERSION


def _make_base_agent(name="agent", description=None, sub_agents=None):
    agent = MagicMock(spec=BaseAgent)
    agent.name = name
    agent.description = description
    agent.sub_agents = sub_agents or []
    return agent


def _make_llm_agent(name="llm_agent", description=None, instruction=None,
                    global_instruction=None, tools=None, planner=None,
                    sub_agents=None, generate_content_config=None):
    agent = MagicMock(spec=LlmAgent)
    agent.name = name
    agent.description = description
    agent.instruction = instruction
    agent.global_instruction = global_instruction
    agent.tools = tools or []
    agent.planner = planner
    agent.sub_agents = sub_agents or []
    agent.generate_content_config = generate_content_config
    return agent


def _make_sequential_agent(name="seq", description=None, sub_agents=None):
    agent = MagicMock(spec=ChainAgent)
    agent.name = name
    agent.description = description
    agent.sub_agents = sub_agents or []
    return agent


def _make_parallel_agent(name="par", description=None, sub_agents=None):
    agent = MagicMock(spec=ParallelAgent)
    agent.name = name
    agent.description = description
    agent.sub_agents = sub_agents or []
    return agent


def _make_loop_agent(name="loop", description=None, sub_agents=None, max_iterations=None):
    agent = MagicMock(spec=CycleAgent)
    agent.name = name
    agent.description = description
    agent.sub_agents = sub_agents or []
    agent.max_iterations = max_iterations
    return agent


# ---------------------------------------------------------------------------
# AgentCardBuilder.__init__
# ---------------------------------------------------------------------------
class TestAgentCardBuilderInit:
    def test_raises_on_none_agent(self):
        with pytest.raises(ValueError, match="Agent cannot be None"):
            AgentCardBuilder(agent=None)

    def test_defaults(self):
        agent = _make_base_agent()
        builder = AgentCardBuilder(agent=agent)
        assert builder._agent is agent
        assert builder._rpc_url == ""
        assert builder._agent_version == "0.0.1"

    def test_custom_rpc_url(self):
        agent = _make_base_agent()
        builder = AgentCardBuilder(agent=agent, rpc_url="http://example.com/")
        assert builder._rpc_url == "http://example.com/"


# ---------------------------------------------------------------------------
# AgentCardBuilder.build
# ---------------------------------------------------------------------------
class TestAgentCardBuilderBuild:
    async def test_basic_build(self):
        agent = _make_llm_agent(name="my-agent", description="A test agent")
        builder = AgentCardBuilder(agent=agent, rpc_url="http://localhost:8080/")
        card = await builder.build()
        assert isinstance(card, AgentCard)
        assert card.name == "my-agent"
        assert card.description == "A test agent"
        assert card.url == "http://localhost:8080"

    async def test_build_with_no_description(self):
        agent = _make_llm_agent(name="agent", description=None)
        builder = AgentCardBuilder(agent=agent)
        card = await builder.build()
        assert card.description == "An A2A Agent"

    async def test_build_includes_trpc_extension(self):
        agent = _make_llm_agent(name="agent")
        builder = AgentCardBuilder(agent=agent)
        card = await builder.build()
        ext_uris = [e.uri for e in card.capabilities.extensions]
        assert EXTENSION_TRPC_A2A_VERSION in ext_uris

    async def test_build_raises_runtime_error_on_failure(self):
        agent = MagicMock(spec=BaseAgent)
        agent.name = "bad_agent"
        agent.description = "desc"
        agent.sub_agents = []
        # Force isinstance to fail for LlmAgent check => falls to non_llm path
        # but then _get_agent_type raises
        with patch(
            "trpc_agent_sdk.server.a2a._agent_card_builder._build_primary_skills",
            side_effect=Exception("boom"),
        ):
            builder = AgentCardBuilder(agent=agent)
            with pytest.raises(RuntimeError, match="Failed to build agent card"):
                await builder.build()


# ---------------------------------------------------------------------------
# _capabilities_with_trpc_extension
# ---------------------------------------------------------------------------
class TestCapabilitiesWithTrpcExtension:
    def test_adds_extension(self):
        caps = _capabilities_with_trpc_extension(AgentCapabilities())
        uris = [e.uri for e in caps.extensions]
        assert EXTENSION_TRPC_A2A_VERSION in uris

    def test_does_not_duplicate(self):
        existing = AgentCapabilities(
            extensions=[AgentExtension(uri=EXTENSION_TRPC_A2A_VERSION, params={"version": "old"})]
        )
        caps = _capabilities_with_trpc_extension(existing)
        uris = [e.uri for e in caps.extensions]
        assert uris.count(EXTENSION_TRPC_A2A_VERSION) == 1

    def test_none_input(self):
        caps = _capabilities_with_trpc_extension(None)
        assert caps.extensions is not None


# ---------------------------------------------------------------------------
# _build_primary_skills / _build_llm_agent_skills / _build_non_llm_agent_skills
# ---------------------------------------------------------------------------
class TestBuildPrimarySkills:
    async def test_llm_agent(self):
        agent = _make_llm_agent(name="llm", description="An LLM")
        skills = await _build_primary_skills(agent)
        assert len(skills) >= 1
        assert skills[0].tags == ["llm"]

    async def test_non_llm_agent(self):
        agent = _make_sequential_agent(name="seq", description="A workflow")
        skills = await _build_primary_skills(agent)
        assert len(skills) >= 1


class TestBuildLlmAgentSkills:
    async def test_basic(self):
        agent = _make_llm_agent(name="llm", description="Desc")
        skills = await _build_llm_agent_skills(agent)
        assert any(s.name == "model" for s in skills)

    async def test_with_planner(self):
        agent = _make_llm_agent(name="llm", planner=MagicMock())
        skills = await _build_llm_agent_skills(agent)
        assert any(s.name == "planning" for s in skills)

    @patch("trpc_agent_sdk.server.a2a._agent_card_builder.convert_toolunion_to_tool_list")
    async def test_with_tools(self, mock_convert):
        tool = MagicMock()
        tool.name = "search"
        tool.description = "Search the web"
        mock_convert.return_value = [tool]
        agent = _make_llm_agent(name="llm", tools=[MagicMock()])
        skills = await _build_llm_agent_skills(agent)
        assert any(s.name == "search" for s in skills)

    @patch("trpc_agent_sdk.server.a2a._agent_card_builder.convert_toolunion_to_tool_list",
           side_effect=Exception("fail"))
    async def test_tool_failure_continues(self, mock_convert):
        agent = _make_llm_agent(name="llm", tools=[MagicMock()])
        skills = await _build_llm_agent_skills(agent)
        assert any(s.name == "model" for s in skills)


# ---------------------------------------------------------------------------
# _build_sub_agent_skills
# ---------------------------------------------------------------------------
class TestBuildSubAgentSkills:
    async def test_basic(self):
        sub = _make_llm_agent(name="sub_llm", description="Sub")
        agent = _make_base_agent(sub_agents=[sub])
        skills = await _build_sub_agent_skills(agent)
        assert len(skills) >= 1
        assert any("sub_agent:sub_llm" in (s.tags or []) for s in skills)

    async def test_no_sub_agents(self):
        agent = _make_base_agent(sub_agents=[])
        skills = await _build_sub_agent_skills(agent)
        assert skills == []

    async def test_failing_sub_agent_continues(self):
        good = _make_llm_agent(name="good", description="Good")
        bad = MagicMock(spec=BaseAgent)
        bad.name = "bad"
        bad.sub_agents = []
        with patch(
            "trpc_agent_sdk.server.a2a._agent_card_builder._build_primary_skills",
            side_effect=[Exception("fail"), [AgentSkill(id="good", name="model", description="desc", tags=["llm"])]],
        ):
            agent = _make_base_agent(sub_agents=[bad, good])
            skills = await _build_sub_agent_skills(agent)
            assert len(skills) >= 0


# ---------------------------------------------------------------------------
# _build_tool_skills
# ---------------------------------------------------------------------------
class TestBuildToolSkills:
    @patch("trpc_agent_sdk.server.a2a._agent_card_builder.convert_toolunion_to_tool_list")
    async def test_basic(self, mock_convert):
        tool = MagicMock()
        tool.name = "my_tool"
        tool.description = "Does stuff"
        mock_convert.return_value = [tool]
        agent = _make_llm_agent(name="llm", tools=[MagicMock()])
        skills = await _build_tool_skills(agent)
        assert len(skills) == 1
        assert skills[0].name == "my_tool"
        assert skills[0].id == "llm-my_tool"

    @patch("trpc_agent_sdk.server.a2a._agent_card_builder.convert_toolunion_to_tool_list")
    async def test_tool_without_name(self, mock_convert):
        tool = MagicMock()
        tool.name = None
        tool.description = "A custom tool"
        tool.__class__.__name__ = "CustomTool"
        mock_convert.return_value = [tool]
        agent = _make_llm_agent(name="llm", tools=[MagicMock()])
        skills = await _build_tool_skills(agent)
        assert skills[0].name == "CustomTool"


# ---------------------------------------------------------------------------
# _build_planner_skill / _build_code_executor_skill
# ---------------------------------------------------------------------------
class TestBuildPlannerSkill:
    def test_basic(self):
        agent = _make_llm_agent(name="llm")
        skill = _build_planner_skill(agent)
        assert skill.id == "llm-planner"
        assert "planning" in skill.tags


class TestBuildCodeExecutorSkill:
    def test_basic(self):
        agent = _make_llm_agent(name="llm")
        skill = _build_code_executor_skill(agent)
        assert skill.id == "llm-code-executor"
        assert "code_execution" in skill.tags


# ---------------------------------------------------------------------------
# _build_non_llm_agent_skills
# ---------------------------------------------------------------------------
class TestBuildNonLlmAgentSkills:
    async def test_sequential_agent(self):
        sub = _make_base_agent(name="step1", description="Step 1")
        agent = _make_sequential_agent(name="seq", description="Pipeline", sub_agents=[sub])
        skills = await _build_non_llm_agent_skills(agent)
        assert len(skills) >= 1
        assert skills[0].tags == ["sequential_workflow"]

    async def test_with_sub_agents_adds_orchestration(self):
        sub = _make_base_agent(name="step1", description="Step 1")
        agent = _make_sequential_agent(name="seq", sub_agents=[sub])
        skills = await _build_non_llm_agent_skills(agent)
        assert any(s.name == "sub-agents" for s in skills)


# ---------------------------------------------------------------------------
# _build_orchestration_skill
# ---------------------------------------------------------------------------
class TestBuildOrchestrationSkill:
    def test_basic(self):
        sub = _make_base_agent(name="sub1", description="Sub agent 1")
        agent = _make_sequential_agent(name="seq", sub_agents=[sub])
        skill = _build_orchestration_skill(agent, "sequential_workflow")
        assert skill is not None
        assert "Orchestrates:" in skill.description

    def test_empty_sub_agents(self):
        agent = _make_sequential_agent(name="seq", sub_agents=[])
        skill = _build_orchestration_skill(agent, "sequential_workflow")
        assert skill is None


# ---------------------------------------------------------------------------
# _get_agent_type / _get_agent_skill_name
# ---------------------------------------------------------------------------
class TestGetAgentType:
    def test_llm(self):
        assert _get_agent_type(_make_llm_agent()) == "llm"

    def test_sequential(self):
        assert _get_agent_type(_make_sequential_agent()) == "sequential_workflow"

    def test_parallel(self):
        assert _get_agent_type(_make_parallel_agent()) == "parallel_workflow"

    def test_loop(self):
        assert _get_agent_type(_make_loop_agent()) == "loop_workflow"

    def test_custom(self):
        assert _get_agent_type(_make_base_agent()) == "custom_agent"


class TestGetAgentSkillName:
    def test_llm(self):
        assert _get_agent_skill_name(_make_llm_agent()) == "model"

    def test_workflow(self):
        assert _get_agent_skill_name(_make_sequential_agent()) == "workflow"
        assert _get_agent_skill_name(_make_parallel_agent()) == "workflow"
        assert _get_agent_skill_name(_make_loop_agent()) == "workflow"

    def test_custom(self):
        assert _get_agent_skill_name(_make_base_agent()) == "custom"


# ---------------------------------------------------------------------------
# _build_agent_description / _build_llm_agent_description_with_instructions
# ---------------------------------------------------------------------------
class TestBuildAgentDescription:
    def test_with_description(self):
        agent = _make_sequential_agent(name="seq", description="My workflow")
        assert "My workflow" in _build_agent_description(agent)

    def test_without_description_uses_default(self):
        agent = _make_sequential_agent(name="seq", description=None, sub_agents=[])
        desc = _build_agent_description(agent)
        assert "sequential workflow" in desc.lower()

    def test_appends_workflow_description(self):
        sub = _make_base_agent(name="step1", description="Do step 1")
        agent = _make_sequential_agent(name="seq", description="Pipeline", sub_agents=[sub])
        desc = _build_agent_description(agent)
        assert "Pipeline" in desc
        assert "Do step 1" in desc


class TestBuildLlmAgentDescriptionWithInstructions:
    def test_with_all_parts(self):
        agent = _make_llm_agent(
            description="A helper",
            instruction="You are a helpful assistant.",
            global_instruction="You must be polite.",
        )
        desc = _build_llm_agent_description_with_instructions(agent)
        assert "A helper" in desc
        assert "I am a helpful assistant." in desc
        assert "I must be polite." in desc

    def test_without_anything(self):
        agent = _make_llm_agent(description=None, instruction=None, global_instruction=None)
        desc = _build_llm_agent_description_with_instructions(agent)
        assert "LLM" in desc


# ---------------------------------------------------------------------------
# _replace_pronouns
# ---------------------------------------------------------------------------
class TestReplacePronouns:
    def test_you_are(self):
        assert _replace_pronouns("You are a helpful agent") == "I am a helpful agent"

    def test_your(self):
        assert _replace_pronouns("This is your task") == "This is my task"

    def test_youre(self):
        assert _replace_pronouns("You're the best") == "I am the best"

    def test_youve(self):
        assert _replace_pronouns("You've done well") == "I have done well"

    def test_yours(self):
        assert _replace_pronouns("The choice is yours") == "The choice is mine"

    def test_you_standalone(self):
        assert _replace_pronouns("I will help you today") == "I will help I today"

    def test_case_insensitive(self):
        result = _replace_pronouns("YOU ARE amazing")
        assert "I am" in result

    def test_no_match(self):
        assert _replace_pronouns("Hello world") == "Hello world"


# ---------------------------------------------------------------------------
# Workflow descriptions
# ---------------------------------------------------------------------------
class TestGetWorkflowDescription:
    def test_no_sub_agents(self):
        agent = _make_sequential_agent(sub_agents=[])
        assert _get_workflow_description(agent) is None

    def test_sequential(self):
        sub = _make_base_agent(name="step1", description="do thing")
        agent = _make_sequential_agent(sub_agents=[sub])
        desc = _get_workflow_description(agent)
        assert desc is not None

    def test_parallel(self):
        sub = _make_base_agent(name="task1", description="do parallel thing")
        agent = _make_parallel_agent(sub_agents=[sub])
        desc = _get_workflow_description(agent)
        assert desc is not None

    def test_loop(self):
        sub = _make_base_agent(name="iter", description="iterate")
        agent = _make_loop_agent(sub_agents=[sub])
        desc = _get_workflow_description(agent)
        assert desc is not None

    def test_base_agent_returns_none(self):
        sub = _make_base_agent(name="s")
        agent = _make_base_agent(sub_agents=[sub])
        assert _get_workflow_description(agent) is None


class TestBuildSequentialDescription:
    def test_single_step(self):
        sub = _make_base_agent(name="step1", description="do thing")
        agent = _make_sequential_agent(sub_agents=[sub])
        desc = _build_sequential_description(agent)
        assert "First" in desc

    def test_multiple_steps(self):
        subs = [
            _make_base_agent(name="s1", description="first thing"),
            _make_base_agent(name="s2", description="second thing"),
            _make_base_agent(name="s3", description="third thing"),
        ]
        agent = _make_sequential_agent(sub_agents=subs)
        desc = _build_sequential_description(agent)
        assert "First" in desc
        assert "Then" in desc
        assert "Finally" in desc

    def test_uses_agent_name_when_no_description(self):
        sub = _make_base_agent(name="step1", description=None)
        agent = _make_sequential_agent(sub_agents=[sub])
        desc = _build_sequential_description(agent)
        assert "step1" in desc


class TestBuildParallelDescription:
    def test_basic(self):
        subs = [
            _make_base_agent(name="t1", description="task 1"),
            _make_base_agent(name="t2", description="task 2"),
        ]
        agent = _make_parallel_agent(sub_agents=subs)
        desc = _build_parallel_description(agent)
        assert "simultaneously" in desc


class TestBuildLoopDescription:
    def test_with_max_iterations(self):
        sub = _make_base_agent(name="iter", description="iterate")
        agent = _make_loop_agent(sub_agents=[sub], max_iterations=5)
        desc = _build_loop_description(agent)
        assert "max 5 iterations" in desc

    def test_without_max_iterations(self):
        sub = _make_base_agent(name="iter", description="iterate")
        agent = _make_loop_agent(sub_agents=[sub], max_iterations=None)
        desc = _build_loop_description(agent)
        assert "unlimited" in desc


# ---------------------------------------------------------------------------
# _get_default_description
# ---------------------------------------------------------------------------
class TestGetDefaultDescription:
    def test_llm(self):
        assert "LLM" in _get_default_description(_make_llm_agent())

    def test_sequential(self):
        assert "sequential" in _get_default_description(_make_sequential_agent()).lower()

    def test_parallel(self):
        assert "parallel" in _get_default_description(_make_parallel_agent()).lower()

    def test_loop(self):
        assert "loop" in _get_default_description(_make_loop_agent()).lower()

    def test_custom(self):
        assert "custom" in _get_default_description(_make_base_agent()).lower()


# ---------------------------------------------------------------------------
# _get_input_modes / _get_output_modes
# ---------------------------------------------------------------------------
class TestGetInputModes:
    def test_non_llm_returns_none(self):
        assert _get_input_modes(_make_base_agent()) is None

    def test_llm_returns_none(self):
        assert _get_input_modes(_make_llm_agent()) is None


class TestGetOutputModes:
    def test_non_llm_returns_none(self):
        assert _get_output_modes(_make_base_agent()) is None

    def test_llm_without_config_returns_none(self):
        agent = _make_llm_agent(generate_content_config=None)
        assert _get_output_modes(agent) is None

    def test_llm_with_modalities(self):
        config = MagicMock()
        config.response_modalities = ["text/plain", "image/png"]
        agent = _make_llm_agent(generate_content_config=config)
        result = _get_output_modes(agent)
        assert result == ["text/plain", "image/png"]
