# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run one real LLM agent through every protected execution entry point."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import UnsafeLocalCodeExecutor
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.skills import SkillToolSet
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools import BashTool
from trpc_agent_sdk.tools import MCPToolset
from trpc_agent_sdk.tools import McpStdioServerParameters
from trpc_agent_sdk.tools import StdioConnectionParams
from trpc_agent_sdk.tools.safety import JsonlAuditSink
from trpc_agent_sdk.tools.safety import SafetyGuardedCodeExecutor
from trpc_agent_sdk.tools.safety import ToolSafetyFilter
from trpc_agent_sdk.tools.safety import ToolSafetyViolation
from trpc_agent_sdk.tools.safety import ToolScriptSafetyGuard
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

EXAMPLE_DIR = Path(__file__).resolve().parent
POLICY_PATH = EXAMPLE_DIR / "tool_safety_policy.yaml"
SKILLS_PATH = EXAMPLE_DIR / "skills"
MCP_SERVER_PATH = EXAMPLE_DIR / "mcp_server.py"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL_NAME = "deepseek-v4-flash"
DEFAULT_TIMEOUT_SECONDS = 10
MCP_ENV_KEYS = ("LD_LIBRARY_PATH", "PATH", "PYTHONPATH", "SYSTEMROOT", "WINDIR")
PROTECTED_SKILL_TOOLS = frozenset({"skill_run", "skill_exec", "workspace_exec"})
APP_NAME = "tool_safety_real_agent"
USER_ID = "safety_demo_user"
INSTRUCTION = """
You are a deterministic Tool Script Safety Guard demo agent.
Follow the requested entry point and arguments exactly. Never replace it with another tool.
After one attempt, report the returned decision/result. Do not retry blocked calls.
For skill scenarios, call skill_load for safety-demo before skill_run.
"""
SCENARIOS = {
    "tool-allow":
    "Call Bash exactly once with command `echo tool-allow`.",
    "tool-review":
    "Call Bash exactly once with command `echo tool-review | cat`.",
    "tool-deny":
    "Call Bash exactly once with command `rm -rf safety-demo-trash`.",
    "mcp-allow":
    "Call execute_command exactly once with command `echo mcp-allow`.",
    "mcp-review":
    "Call execute_command exactly once with command `uname -a`.",
    "mcp-deny":
    "Call execute_command exactly once with command `rm -rf safety-demo-trash`.",
    "skill-allow":
    "Load safety-demo, then call skill_run with command `echo skill-allow`.",
    "skill-review":
    "Load safety-demo, then call skill_run with command `echo skill-review | cat`.",
    "skill-deny":
    "Load safety-demo, then call skill_run with command `rm -rf safety-demo-trash`.",
    "executor-allow":
    "Call execute_code exactly once with code `print('executor-allow')`.",
    "executor-review": ("Call execute_code exactly once with code "
                        "`import subprocess; subprocess.run(['echo', 'executor-review'])`."),
    "executor-deny": ("Call execute_code exactly once with code "
                      "`import shutil; shutil.rmtree('safety-demo-trash')`."),
}


class PortableLocalCodeExecutor(UnsafeLocalCodeExecutor):
    """Use the running interpreter when a python3 binary is unavailable."""

    def _build_command_args(self, language: str, file_path: Path) -> list[str]:
        if language.lower() in {"python", "py", "python3"}:
            return [sys.executable, str(file_path)]
        return super()._build_command_args(language, file_path)


class SafetyFilteredSkillToolSet(SkillToolSet):
    """Attach one safety filter to every command-running Skill tool."""

    def __init__(self, safety_filter: ToolSafetyFilter):
        super().__init__(paths=[str(SKILLS_PATH)])
        self._safety_filter = safety_filter

    async def get_tools(self, invocation_context=None):
        tools = await super().get_tools(invocation_context)
        for tool in tools:
            if tool.name in PROTECTED_SKILL_TOOLS:
                tool.add_one_filter(self._safety_filter)
        return tools


class CodeExecutorTool(BaseTool):
    """Expose a guarded CodeExecutor as a model-callable tool."""

    def __init__(self, executor: SafetyGuardedCodeExecutor):
        super().__init__(
            name="execute_code",
            description="Execute Python code through SafetyGuardedCodeExecutor.",
        )
        self._executor = executor

    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "code": Schema(type=Type.STRING, description="Python source code."),
                },
                required=["code"],
            ),
        )

    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        try:
            result = await self._executor.execute_code(
                tool_context,
                CodeExecutionInput(code=str(args.get("code", ""))),
            )
        except ToolSafetyViolation as error:
            return error.report.as_dict()
        return {
            "outcome": str(result.outcome),
            "output": result.output,
        }


def _model() -> OpenAIModel:
    api_key = os.getenv("TRPC_AGENT_API_KEY", "")
    if not api_key:
        raise ValueError("TRPC_AGENT_API_KEY must be set")
    return OpenAIModel(
        model_name=os.getenv("TRPC_AGENT_MODEL_NAME", DEFAULT_MODEL_NAME),
        api_key=api_key,
        base_url=os.getenv("TRPC_AGENT_BASE_URL", DEFAULT_BASE_URL),
    )


def _mcp_toolset(safety_filter: ToolSafetyFilter) -> MCPToolset:
    child_env = {key: os.environ[key] for key in MCP_ENV_KEYS if key in os.environ}
    server = McpStdioServerParameters(
        command=sys.executable,
        args=[str(MCP_SERVER_PATH)],
        env=child_env,
    )
    return MCPToolset(
        connection_params=StdioConnectionParams(
            server_params=server,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        ),
        filters=[safety_filter],
    )


def create_agent(audit_path: Path) -> LlmAgent:
    """Build one real agent covering Tool, Skill, MCP Tool and CodeExecutor."""
    audit = JsonlAuditSink(audit_path)
    guard = ToolScriptSafetyGuard.from_policy(POLICY_PATH)
    safety_filter = ToolSafetyFilter(guard, audit)
    bash = BashTool(cwd=str(EXAMPLE_DIR))
    bash.add_one_filter(safety_filter)
    executor = SafetyGuardedCodeExecutor(
        delegate=PortableLocalCodeExecutor(timeout=DEFAULT_TIMEOUT_SECONDS),
        guard=guard,
        audit_sink=audit,
    )
    skill_toolset = SafetyFilteredSkillToolSet(safety_filter)
    return LlmAgent(
        name="tool_safety_demo",
        description="Agent demonstrating guarded execution entry points.",
        model=_model(),
        instruction=INSTRUCTION,
        tools=[
            bash,
            skill_toolset,
            _mcp_toolset(safety_filter),
            CodeExecutorTool(executor),
        ],
        skill_repository=skill_toolset.repository,
    )


def _print_event(event) -> None:
    if not event.content or not event.content.parts:
        return
    for part in event.content.parts:
        if part.function_call:
            print(f"CALL {part.function_call.name}")
        elif part.function_response:
            response = part.function_response.response
            if isinstance(response, dict):
                response = {
                    "decision": response.get("decision"),
                    "execution_blocked": response.get("execution_blocked"),
                    "return_code": response.get("return_code"),
                }
            print(f"RESULT {part.function_response.name}: {response}")
        elif part.text and not part.thought:
            print(part.text, end="" if event.partial else "\n")


async def run_scenarios(names: list[str], audit_path: Path) -> None:
    agent = create_agent(audit_path)
    sessions = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=agent, session_service=sessions)
    try:
        for name in names:
            print(f"\n=== {name} ===")
            session_id = str(uuid.uuid4())
            await sessions.create_session(
                app_name=APP_NAME,
                user_id=USER_ID,
                session_id=session_id,
            )
            message = Content(parts=[Part.from_text(text=SCENARIOS[name])])
            async for event in runner.run_async(
                    user_id=USER_ID,
                    session_id=session_id,
                    new_message=message,
            ):
                _print_event(event)
    finally:
        await runner.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", nargs="?", choices=["all", *sorted(SCENARIOS)])
    parser.add_argument("--audit", type=Path, default=EXAMPLE_DIR / "real_agent_audit.jsonl")
    parser.add_argument("--list-scenarios", action="store_true")
    return parser


def main() -> None:
    load_dotenv()
    args = _parser().parse_args()
    if args.list_scenarios:
        print("\n".join(sorted(SCENARIOS)))
        return
    if not args.scenario:
        raise SystemExit("scenario is required unless --list-scenarios is used")
    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    asyncio.run(run_scenarios(names, args.audit))


if __name__ == "__main__":
    main()
