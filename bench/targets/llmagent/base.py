"""Benchmark target for trpc-agent-python's LlmAgent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from typing import AsyncIterator

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import BaseSessionService
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from trpc_agent_bench.core import AgentBenchConf
from trpc_agent_bench.core import AgentHandle
from trpc_agent_bench.core import AgentTarget
from trpc_agent_bench.core import ToolsFactory
from trpc_agent_bench.core import resolve_tools

INSTRUCTION = "You are a deterministic benchmark agent. Answer briefly."


@dataclass
class PreparedLlmAgent:
    model: OpenAIModel
    session_service: BaseSessionService


class LlmAgentHandle(AgentHandle):

    def __init__(
        self,
        *,
        agent: LlmAgent,
        session_service: BaseSessionService,
    ) -> None:
        self.agent = agent
        self.session_service = session_service

    async def run_turn(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        message: str,
    ) -> AsyncIterator[Any]:
        await self._ensure_session(app_name, user_id, session_id)
        runner = Runner(
            app_name=app_name,
            agent=self.agent,
            session_service=self.session_service,
            enable_post_turn_processing=False,
            close_session_service_on_close=False,
        )
        try:
            async for event in runner.run_async(
                    user_id=user_id,
                    session_id=session_id,
                    new_message=Content(parts=[Part.from_text(text=message)]),
            ):
                yield event
        finally:
            await runner.close()

    async def _ensure_session(
        self,
        app_name: str,
        user_id: str,
        session_id: str,
    ) -> None:
        session = await self.session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if session is None:
            await self.session_service.create_session(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
                state={},
            )


class LlmAgentTarget(AgentTarget):
    name = "llm_agent"
    import_module = "trpc_agent_sdk"

    def create_session_service(self) -> BaseSessionService:
        return InMemorySessionService()

    async def prepare(self, *, conf: AgentBenchConf) -> PreparedLlmAgent:
        if conf.base_url is None:
            raise RuntimeError("AgentBenchConf.base_url is not set; call setup() first")

        session_service = self.create_session_service()
        try:
            model = OpenAIModel(
                model_name="bench-model",
                api_key="bench",
                base_url=conf.base_url,
            )
        except Exception:
            await session_service.close()
            raise

        return PreparedLlmAgent(
            model=model,
            session_service=session_service,
        )

    async def instantiate(
        self,
        prepared: PreparedLlmAgent,
        *,
        tools_factory: ToolsFactory,
    ) -> LlmAgent:
        tools = await resolve_tools(tools_factory)
        return LlmAgent(
            name="bench_llm_agent",
            description="LlmAgent benchmark target.",
            model=prepared.model,
            instruction=INSTRUCTION,
            tools=tools,
            parallel_tool_calls=True,
        )

    async def handle(
        self,
        prepared: PreparedLlmAgent,
        *,
        tools_factory: ToolsFactory,
    ) -> AgentHandle:
        agent = await self.instantiate(prepared, tools_factory=tools_factory)
        return LlmAgentHandle(
            agent=agent,
            session_service=prepared.session_service,
        )

    async def cleanup(self, prepared: PreparedLlmAgent) -> None:
        await prepared.session_service.close()


TARGET = LlmAgentTarget()
