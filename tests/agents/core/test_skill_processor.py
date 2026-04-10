# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import json
from copy import deepcopy
from unittest.mock import Mock

import trpc_agent_sdk.skills as _skills_pkg

if not hasattr(_skills_pkg, "get_skill_processor_parameters"):

    def _compat_get_skill_processor_parameters(agent_context):
        from trpc_agent_sdk.agents.core._skill_processor import get_skill_processor_parameters as _impl
        return _impl(agent_context)

    _skills_pkg.get_skill_processor_parameters = _compat_get_skill_processor_parameters

from trpc_agent_sdk.agents.core._skill_processor import SkillsRequestProcessor
from trpc_agent_sdk.agents.core._skill_processor import get_skill_processor_parameters
from trpc_agent_sdk.agents.core._skill_processor import set_skill_processor_parameters
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.skills import Skill
from trpc_agent_sdk.skills import SkillResource
from trpc_agent_sdk.skills import SkillSummary
from trpc_agent_sdk.skills import docs_key
from trpc_agent_sdk.skills import docs_session_key
from trpc_agent_sdk.skills import loaded_key
from trpc_agent_sdk.skills import loaded_order_key
from trpc_agent_sdk.skills import loaded_session_key
from trpc_agent_sdk.skills import loaded_session_order_key
from trpc_agent_sdk.skills import tool_key
from trpc_agent_sdk.skills import tool_session_key
from trpc_agent_sdk.skills._skill_config import DEFAULT_SKILL_CONFIG
from trpc_agent_sdk.skills._skill_config import set_skill_config


def _build_skill(name: str) -> Skill:
    return Skill(
        summary=SkillSummary(name=name, description=f"{name} description"),
        body=f"{name} body",
        resources=[SkillResource(path="docs/guide.md", content=f"{name} guide")],
        tools=["tool-a", "tool-b"],
    )


def _build_repo(skills: dict[str, Skill]) -> Mock:
    repo = Mock()
    repo.summaries.return_value = [skill.summary for skill in skills.values()]
    repo.user_prompt.return_value = "repo user prompt"
    repo.get.side_effect = lambda name: skills.get(name)
    return repo


def _build_ctx(state: dict, *, agent_name: str = "demo-agent", load_mode: str = "turn") -> Mock:
    ctx = Mock(spec=InvocationContext)
    ctx.agent_name = agent_name
    ctx.session_state = state
    ctx.actions = Mock()
    ctx.actions.state_delta = {}
    ctx.agent_context = AgentContext()
    config = deepcopy(DEFAULT_SKILL_CONFIG)
    config["skill_processor"]["load_mode"] = load_mode
    set_skill_config(ctx.agent_context, config)
    ctx.session = Mock()
    ctx.session.state = state
    ctx.session.events = []
    return ctx


class TestSkillsRequestProcessor:

    async def test_injects_overview_and_loaded_content(self):
        skill = _build_skill("demo-skill")
        repo = _build_repo({"demo-skill": skill})
        loaded_state_key = loaded_session_key(loaded_key("demo-agent", "demo-skill"))
        docs_state_key = docs_session_key(docs_key("demo-agent", "demo-skill"))
        tools_state_key = tool_session_key(tool_key("demo-agent", "demo-skill"))
        ctx = _build_ctx({
            loaded_state_key: True,
            docs_state_key: json.dumps(["docs/guide.md"]),
            tools_state_key: json.dumps(["tool-a"]),
        }, load_mode="session")
        request = LlmRequest(contents=[], tools_dict={})
        processor = SkillsRequestProcessor(repo, load_mode="session")

        loaded = await processor.process_llm_request(ctx, request)

        assert loaded == ["demo-skill"]
        assert request.config is not None
        text = request.config.system_instruction
        assert "repo user prompt" in text
        assert "Available skills:" in text
        assert "- demo-skill: demo-skill description" in text
        assert "[Loaded] demo-skill" in text
        assert "Docs loaded: docs/guide.md" in text
        assert "[Doc] docs/guide.md" in text
        assert "Tools selected: tool-a" in text

    async def test_tool_result_mode_skips_loaded_materialization(self):
        skill = _build_skill("demo-skill")
        repo = _build_repo({"demo-skill": skill})
        loaded_state_key = loaded_session_key(loaded_key("demo-agent", "demo-skill"))
        ctx = _build_ctx({loaded_state_key: True}, load_mode="session")
        request = LlmRequest(contents=[], tools_dict={})
        processor = SkillsRequestProcessor(repo, tool_result_mode=True, load_mode="session")

        loaded = await processor.process_llm_request(ctx, request)

        assert loaded == ["demo-skill"]
        assert request.config is not None
        text = request.config.system_instruction
        assert "Available skills:" in text
        assert "[Loaded] demo-skill" not in text

    async def test_once_mode_offloads_loaded_state(self):
        skill = _build_skill("demo-skill")
        repo = _build_repo({"demo-skill": skill})
        ctx = _build_ctx({
            loaded_key("demo-agent", "demo-skill"): True,
            docs_key("demo-agent", "demo-skill"): json.dumps(["docs/guide.md"]),
            tool_key("demo-agent", "demo-skill"): json.dumps(["tool-a"]),
        })
        request = LlmRequest(contents=[], tools_dict={})
        processor = SkillsRequestProcessor(repo, load_mode="once")

        await processor.process_llm_request(ctx, request)

        assert ctx.actions.state_delta[loaded_key("demo-agent", "demo-skill")] is None
        assert ctx.actions.state_delta[docs_key("demo-agent", "demo-skill")] is None
        assert ctx.actions.state_delta[tool_key("demo-agent", "demo-skill")] is None
        assert ctx.actions.state_delta[loaded_order_key("demo-agent")] is None

    async def test_turn_mode_clears_previous_skill_state(self):
        skill = _build_skill("demo-skill")
        repo = _build_repo({"demo-skill": skill})
        ctx = _build_ctx({
            loaded_key("demo-agent", "demo-skill"): True,
            docs_key("demo-agent", "demo-skill"): json.dumps(["docs/guide.md"]),
            tool_key("demo-agent", "demo-skill"): json.dumps(["tool-a"]),
            loaded_order_key("demo-agent"): json.dumps(["demo-skill"]),
        })
        request = LlmRequest(contents=[], tools_dict={})
        processor = SkillsRequestProcessor(repo, load_mode="turn")

        loaded = await processor.process_llm_request(ctx, request)

        assert loaded == []
        assert ctx.actions.state_delta[loaded_key("demo-agent", "demo-skill")] is None
        assert ctx.actions.state_delta[docs_key("demo-agent", "demo-skill")] is None
        assert ctx.actions.state_delta[tool_key("demo-agent", "demo-skill")] is None
        assert ctx.actions.state_delta[loaded_order_key("demo-agent")] is None
        assert ctx.agent_context.get_metadata("processor:skills:turn_init") is True

    async def test_session_mode_uses_temp_only_state(self):
        skill = _build_skill("demo-skill")
        repo = _build_repo({"demo-skill": skill})
        loaded_state_key = loaded_session_key(loaded_key("demo-agent", "demo-skill"))
        docs_state_key = docs_session_key(docs_key("demo-agent", "demo-skill"))
        tools_state_key = tool_session_key(tool_key("demo-agent", "demo-skill"))
        session_order_key = loaded_session_order_key(loaded_order_key("demo-agent"))
        ctx = _build_ctx({
            loaded_state_key: True,
            docs_state_key: json.dumps(["docs/guide.md"]),
            tools_state_key: json.dumps(["tool-a"]),
            session_order_key: json.dumps(["demo-skill"]),
        }, load_mode="session")
        request = LlmRequest(contents=[], tools_dict={})
        processor = SkillsRequestProcessor(repo, load_mode="session")

        loaded = await processor.process_llm_request(ctx, request)

        assert loaded == ["demo-skill"]
        assert loaded_key("demo-agent", "demo-skill") not in ctx.actions.state_delta
        assert docs_key("demo-agent", "demo-skill") not in ctx.actions.state_delta
        assert tool_key("demo-agent", "demo-skill") not in ctx.actions.state_delta

    async def test_max_loaded_skills_evicts_lru_skills_in_session_state(self):
        skill_a = _build_skill("skill-a")
        skill_b = _build_skill("skill-b")
        repo = _build_repo({"skill-a": skill_a, "skill-b": skill_b})
        loaded_a = loaded_session_key(loaded_key("demo-agent", "skill-a"))
        loaded_b = loaded_session_key(loaded_key("demo-agent", "skill-b"))
        docs_a = docs_session_key(docs_key("demo-agent", "skill-a"))
        docs_b = docs_session_key(docs_key("demo-agent", "skill-b"))
        tools_a = tool_session_key(tool_key("demo-agent", "skill-a"))
        tools_b = tool_session_key(tool_key("demo-agent", "skill-b"))
        order_key = loaded_session_order_key(loaded_order_key("demo-agent"))
        ctx = _build_ctx({
            loaded_a: True,
            loaded_b: True,
            docs_a: json.dumps(["docs/guide.md"]),
            docs_b: json.dumps(["docs/guide.md"]),
            tools_a: json.dumps(["tool-a"]),
            tools_b: json.dumps(["tool-a"]),
            order_key: json.dumps(["skill-a", "skill-b"]),
        }, load_mode="session")
        request = LlmRequest(contents=[], tools_dict={})
        processor = SkillsRequestProcessor(repo, max_loaded_skills=1, load_mode="session")

        loaded = await processor.process_llm_request(ctx, request)

        assert loaded == ["skill-b"]
        assert ctx.actions.state_delta[loaded_a] is None
        assert ctx.actions.state_delta[docs_a] is None
        assert ctx.actions.state_delta[tools_a] is None
        assert ctx.actions.state_delta[order_key] == json.dumps(["skill-b"])


class TestSkillProcessorParameters:

    def test_set_and_get_skill_processor_parameters(self):
        agent_context = AgentContext()
        parameters = {"load_mode": "session", "max_loaded_skills": 3}

        set_skill_processor_parameters(agent_context, parameters)
        got = get_skill_processor_parameters(agent_context)

        assert got["load_mode"] == "session"
        assert got["max_loaded_skills"] == 3
