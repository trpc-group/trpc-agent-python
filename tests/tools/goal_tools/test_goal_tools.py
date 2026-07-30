# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the Goal capability (:mod:`trpc_agent_sdk.tools.goal_tools`)."""

from __future__ import annotations

import asyncio

import pytest

from trpc_agent_sdk.agents._base_agent import BaseAgent
from trpc_agent_sdk.agents._constants import TRPC_AGENT_RUNNING_KEY
from trpc_agent_sdk.context import InvocationContext, create_agent_context
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LlmRequest, LlmResponse
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools.goal_tools import (
    DEFAULT_STATE_KEY_PREFIX,
    GoalCreateTool,
    GoalGetTool,
    GoalOptions,
    GoalRecord,
    GoalStatus,
    GoalToolSet,
    GoalUpdateTool,
    decode_goal,
    encode_goal,
    get_goal_record,
    render_goal,
    setup_goal,
    start_goal,
    state_key,
)
from trpc_agent_sdk.tools.goal_tools._setup import (
    _REMINDER_PENDING_KEY,
    _RETRY_COUNT_KEY,
    _GoalCallbacks,
)
from trpc_agent_sdk.tools.goal_tools._prompt import _GUIDANCE_MARKER
from trpc_agent_sdk.types import Content, EventActions, Part

AGENT_NAME = "goal_agent"


class _StubAgent(BaseAgent):
    async def _run_async_impl(self, ctx):
        yield


@pytest.fixture
def bundle():
    service = InMemorySessionService()
    session = asyncio.run(service.create_session(app_name="test", user_id="u1", session_id="s1"))
    agent = _StubAgent(name=AGENT_NAME)
    ctx = InvocationContext(
        session_service=service,
        invocation_id="inv-1",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
        branch="",
    )
    return service, session, agent, ctx


async def _create(ctx, objective):
    return await GoalCreateTool()._run_async_impl(tool_context=ctx, args={"objective": objective})


async def _update(ctx, status):
    return await GoalUpdateTool()._run_async_impl(tool_context=ctx, args={"status": status})


async def _refresh_ctx(service, ctx, *, app_name="test", user_id="u1", session_id="s1"):
    """Reload session state into an invocation context after host-side writes."""
    ctx.session = await service.get_session(app_name=app_name, user_id=user_id, session_id=session_id)
    ctx.callback_state = None


async def _seed_goal(service, *, app_name, user_id, session_id, objective, branch=""):
    """Application-layer goal write used by tests (appends a ``state_delta`` event)."""
    import time
    import uuid

    session = await service.get_session(app_name=app_name, user_id=user_id, session_id=session_id)
    now = int(time.time())
    record = GoalRecord(
        id=uuid.uuid4().hex,
        objective=objective,
        status=GoalStatus.ACTIVE,
        createdAtUnix=now,
        updatedAtUnix=now,
    )
    key = state_key(DEFAULT_STATE_KEY_PREFIX, branch)
    event = Event(
        invocation_id="goal-" + uuid.uuid4().hex,
        author="goal",
        branch=branch or None,
        actions=EventActions(state_delta={key: record.model_dump_json(by_alias=True)}),
    )
    await service.append_event(session, event)
    return record


def _final_text_response(text: str = "All done!") -> LlmResponse:
    return LlmResponse(content=Content(role="model", parts=[Part.from_text(text=text)]), partial=False)


# --------------------------------------------------------------------------- #
# Tools                                                                        #
# --------------------------------------------------------------------------- #
class TestGoalCreate:
    @pytest.mark.asyncio
    async def test_creates_active_goal(self, bundle):
        _, _, agent, ctx = bundle
        res = await _create(ctx, "Refactor the whole billing service")
        assert res["goal"]["status"] == "active"
        assert res["goal"]["objective"] == "Refactor the whole billing service"
        assert state_key(DEFAULT_STATE_KEY_PREFIX, agent.name) in ctx.state._delta

    @pytest.mark.asyncio
    async def test_rejects_empty_objective(self, bundle):
        _, _, _, ctx = bundle
        res = await GoalCreateTool()._run_async_impl(tool_context=ctx, args={"objective": "   "})
        assert "error" in res

    @pytest.mark.asyncio
    async def test_rejects_duplicate_active(self, bundle):
        _, _, _, ctx = bundle
        await _create(ctx, "first")
        res = await _create(ctx, "second")
        assert "error" in res
        assert "active goal already exists" in res["error"]

    @pytest.mark.asyncio
    async def test_can_recreate_after_terminal(self, bundle):
        _, _, _, ctx = bundle
        await _create(ctx, "first")
        await _update(ctx, "complete")
        res = await _create(ctx, "second")
        assert "error" not in res
        assert res["goal"]["objective"] == "second"


class TestGoalUpdate:
    @pytest.mark.asyncio
    async def test_complete_sets_terminal_time(self, bundle):
        _, _, _, ctx = bundle
        await _create(ctx, "obj")
        res = await _update(ctx, "complete")
        assert res["goal"]["status"] == "complete"
        assert res["goal"]["terminalAtUnix"] is not None

    @pytest.mark.asyncio
    async def test_blocked_is_terminal(self, bundle):
        _, _, _, ctx = bundle
        await _create(ctx, "obj")
        res = await _update(ctx, "blocked")
        assert res["goal"]["status"] == "blocked"

    @pytest.mark.asyncio
    async def test_rejects_active_status(self, bundle):
        _, _, _, ctx = bundle
        await _create(ctx, "obj")
        res = await GoalUpdateTool()._run_async_impl(tool_context=ctx, args={"status": "active"})
        assert "error" in res

    @pytest.mark.asyncio
    async def test_rejects_when_no_goal(self, bundle):
        _, _, _, ctx = bundle
        res = await _update(ctx, "complete")
        assert "error" in res

    @pytest.mark.asyncio
    async def test_terminal_cannot_be_changed(self, bundle):
        _, _, _, ctx = bundle
        await _create(ctx, "obj")
        await _update(ctx, "complete")
        res = await _update(ctx, "blocked")
        assert "error" in res
        assert "terminal" in res["error"]


class TestStartGoal:
    @pytest.mark.asyncio
    async def test_writes_active_goal_to_existing_session(self):
        service = InMemorySessionService()
        await service.create_session(app_name="test", user_id="u1", session_id="s1")

        goal = await start_goal(
            service,
            app_name="test",
            user_id="u1",
            session_id="s1",
            objective="Ship the feature",
            agent_name=AGENT_NAME,
        )

        assert goal.status == GoalStatus.ACTIVE
        assert goal.objective == "Ship the feature"
        assert goal.id

        session = await service.get_session(app_name="test", user_id="u1", session_id="s1")
        stored = get_goal_record(session, branch=AGENT_NAME)
        assert stored is not None
        assert stored.id == goal.id
        assert stored.objective == "Ship the feature"
        assert session.state[state_key(DEFAULT_STATE_KEY_PREFIX, AGENT_NAME)] == encode_goal(goal)

    @pytest.mark.asyncio
    async def test_creates_session_when_missing(self):
        service = InMemorySessionService()

        goal = await start_goal(
            service,
            app_name="test",
            user_id="u1",
            session_id="brand-new-session",
            objective="Bootstrap task",
            agent_name=AGENT_NAME,
        )

        assert goal.status == GoalStatus.ACTIVE
        session = await service.get_session(app_name="test", user_id="u1", session_id="brand-new-session")
        assert session is not None
        stored = get_goal_record(session, branch=AGENT_NAME)
        assert stored is not None
        assert stored.objective == "Bootstrap task"

    @pytest.mark.asyncio
    async def test_rejects_empty_objective(self):
        service = InMemorySessionService()
        with pytest.raises(ValueError, match="non-empty"):
            await start_goal(
                service,
                app_name="test",
                user_id="u1",
                session_id="s1",
                objective="   ",
            )

    @pytest.mark.asyncio
    async def test_strips_objective_whitespace(self):
        service = InMemorySessionService()

        goal = await start_goal(
            service,
            app_name="test",
            user_id="u1",
            session_id="trim-session",
            objective="  trimmed objective  ",
        )

        assert goal.objective == "trimmed objective"

    @pytest.mark.asyncio
    async def test_replaces_existing_goal(self):
        service = InMemorySessionService()
        await service.create_session(app_name="test", user_id="u1", session_id="s1")

        first = await start_goal(
            service,
            app_name="test",
            user_id="u1",
            session_id="s1",
            objective="first",
            agent_name=AGENT_NAME,
        )
        second = await start_goal(
            service,
            app_name="test",
            user_id="u1",
            session_id="s1",
            objective="second",
            agent_name=AGENT_NAME,
        )

        assert second.id != first.id
        assert second.objective == "second"
        session = await service.get_session(app_name="test", user_id="u1", session_id="s1")
        stored = get_goal_record(session, branch=AGENT_NAME)
        assert stored is not None
        assert stored.objective == "second"

    @pytest.mark.asyncio
    async def test_honours_custom_state_key_prefix(self):
        service = InMemorySessionService()
        prefix = "custom_goal"

        await start_goal(
            service,
            app_name="test",
            user_id="u1",
            session_id="custom-prefix",
            objective="scoped objective",
            state_key_prefix=prefix,
            agent_name="worker",
        )

        session = await service.get_session(app_name="test", user_id="u1", session_id="custom-prefix")
        assert get_goal_record(session, branch="worker", prefix=prefix) is not None
        assert get_goal_record(session, branch="worker", prefix=DEFAULT_STATE_KEY_PREFIX) is None

    @pytest.mark.asyncio
    async def test_create_goal_rejects_after_start_goal(self, bundle):
        service, _, agent, ctx = bundle
        await start_goal(
            service,
            app_name="test",
            user_id="u1",
            session_id="s1",
            objective="host goal",
            agent_name=agent.name,
        )
        await _refresh_ctx(service, ctx)

        res = await _create(ctx, "model goal")
        assert "error" in res
        assert "active goal already exists" in res["error"]

    @pytest.mark.asyncio
    async def test_get_goal_reads_host_injected_goal(self, bundle):
        service, _, agent, ctx = bundle
        await start_goal(
            service,
            app_name="test",
            user_id="u1",
            session_id="s1",
            objective="host objective",
            agent_name=agent.name,
        )
        await _refresh_ctx(service, ctx)

        res = await GoalGetTool()._run_async_impl(tool_context=ctx, args={})
        assert res["goal"]["objective"] == "host objective"
        assert res["goal"]["status"] == "active"

    @pytest.mark.asyncio
    async def test_enforcement_intercepts_premature_final(self, bundle):
        service, _, agent, ctx = bundle
        await start_goal(
            service,
            app_name="test",
            user_id="u1",
            session_id="s1",
            objective="finish the job",
            agent_name=agent.name,
        )
        await _refresh_ctx(service, ctx)

        cb = _GoalCallbacks(GoalOptions())
        replaced = await cb.after_model(ctx, _final_text_response())

        assert replaced is not None
        assert replaced.partial is True
        assert ctx.agent_context.metadata[TRPC_AGENT_RUNNING_KEY] is True


class TestGoalGet:
    @pytest.mark.asyncio
    async def test_no_goal(self, bundle):
        _, _, _, ctx = bundle
        res = await GoalGetTool()._run_async_impl(tool_context=ctx, args={})
        assert "goal" not in res
        assert "No session goal" in res["message"]

    @pytest.mark.asyncio
    async def test_returns_current(self, bundle):
        _, _, _, ctx = bundle
        await _create(ctx, "obj")
        res = await GoalGetTool()._run_async_impl(tool_context=ctx, args={})
        assert res["goal"]["objective"] == "obj"


# --------------------------------------------------------------------------- #
# Enforcement callbacks                                                        #
# --------------------------------------------------------------------------- #
class TestEnforcement:
    @pytest.mark.asyncio
    async def test_before_model_injects_guidance_once(self, bundle):
        _, _, _, ctx = bundle
        cb = _GoalCallbacks(GoalOptions())
        request = LlmRequest()
        await cb.before_model(ctx, request)
        await cb.before_model(ctx, request)
        text = str(request.config.system_instruction)
        assert text.count(_GUIDANCE_MARKER) == 1

    @pytest.mark.asyncio
    async def test_premature_final_triggers_rerun(self, bundle):
        _, _, _, ctx = bundle
        events = []
        cb = _GoalCallbacks(GoalOptions(on_retry=events.append))
        await _create(ctx, "obj")

        replaced = await cb.after_model(ctx, _final_text_response())

        assert replaced is not None
        assert replaced.partial is True
        meta = ctx.agent_context.metadata
        assert meta[TRPC_AGENT_RUNNING_KEY] is True
        assert meta[_RETRY_COUNT_KEY] == 1
        assert meta[_REMINDER_PENDING_KEY] is True
        assert len(events) == 1 and events[0].reason == "blocked"

    @pytest.mark.asyncio
    async def test_nudge_appended_on_next_turn(self, bundle):
        _, _, _, ctx = bundle
        cb = _GoalCallbacks(GoalOptions())
        await _create(ctx, "ship the feature")
        await cb.after_model(ctx, _final_text_response())

        request = LlmRequest()
        await cb.before_model(ctx, request)
        assert len(request.contents) == 1
        nudge_text = request.contents[0].parts[0].text
        assert "ship the feature" in nudge_text
        assert "attempt 1" in nudge_text
        # reminder cleared after being consumed
        assert ctx.agent_context.metadata[_REMINDER_PENDING_KEY] is False

    @pytest.mark.asyncio
    async def test_fail_open_after_max_retries(self, bundle):
        _, _, _, ctx = bundle
        events = []
        cb = _GoalCallbacks(GoalOptions(max_retries=2, on_retry=events.append))
        await _create(ctx, "obj")

        assert await cb.after_model(ctx, _final_text_response()) is not None  # attempt 1
        assert await cb.after_model(ctx, _final_text_response()) is not None  # attempt 2
        # budget exhausted -> let the final response through (fail-open)
        passthrough = await cb.after_model(ctx, _final_text_response())
        assert passthrough is None
        assert ctx.agent_context.metadata[_RETRY_COUNT_KEY] == 0
        assert events[-1].reason == "exhausted"

    @pytest.mark.asyncio
    async def test_partial_chunk_passes_through(self, bundle):
        _, _, _, ctx = bundle
        cb = _GoalCallbacks(GoalOptions())
        await _create(ctx, "obj")
        partial = LlmResponse(content=Content(role="model", parts=[Part.from_text(text="thinking")]), partial=True)
        assert await cb.after_model(ctx, partial) is None
        assert TRPC_AGENT_RUNNING_KEY not in ctx.agent_context.metadata

    @pytest.mark.asyncio
    async def test_tool_call_response_passes_through(self, bundle):
        _, _, _, ctx = bundle
        cb = _GoalCallbacks(GoalOptions())
        await _create(ctx, "obj")
        tool_call = LlmResponse(
            content=Content(role="model", parts=[Part.from_function_call(name="update_goal", args={"status": "complete"})]),
            partial=False,
        )
        assert await cb.after_model(ctx, tool_call) is None

    @pytest.mark.asyncio
    async def test_no_interception_when_goal_terminal(self, bundle):
        _, _, _, ctx = bundle
        cb = _GoalCallbacks(GoalOptions())
        await _create(ctx, "obj")
        await _update(ctx, "complete")
        # terminal goal: final response is a legitimate wrap-up
        assert await cb.after_model(ctx, _final_text_response()) is None

    @pytest.mark.asyncio
    async def test_no_interception_when_no_goal(self, bundle):
        _, _, _, ctx = bundle
        cb = _GoalCallbacks(GoalOptions())
        assert await cb.after_model(ctx, _final_text_response()) is None


# --------------------------------------------------------------------------- #
# setup_goal / helpers                                                         #
# --------------------------------------------------------------------------- #
class TestSetupGoal:
    @pytest.mark.asyncio
    async def test_appends_toolset_and_chains_callbacks(self):
        from types import SimpleNamespace

        def prior_before(ctx, req):
            return None

        # setup_goal only touches ``tools`` and the two model-callback fields.
        agent = SimpleNamespace(tools=[], before_model_callback=prior_before, after_model_callback=None)
        setup_goal(agent)

        # toolset appended
        assert any(isinstance(t, GoalToolSet) for t in agent.tools)
        # callbacks chained (prior preserved + new appended)
        assert isinstance(agent.before_model_callback, list)
        assert prior_before in agent.before_model_callback
        assert len(agent.before_model_callback) == 2
        assert isinstance(agent.after_model_callback, list)
        tools = await GoalToolSet().get_tools()
        assert {t.name for t in tools} == {"get_goal", "create_goal", "update_goal"}


class TestPersistence:
    @pytest.mark.asyncio
    async def test_goal_survives_append_event_and_get_session(self, bundle):
        service, session, agent, ctx = bundle
        await _create(ctx, "obj")
        event = Event(
            invocation_id="inv-1",
            author=agent.name,
            content=Content(parts=[Part.from_text(text="tool result")]),
            actions=EventActions(state_delta=dict(ctx.event_actions.state_delta)),
        )
        await service.append_event(session, event)
        stored = await service.get_session(app_name="test", user_id="u1", session_id="s1")
        goal = get_goal_record(stored, branch=agent.name)
        assert goal is not None and goal.status == GoalStatus.ACTIVE


class TestHelpers:
    def test_state_key(self):
        assert state_key(DEFAULT_STATE_KEY_PREFIX, "") == "goal"
        assert state_key(DEFAULT_STATE_KEY_PREFIX, "agent") == "goal:agent"

    def test_decode_dirty_data_degrades_to_none(self):
        assert decode_goal("not json") is None
        assert decode_goal(None) is None

    def test_render_goal(self):
        assert render_goal(None) == "(no goal)"
        rec = GoalRecord(id="x", objective="do it", status=GoalStatus.ACTIVE, createdAtUnix=1, updatedAtUnix=1)
        rendered = render_goal(rec)
        assert "active" in rendered
        assert "do it" in rendered


class TestGoalToolSet:
    @pytest.mark.asyncio
    async def test_returns_three_tools(self):
        tools = await GoalToolSet().get_tools()
        assert {t.name for t in tools} == {"get_goal", "create_goal", "update_goal"}


# --------------------------------------------------------------------------- #
# End-to-end: validates the same-invocation re-run lever (B2) via the Runner.  #
# --------------------------------------------------------------------------- #
from typing import List  # noqa: E402

from trpc_agent_sdk.models import LLMModel, ModelRegistry  # noqa: E402
from trpc_agent_sdk.runners import Runner  # noqa: E402


class _ScriptedModel(LLMModel):
    """Premature-final on turn 1, ``update_goal(complete)`` on turn 2, final on turn 3."""

    calls: int = 0
    saw_nudge: List[bool] = []

    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"scripted-.*"]

    def validate_request(self, request):
        pass

    async def _generate_async_impl(self, request, stream=False, ctx=None):
        type(self).calls += 1
        n = type(self).calls
        nudged = any(
            (c.role == "user") and c.parts and any("goal reminder" in (p.text or "") for p in c.parts)
            for c in request.contents
        )
        type(self).saw_nudge.append(nudged)
        if n == 1:
            yield LlmResponse(
                content=Content(role="model", parts=[Part.from_text(text="I refactored one function. Done!")]),
                partial=False,
            )
        elif n == 2:
            part = Part.from_function_call(name="update_goal", args={"status": "complete"})
            part.function_call.id = "call-1"
            yield LlmResponse(content=Content(role="model", parts=[part]), partial=False)
        else:
            yield LlmResponse(
                content=Content(role="model", parts=[Part.from_text(text="The whole task is finished.")]),
                partial=False,
            )


class TestEndToEndRerun:
    @pytest.mark.asyncio
    async def test_premature_final_reruns_until_model_self_reports(self):
        from trpc_agent_sdk.agents import LlmAgent

        original = ModelRegistry._registry.copy()
        ModelRegistry.register(_ScriptedModel)
        _ScriptedModel.calls = 0
        _ScriptedModel.saw_nudge = []
        try:
            agent = LlmAgent(name="goal_e2e", model="scripted-1")
            setup_goal(agent, GoalOptions(max_retries=3))

            service = InMemorySessionService()
            runner = Runner(app_name="goal_app", agent=agent, session_service=service)
            await service.create_session(app_name="goal_app", user_id="u", session_id="sid")
            await _seed_goal(
                service, app_name="goal_app", user_id="u", session_id="sid",
                objective="Refactor the entire service", branch=agent.name,
            )

            async for _ in runner.run_async(
                user_id="u",
                session_id="sid",
                new_message=Content(role="user", parts=[Part.from_text(text="go")]),
            ):
                pass
            await runner.close()

            # The loop re-ran within ONE invocation: model called 3 times.
            assert _ScriptedModel.calls == 3
            # Turn 2 saw the nudge injected by before_model.
            assert _ScriptedModel.saw_nudge[1] is True
            # Goal ended up complete (model self-reported via update_goal).
            stored = await service.get_session(app_name="goal_app", user_id="u", session_id="sid")
            goal = get_goal_record(stored, branch=agent.name)
            assert goal is not None and goal.status == GoalStatus.COMPLETE
        finally:
            ModelRegistry._registry = original
