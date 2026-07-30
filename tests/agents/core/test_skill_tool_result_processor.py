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

from trpc_agent_sdk.agents.core._skills_tool_result_processor import SkillsToolResultRequestProcessor
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.skills import Skill
from trpc_agent_sdk.skills import SkillResource
from trpc_agent_sdk.skills import SkillSummary
from trpc_agent_sdk.skills import SkillToolsNames
from trpc_agent_sdk.skills import docs_key
from trpc_agent_sdk.skills import loaded_key
from trpc_agent_sdk.skills import loaded_order_key
from trpc_agent_sdk.skills._skill_config import DEFAULT_SKILL_CONFIG
from trpc_agent_sdk.skills._skill_config import set_skill_config
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


def _build_context(agent_name: str, state: dict, *, load_mode: str = "turn") -> Mock:
    ctx = Mock(spec=InvocationContext)
    ctx.agent_name = agent_name
    ctx.session_state = state
    ctx.actions = Mock()
    ctx.actions.state_delta = {}
    ctx.agent_context = AgentContext()
    config = deepcopy(DEFAULT_SKILL_CONFIG)
    config["skill_processor"]["load_mode"] = load_mode
    set_skill_config(ctx.agent_context, config)
    return ctx


def _build_skill() -> Skill:
    return Skill(
        summary=SkillSummary(name="demo-skill", description="demo"),
        body="Use this skill body.",
        resources=[SkillResource(path="docs/guide.md", content="Guide content.")],
    )


class TestSkillsToolResultRequestProcessor:

    async def test_materialize_tool_result_from_skill_load(self):
        repo = Mock()
        repo.get.return_value = _build_skill()

        ctx = _build_context(
            "demo-agent",
            {
                loaded_key("demo-agent", "demo-skill"): True,
                docs_key("demo-agent", "demo-skill"): json.dumps(["docs/guide.md"]),
            },
        )
        processor = SkillsToolResultRequestProcessor(repo)

        call_part = Part.from_function_call(name=SkillToolsNames.LOAD, args={"skill": "demo-skill"})
        call_part.function_call.id = "call_1"
        response_part = Part.from_function_response(name=SkillToolsNames.LOAD, response={"result": "skill 'demo-skill' loaded"})
        response_part.function_response.id = "call_1"
        request = LlmRequest(
            contents=[
                Content(role="model", parts=[call_part]),
                Content(role="user", parts=[response_part]),
            ],
            tools_dict={},
        )

        loaded = await processor.process_llm_request(ctx, request)

        assert loaded == ["demo-skill"]
        result = response_part.function_response.response["result"]
        assert "[Loaded] demo-skill" in result
        assert "Docs loaded: docs/guide.md" in result
        assert "[Doc] docs/guide.md" in result

    async def test_fallback_to_system_instruction_when_tool_result_missing(self):
        repo = Mock()
        repo.get.return_value = _build_skill()
        ctx = _build_context(
            "demo-agent",
            {loaded_key("demo-agent", "demo-skill"): True},
        )
        processor = SkillsToolResultRequestProcessor(repo)
        request = LlmRequest(contents=[Content(role="user", parts=[Part.from_text(text="hello")])], tools_dict={})

        await processor.process_llm_request(ctx, request)

        assert request.config is not None
        assert "Loaded skill context:" in request.config.system_instruction
        assert "[Loaded] demo-skill" in request.config.system_instruction

    async def test_once_mode_offloads_loaded_skill_state(self):
        repo = Mock()
        repo.get.return_value = _build_skill()
        state = {
            loaded_key("demo-agent", "demo-skill"): True,
            docs_key("demo-agent", "demo-skill"): json.dumps(["docs/guide.md"]),
        }
        ctx = _build_context("demo-agent", state, load_mode="once")
        processor = SkillsToolResultRequestProcessor(repo)

        call_part = Part.from_function_call(name=SkillToolsNames.LOAD, args={"skill": "demo-skill"})
        call_part.function_call.id = "call_1"
        response_part = Part.from_function_response(name=SkillToolsNames.LOAD, response={"result": "skill 'demo-skill' loaded"})
        response_part.function_response.id = "call_1"
        request = LlmRequest(
            contents=[
                Content(role="model", parts=[call_part]),
                Content(role="user", parts=[response_part]),
            ],
            tools_dict={},
        )

        await processor.process_llm_request(ctx, request)

        assert ctx.actions.state_delta[loaded_key("demo-agent", "demo-skill")] is None
        assert ctx.actions.state_delta[docs_key("demo-agent", "demo-skill")] is None
        assert ctx.actions.state_delta[loaded_order_key("demo-agent")] is None
