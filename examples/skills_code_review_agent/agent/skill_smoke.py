"""SDK-native skill_load/skill_run smoke helpers for the code-review Skill."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from typing import AsyncGenerator

from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.code_executors import create_local_workspace_runtime
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.context import create_agent_context
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from .native_agent import create_code_review_skill_tool_set

EXAMPLE_DIR = Path(__file__).resolve().parents[1]


class _SkillSmokeAgent(BaseAgent):
    """Minimal agent used only to build a valid tool InvocationContext."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            content=Content(parts=[Part(text="skill smoke context")]),
        )


async def run_code_review_skill_smoke(*, diff_text: str | None = None) -> dict[str, Any]:
    """Load the code-review Skill and execute one Skill script through skill_run."""
    runtime = create_local_workspace_runtime()
    toolset, repository = create_code_review_skill_tool_set(workspace_runtime=runtime)
    tools = {tool.name: tool for tool in await toolset.get_tools()}

    service = InMemorySessionService()
    try:
        session = await service.create_session(
            app_name="skills-code-review-agent",
            user_id="smoke",
            session_id="skill-smoke",
        )
        ctx = InvocationContext(
            session_service=service,
            invocation_id="skill-smoke",
            agent=_SkillSmokeAgent(name="skill_smoke_agent"),
            agent_context=create_agent_context(),
            session=session,
        )
        load_result = await tools["skill_load"].run_async(
            tool_context=ctx,
            args={"skill_name": "code-review"},
        )
        if diff_text is None:
            diff_text = (EXAMPLE_DIR / "fixtures" / "clean.diff").read_text(encoding="utf-8")
        run_result = await tools["skill_run"].run_async(
            tool_context=ctx,
            args={
                "skill": "code-review",
                "command": f"{sys.executable} scripts/diff_summary.py",
                "stdin": diff_text,
                "timeout": 5,
            },
        )
        return {
            "skill_loaded": "code-review" in repository.skill_list(),
            "load_result": load_result,
            "run_result": run_result,
        }
    finally:
        await service.close()
