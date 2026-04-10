# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from unittest.mock import Mock

import trpc_agent_sdk.skills as _skills_pkg

if not hasattr(_skills_pkg, "get_skill_processor_parameters"):

    def _compat_get_skill_processor_parameters(agent_context):
        from trpc_agent_sdk.agents.core._skill_processor import get_skill_processor_parameters as _impl
        return _impl(agent_context)

    _skills_pkg.get_skill_processor_parameters = _compat_get_skill_processor_parameters

from trpc_agent_sdk.agents.core._workspace_exec_processor import WorkspaceExecRequestProcessor
from trpc_agent_sdk.agents.core._workspace_exec_processor import get_workspace_exec_processor_parameters
from trpc_agent_sdk.agents.core._workspace_exec_processor import set_workspace_exec_processor_parameters
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import LlmRequest


def _build_ctx() -> Mock:
    ctx = Mock(spec=InvocationContext)
    ctx.agent_name = "demo-agent"
    return ctx


def _build_request_with_tools(*tool_names: str) -> LlmRequest:
    request = LlmRequest(contents=[], tools_dict={})
    config = Mock()
    config.system_instruction = ""
    config.tools = []
    for name in tool_names:
        declaration = Mock()
        declaration.name = name
        tool = Mock()
        tool.function_declarations = [declaration]
        config.tools.append(tool)
    request.config = config
    return request


class TestWorkspaceExecRequestProcessor:

    async def test_injects_guidance_when_workspace_exec_present(self):
        processor = WorkspaceExecRequestProcessor()
        ctx = _build_ctx()
        request = _build_request_with_tools("workspace_exec")

        await processor.process_llm_request(ctx, request)

        assert "Executor workspace guidance:" in request.config.system_instruction
        assert "workspace_exec" in request.config.system_instruction

    async def test_does_not_duplicate_guidance_header(self):
        processor = WorkspaceExecRequestProcessor()
        ctx = _build_ctx()
        request = _build_request_with_tools("workspace_exec")
        request.config.system_instruction = "Executor workspace guidance:\nexisting"

        await processor.process_llm_request(ctx, request)

        assert request.config.system_instruction == "Executor workspace guidance:\nexisting"

    async def test_includes_artifact_and_session_hints_when_tools_available(self):
        processor = WorkspaceExecRequestProcessor()
        ctx = _build_ctx()
        request = _build_request_with_tools(
            "workspace_exec",
            "workspace_save_artifact",
            "workspace_write_stdin",
            "workspace_kill_session",
        )

        await processor.process_llm_request(ctx, request)

        text = request.config.system_instruction
        assert "workspace_save_artifact" in text
        assert "workspace_write_stdin" in text
        assert "workspace_kill_session" in text

    async def test_includes_skills_repo_hint_via_repo_resolver(self):
        processor = WorkspaceExecRequestProcessor(repo_resolver=lambda _ctx: object())
        ctx = _build_ctx()
        request = _build_request_with_tools("workspace_exec")

        await processor.process_llm_request(ctx, request)

        assert "Paths under skills/" in request.config.system_instruction

    async def test_respects_enabled_resolver(self):
        processor = WorkspaceExecRequestProcessor(enabled_resolver=lambda _ctx: False)
        ctx = _build_ctx()
        request = _build_request_with_tools("workspace_exec")

        await processor.process_llm_request(ctx, request)

        assert request.config.system_instruction == ""

    async def test_sessions_resolver_can_enable_session_guidance_without_session_tools(self):
        processor = WorkspaceExecRequestProcessor(sessions_resolver=lambda _ctx: True)
        ctx = _build_ctx()
        request = _build_request_with_tools("workspace_exec")

        await processor.process_llm_request(ctx, request)

        text = request.config.system_instruction
        assert "workspace_write_stdin" in text
        assert "workspace_kill_session" in text


class TestWorkspaceExecProcessorParameters:

    def test_set_and_get_workspace_exec_processor_parameters(self):
        agent_context = AgentContext()
        parameters = {"session_tools": True, "has_skills_repo": True}

        set_workspace_exec_processor_parameters(agent_context, parameters)
        got = get_workspace_exec_processor_parameters(agent_context)

        assert got["session_tools"] is True
        assert got["has_skills_repo"] is True
