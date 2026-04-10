# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Inject workspace_exec guidance into request system instructions.
"""

from __future__ import annotations

from typing import Any
from typing import Callable
from typing import Optional

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.skills import BaseSkillRepository
from trpc_agent_sdk.skills import get_skill_config
from trpc_agent_sdk.skills import set_skill_config

_WORKSPACE_EXEC_GUIDANCE_HEADER = "Executor workspace guidance:"


class WorkspaceExecRequestProcessor:
    """Request processor for workspace_exec guidance injection."""

    def __init__(
        self,
        *,
        session_tools: bool = False,
        has_skills_repo: bool = False,
        repo_resolver: Optional[Callable[[InvocationContext], Optional[BaseSkillRepository]]] = None,
        enabled_resolver: Optional[Callable[[InvocationContext], bool]] = None,
        sessions_resolver: Optional[Callable[[InvocationContext], bool]] = None,
    ) -> None:
        self._session_tools = session_tools
        self._static_skills_repo = has_skills_repo
        self._repo_resolver = repo_resolver
        self._enabled_resolver = enabled_resolver
        self._sessions_resolver = sessions_resolver

    async def process_llm_request(self, ctx: InvocationContext, request: LlmRequest) -> None:
        """Inject workspace guidance into request.config.system_instruction."""
        if ctx is None or request is None:
            return
        guidance = self._guidance_text(ctx, request)
        if not guidance:
            return

        existing = ""
        if request.config and request.config.system_instruction:
            existing = str(request.config.system_instruction)
        if _WORKSPACE_EXEC_GUIDANCE_HEADER in existing:
            return
        request.append_instructions([guidance])

    def _guidance_text(self, ctx: InvocationContext, request: LlmRequest) -> str:
        if not self._enabled_for_invocation(ctx, request):
            return ""
        lines: list[str] = [
            _WORKSPACE_EXEC_GUIDANCE_HEADER,
            "- Treat workspace_exec as the default general shell runner for shared "
            "executor-side work. It runs inside the current executor workspace, not "
            "on the agent host; workspace is its scope, not its capability limit.",
            "- workspace_exec starts at the workspace root by default. Prefer work/, "
            "out/, and runs/ for shared executor-side work, and treat cwd as a "
            "workspace-relative path.",
            "- Network access depends on the current executor environment. If you "
            "need a network command such as curl, use a small bounded command to "
            "verify whether that environment allows it.",
            "- When a limitation depends on the executor environment and a small "
            "bounded command can verify it, verify first before claiming the "
            "limitation. This applies to checks such as command availability, file "
            "presence, or access to a known URL.",
        ]
        if self._supports_artifact_save(request):
            lines.append("- Use workspace_save_artifact only when you need a stable artifact "
                         "reference for an already existing file in work/, out/, or runs/. "
                         "Intermediate files usually stay in the workspace.")
        if self._has_skills_repo(ctx):
            lines.append("- Paths under skills/ are only useful when some other tool has "
                         "already placed content there. workspace_exec does not stage skills "
                         "automatically.")
        if self._session_tools_for_invocation(ctx, request):
            lines.append("- When workspace_exec starts a command that keeps running or waits "
                         "for stdin, continue with workspace_write_stdin. When chars is empty, "
                         "workspace_write_stdin acts like a poll. Use workspace_kill_session "
                         "to stop a running workspace_exec session.")
            lines.append("- Interactive workspace_exec sessions are only guaranteed within the "
                         "current invocation. Do not assume a later user message can resume "
                         "the same session.")
        return "\n".join(lines).strip()

    def _enabled_for_invocation(self, ctx: InvocationContext, request: LlmRequest) -> bool:
        if self._enabled_resolver is not None:
            return bool(self._enabled_resolver(ctx))
        return self._has_tool(request, "workspace_exec")

    def _session_tools_for_invocation(self, ctx: InvocationContext, request: LlmRequest) -> bool:
        if self._sessions_resolver is not None:
            return bool(self._sessions_resolver(ctx))
        if self._session_tools:
            return True
        return self._has_tool(request, "workspace_write_stdin") and self._has_tool(request, "workspace_kill_session")

    def _has_skills_repo(self, ctx: InvocationContext) -> bool:
        if self._repo_resolver is not None:
            return self._repo_resolver(ctx) is not None
        return self._static_skills_repo

    def _supports_artifact_save(self, request: LlmRequest) -> bool:
        return self._has_tool(request, "workspace_save_artifact")

    @staticmethod
    def _has_tool(request: LlmRequest, tool_name: str) -> bool:
        if request is None or request.config is None or not request.config.tools:
            return False
        target = (tool_name or "").strip()
        if not target:
            return False
        for tool in request.config.tools:
            declarations = getattr(tool, "function_declarations", None) or []
            for declaration in declarations:
                name = getattr(declaration, "name", "")
                if (name or "").strip() == target:
                    return True
        return False


def set_workspace_exec_processor_parameters(agent_context: AgentContext, parameters: dict[str, Any]) -> None:
    """Set the parameters of a workspace exec processor by agent context.

    Args:
        agent_context: AgentContext object
        parameters: Parameters to set
    """
    skill_config = get_skill_config(agent_context)
    skill_config["workspace_exec_processor"].update(parameters)
    set_skill_config(agent_context, skill_config)


def get_workspace_exec_processor_parameters(agent_context: AgentContext) -> dict[str, Any]:
    """Get the parameters of a workspace exec processor.

    Args:
        agent_context: AgentContext object

    Returns:
        Parameters of the workspace exec processor
    """
    skill_config = get_skill_config(agent_context)
    return skill_config["workspace_exec_processor"]
