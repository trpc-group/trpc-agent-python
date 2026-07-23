# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""End-to-end HITL propagation tests for GraphAgent agent nodes."""

from typing import AsyncGenerator
from typing import List

import pytest
from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.dsl.graph import END
from trpc_agent_sdk.dsl.graph import START
from trpc_agent_sdk.dsl.graph import GraphAgent
from trpc_agent_sdk.dsl.graph import State
from trpc_agent_sdk.dsl.graph import StateGraph
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.events import LongRunningEvent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.sessions import SqlSessionService
from trpc_agent_sdk.teams import TeamAgent
from trpc_agent_sdk.teams.core import TEAM_STATE_KEY
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part


class HitlState(State, total=False):
    after_child: bool


class StubModel(LLMModel):
    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"stub-model"]

    async def _generate_async_impl(
        self,
        request: LlmRequest,
        stream: bool = False,
        ctx=None,
    ) -> AsyncGenerator[LlmResponse, None]:
        del request, stream, ctx
        yield LlmResponse(content=None)

    def validate_request(self, request: LlmRequest) -> None:
        del request


class TwoRoundClarifyingAgent(BaseAgent):
    """Child agent that requires two FunctionResponses before completing."""

    received_call_ids: list[str] = []

    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        response = self._function_response(ctx.user_content)
        if response is not None:
            self.received_call_ids.append(str(response.id))
            if response.response.get("answer") == "done":
                yield Event(
                    invocation_id=ctx.invocation_id,
                    author=self.name,
                    branch=ctx.branch,
                    content=Content(role="model", parts=[Part.from_text(text="clarification-complete")]),
                )
                return
            call_id = "child-question-2"
        else:
            call_id = "child-question-1"

        call = FunctionCall(
            id=call_id,
            name="ask_clarification",
            args={"round": 1 if call_id.endswith("1") else 2},
        )
        yield LongRunningEvent(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            function_call=call,
            function_response=FunctionResponse(
                id=call.id,
                name=call.name,
                response={"status": "pending"},
            ),
        )

    @staticmethod
    def _function_response(content: Content | None) -> FunctionResponse | None:
        if content is None:
            return None
        for part in content.parts or []:
            if part.function_response is not None:
                return part.function_response
        return None


class TeamHitlLeader(BaseAgent):
    """Leader double that verifies TeamAgent receives the original response ID."""

    received_call_ids: list[str] = []

    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        response = TwoRoundClarifyingAgent._function_response(ctx.user_content)
        if response is not None:
            self.received_call_ids.append(str(response.id))
            yield Event(
                invocation_id=ctx.invocation_id,
                author=self.name,
                branch=ctx.branch,
                content=Content(role="model", parts=[Part.from_text(text="team-clarification-complete")]),
            )
            return

        call = FunctionCall(
            id="team-question-1",
            name="ask_clarification",
            args={"question": "approve team plan?"},
        )
        yield LongRunningEvent(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            function_call=call,
            function_response=FunctionResponse(
                id=call.id,
                name=call.name,
                response={"status": "pending"},
            ),
        )


def _user_text(text: str) -> Content:
    return Content(role="user", parts=[Part.from_text(text=text)])


def _tool_response(event: LongRunningEvent, answer: str) -> Content:
    return Content(
        role="user",
        parts=[
            Part(
                function_response=FunctionResponse(
                    id=event.function_call.id,
                    name=event.function_call.name,
                    response={"answer": answer},
                )
            )
        ],
    )


async def _run(runner: Runner, message: Content) -> list[Event]:
    return [
        event
        async for event in runner.run_async(
            user_id="user-1",
            session_id="session-1",
            new_message=message,
        )
    ]


@pytest.mark.asyncio
async def test_agent_node_long_running_interrupts_parent_and_resumes_multiple_rounds():
    child = TwoRoundClarifyingAgent(name="clarifier")

    async def after_child(state: HitlState) -> dict[str, bool]:
        del state
        return {"after_child": True}

    graph = StateGraph(HitlState)
    graph.add_agent_node("clarify", child, isolated_messages=True)
    graph.add_node("after_child", after_child)
    graph.add_edge(START, "clarify")
    graph.add_edge("clarify", "after_child")
    graph.add_edge("after_child", END)

    service = InMemorySessionService()
    runner = Runner(
        app_name="agent-node-hitl-test",
        agent=GraphAgent(name="workflow", graph=graph.compile()),
        session_service=service,
        close_session_service_on_close=False,
    )

    first_events = await _run(runner, _user_text("start"))
    first_pending = next(event for event in first_events if isinstance(event, LongRunningEvent))
    assert first_pending.function_call.name == "ask_clarification"
    assert first_pending.function_call.args == {"round": 1, "status": "pending"}
    first_session = await service.get_session(
        app_name="agent-node-hitl-test",
        user_id="user-1",
        session_id="session-1",
    )
    assert first_session is not None
    assert first_session.state.get("after_child") is not True

    second_events = await _run(runner, _tool_response(first_pending, "again"))
    second_pending = next(event for event in second_events if isinstance(event, LongRunningEvent))
    assert second_pending.function_call.name == "ask_clarification"
    second_session = await service.get_session(
        app_name="agent-node-hitl-test",
        user_id="user-1",
        session_id="session-1",
    )
    assert second_session is not None
    assert second_session.state.get("after_child") is not True

    await _run(runner, _tool_response(second_pending, "done"))
    completed_session = await service.get_session(
        app_name="agent-node-hitl-test",
        user_id="user-1",
        session_id="session-1",
    )
    assert completed_session is not None
    assert completed_session.state.get("after_child") is True
    assert child.received_call_ids == ["child-question-1", "child-question-2"]

    await runner.close()
    await service.close()


@pytest.mark.asyncio
async def test_team_agent_leader_hitl_survives_service_restart(tmp_path):
    async def after_team(state: HitlState) -> dict[str, bool]:
        del state
        return {"after_child": True}

    def build_runner(service: SqlSessionService) -> tuple[Runner, TeamHitlLeader]:
        member = TwoRoundClarifyingAgent(name="unused_member")
        team = TeamAgent(
            name="development_team",
            model=StubModel(model_name="stub-model"),
            members=[member],
        )
        leader = TeamHitlLeader(name="development_team")
        team.__pydantic_private__["_leader_agent"] = leader
        graph = StateGraph(HitlState)
        graph.add_agent_node("development", team, isolated_messages=True)
        graph.add_node("after_team", after_team)
        graph.add_edge(START, "development")
        graph.add_edge("development", "after_team")
        graph.add_edge("after_team", END)
        return (
            Runner(
                app_name="agent-node-hitl-test",
                agent=GraphAgent(name="team_workflow", graph=graph.compile()),
                session_service=service,
                close_session_service_on_close=False,
            ),
            leader,
        )

    db_url = f"sqlite:///{tmp_path / 'agent-node-hitl.sqlite'}"
    service = SqlSessionService(db_url, is_async=False)
    runner, _ = build_runner(service)

    first_events = await _run(runner, _user_text("start team"))
    pending = next(event for event in first_events if isinstance(event, LongRunningEvent))
    first_session = await service.get_session(
        app_name="agent-node-hitl-test",
        user_id="user-1",
        session_id="session-1",
    )
    assert first_session is not None
    assert first_session.state.get("after_child") is not True
    assert TEAM_STATE_KEY in first_session.state

    await runner.close()
    await service.close()

    service = SqlSessionService(db_url, is_async=False)
    runner, resumed_leader = build_runner(service)
    await _run(runner, _tool_response(pending, "done"))
    completed_session = await service.get_session(
        app_name="agent-node-hitl-test",
        user_id="user-1",
        session_id="session-1",
    )
    assert completed_session is not None
    assert completed_session.state.get("after_child") is True
    assert resumed_leader.received_call_ids == ["team-question-1"]

    await runner.close()
    await service.close()
