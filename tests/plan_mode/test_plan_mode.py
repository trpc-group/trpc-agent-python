# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.

from __future__ import annotations

import time
import uuid

import pytest

from trpc_agent_sdk.plan_mode import PlanStatus
from trpc_agent_sdk.plan_mode import decode_plan
from trpc_agent_sdk.plan_mode import encode_plan
from trpc_agent_sdk.plan_mode import get_plan_record
from trpc_agent_sdk.plan_mode import plan_to_task_subjects
from trpc_agent_sdk.plan_mode._lock import reset_locks_for_tests
from trpc_agent_sdk.plan_mode._models import PlanRecord
from trpc_agent_sdk.plan_mode._store import apply_approval_decision
from trpc_agent_sdk.plan_mode._store import apply_enter
from trpc_agent_sdk.plan_mode._store import apply_enter_decision
from trpc_agent_sdk.plan_mode._store import apply_request_enter
from trpc_agent_sdk.plan_mode._store import apply_request_exit
from trpc_agent_sdk.plan_mode._store import apply_update_content
from trpc_agent_sdk.sessions import InMemorySessionService


async def _enter_plan_mode_confirmed(ctx, objective: str = "x") -> None:
    from trpc_agent_sdk.plan_mode._long_running_tools import make_enter_plan_mode_tool
    from trpc_agent_sdk.plan_mode._long_running_tools import process_hitl_function_response

    tool = make_enter_plan_mode_tool()
    pending = await tool._run_async_impl(tool_context=ctx, args={"objective": objective})
    assert pending["status"] == "pending_enter"
    process_hitl_function_response(
        ctx,
        name="enter_plan_mode",
        response={
            "status": "approved",
            "reviewer_note": "ok",
        },
    )


class _FakeSession:

    def __init__(self, state: dict | None = None):
        self.state = state or {}


@pytest.fixture(autouse=True)
def _reset_plan_locks():
    reset_locks_for_tests()
    yield
    reset_locks_for_tests()


def test_encode_decode_roundtrip():
    record = PlanRecord(
        id="abc",
        status=PlanStatus.DRAFTING,
        objective="refactor auth",
        content="## Step 1\nDo thing",
        started_at_unix=1,
    )
    raw = encode_plan(record)
    decoded = decode_plan(raw)
    assert decoded is not None
    assert decoded.objective == "refactor auth"
    assert decoded.status == PlanStatus.DRAFTING


def test_request_enter_approve_and_reject():
    now = int(time.time())
    record, err, payload = apply_request_enter(None, objective="build feature", request_id="req-enter", now_unix=now)
    assert err is None
    assert record.status == PlanStatus.PENDING_ENTER
    assert payload["status"] == "pending_enter"

    record, err, result = apply_enter_decision(
        record,
        decision="approved",
        reviewer_note="go",
        now_unix=now,
    )
    assert err is None
    assert record.status == PlanStatus.EXPLORING
    assert result["status"] == "approved"

    record, err, payload = apply_request_enter(None, objective="another", request_id="req-enter-2", now_unix=now)
    assert err is None
    _, err, result = apply_enter_decision(
        record,
        decision="rejected",
        reviewer_note="not now",
        now_unix=now,
    )
    assert err is None
    assert result["status"] == "rejected"


def test_state_machine_enter_update_exit_approve():
    now = int(time.time())
    record, err = apply_enter(None, objective="build feature", now_unix=now)
    assert err is None
    assert record.status == PlanStatus.EXPLORING

    record, err = apply_update_content(
        record,
        content="# Plan\n## Step A",
        mode="replace",
        now_unix=now,
    )
    assert err is None
    assert record.status == PlanStatus.DRAFTING

    record, err, payload = apply_request_exit(
        record,
        summary="ready",
        request_id="req1",
        now_unix=now,
    )
    assert err is None
    assert record.status == PlanStatus.PENDING_APPROVAL
    assert payload["status"] == "pending_approval"

    record, err, result = apply_approval_decision(
        record,
        decision="approved",
        reviewer_note="lgtm",
        edited_content=None,
        now_unix=now,
    )
    assert err is None
    assert record.status == PlanStatus.APPROVED
    assert result["status"] == "approved"


def test_reject_returns_to_drafting():
    now = int(time.time())
    record, _ = apply_enter(None, objective="x", now_unix=now)
    record, _ = apply_update_content(record, content="plan body", mode="replace", now_unix=now)
    record, _, _ = apply_request_exit(record, summary="", request_id="r", now_unix=now)
    record, err, result = apply_approval_decision(
        record,
        decision="rejected",
        reviewer_note="need more detail",
        edited_content=None,
        now_unix=now,
    )
    assert err is None
    assert record.status == PlanStatus.DRAFTING
    assert result["status"] == "rejected"


def test_plan_to_task_subjects():
    record = PlanRecord(
        id="1",
        status=PlanStatus.APPROVED,
        objective="o",
        content="## Setup\n\n## Implement\n\nplain",
        started_at_unix=1,
    )
    assert plan_to_task_subjects(record) == ["Setup", "Implement"]


@pytest.mark.asyncio
async def test_plan_tools_e2e_offline():
    from trpc_agent_sdk.agents._base_agent import BaseAgent
    from trpc_agent_sdk.context import InvocationContext, create_agent_context
    from trpc_agent_sdk.plan_mode import UpdatePlanContentTool, decode_plan, encode_plan, state_key
    from trpc_agent_sdk.plan_mode._helpers import DEFAULT_WRITE_TOOL_NAMES
    from trpc_agent_sdk.plan_mode._controller import _PlanCallbacks
    from trpc_agent_sdk.plan_mode._long_running_tools import make_exit_plan_mode_tool
    from trpc_agent_sdk.plan_mode._long_running_tools import process_hitl_function_response
    from trpc_agent_sdk.tools._base_tool import BaseTool

    class _Stub(BaseAgent):

        async def _run_async_impl(self, ctx):
            yield

    svc = InMemorySessionService()
    session = await svc.create_session(app_name="t", user_id="u", session_id="s1")
    agent = _Stub(name="orch")
    ctx = InvocationContext(
        session_service=svc,
        invocation_id="inv",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
        branch="",
    )

    await _enter_plan_mode_confirmed(ctx, objective="migrate auth")
    await UpdatePlanContentTool()._run_async_impl(
        tool_context=ctx,
        args={
            "content": "## Step 1\nDo work",
            "mode": "replace"
        },
    )
    exit_tool = make_exit_plan_mode_tool()
    pending = await exit_tool._run_async_impl(tool_context=ctx, args={"summary": "review"})
    assert pending["status"] == "pending_approval"

    process_hitl_function_response(
        ctx,
        name="exit_plan_mode",
        response={
            "status": "approved",
            "reviewer_note": "ok"
        },
    )
    plan = decode_plan(ctx.state.get(state_key("plan", agent.name)))
    assert plan is not None
    assert plan.status == PlanStatus.APPROVED

    callbacks = _PlanCallbacks(
        state_key_prefix="plan",
        plan_prompt="",
        awareness_prompt="",
        write_tool_names=DEFAULT_WRITE_TOOL_NAMES,
        inject_prompt=False,
        inject_awareness=False,
        on_approval=None,
    )

    class _WriteTool(BaseTool):

        def __init__(self):
            super().__init__(name="Write", description="write")

        def _get_declaration(self):
            return None

        async def _run_async_impl(self, *, tool_context, args):
            return {"ok": True}

    # Re-open gate for write-block test
    plan.status = PlanStatus.DRAFTING
    ctx.state[state_key("plan", agent.name)] = encode_plan(plan)
    blocked = await callbacks.before_tool(ctx, _WriteTool(), {}, {})
    assert blocked is not None and "PLAN_MODE_GATE" in blocked["error"]

    plan.status = PlanStatus.APPROVED
    ctx.state[state_key("plan", agent.name)] = encode_plan(plan)
    allowed = await callbacks.before_tool(ctx, _WriteTool(), {}, {})
    assert allowed is None


@pytest.mark.asyncio
async def test_edit_tool_is_blocked_by_default_denylist():
    """Regression test: the real file-editing tool is named ``Edit`` (not
    ``edit_file``) — the denylist must match the actual registered name or
    the plan gate silently lets file edits through."""
    from trpc_agent_sdk.agents._base_agent import BaseAgent
    from trpc_agent_sdk.context import InvocationContext, create_agent_context
    from trpc_agent_sdk.plan_mode._controller import _PlanCallbacks
    from trpc_agent_sdk.plan_mode._helpers import DEFAULT_WRITE_TOOL_NAMES
    from trpc_agent_sdk.tools._base_tool import BaseTool

    class _Stub(BaseAgent):

        async def _run_async_impl(self, ctx):
            yield

    svc = InMemorySessionService()
    session = await svc.create_session(app_name="t", user_id="u", session_id="s_edit")
    agent = _Stub(name="orch")
    ctx = InvocationContext(
        session_service=svc,
        invocation_id="inv",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
        branch="",
    )
    await _enter_plan_mode_confirmed(ctx)

    class _EditTool(BaseTool):

        def __init__(self):
            super().__init__(name="Edit", description="edit")

        def _get_declaration(self):
            return None

        async def _run_async_impl(self, *, tool_context, args):
            return {"ok": True}

    callbacks = _PlanCallbacks(
        state_key_prefix="plan",
        plan_prompt="",
        awareness_prompt="",
        write_tool_names=DEFAULT_WRITE_TOOL_NAMES,
        inject_prompt=False,
        inject_awareness=False,
        on_approval=None,
    )
    blocked = await callbacks.before_tool(ctx, _EditTool(), {}, {})
    assert blocked is not None and "PLAN_MODE_GATE" in blocked["error"]


@pytest.mark.asyncio
async def test_spawn_subagent_restricted_to_readonly_archetypes():
    """spawn_subagent must only bypass the gate for read-only archetypes;
    other archetypes (e.g. "default") inherit the parent's full — possibly
    write-capable — tool surface and must stay gated."""
    from trpc_agent_sdk.agents._base_agent import BaseAgent
    from trpc_agent_sdk.context import InvocationContext, create_agent_context
    from trpc_agent_sdk.plan_mode._controller import _PlanCallbacks
    from trpc_agent_sdk.plan_mode._helpers import DEFAULT_WRITE_TOOL_NAMES
    from trpc_agent_sdk.tools._base_tool import BaseTool

    class _Stub(BaseAgent):

        async def _run_async_impl(self, ctx):
            yield

    svc = InMemorySessionService()
    session = await svc.create_session(app_name="t", user_id="u", session_id="s_spawn")
    agent = _Stub(name="orch")
    ctx = InvocationContext(
        session_service=svc,
        invocation_id="inv",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
        branch="",
    )
    await _enter_plan_mode_confirmed(ctx)

    class _SpawnTool(BaseTool):

        def __init__(self):
            super().__init__(name="spawn_subagent", description="spawn")

        def _get_declaration(self):
            return None

        async def _run_async_impl(self, *, tool_context, args):
            return {"ok": True}

    callbacks = _PlanCallbacks(
        state_key_prefix="plan",
        plan_prompt="",
        awareness_prompt="",
        write_tool_names=DEFAULT_WRITE_TOOL_NAMES,
        inject_prompt=False,
        inject_awareness=False,
        on_approval=None,
    )
    tool = _SpawnTool()

    allowed = await callbacks.before_tool(ctx, tool, {"subagent_type": "Explore"}, {})
    assert allowed is None

    blocked = await callbacks.before_tool(ctx, tool, {"subagent_type": "default"}, {})
    assert blocked is not None and "PLAN_MODE_GATE" in blocked["error"]

    blocked_missing = await callbacks.before_tool(ctx, tool, {}, {})
    assert blocked_missing is not None and "PLAN_MODE_GATE" in blocked_missing["error"]


@pytest.mark.asyncio
async def test_dynamic_subagent_requires_explicit_readonly_tools():
    """dynamic_subagent must only bypass the gate when it explicitly narrows
    itself to read-only tools; without that restriction it can inherit the
    parent's full tool surface and must stay gated."""
    from trpc_agent_sdk.agents._base_agent import BaseAgent
    from trpc_agent_sdk.context import InvocationContext, create_agent_context
    from trpc_agent_sdk.plan_mode._controller import _PlanCallbacks
    from trpc_agent_sdk.plan_mode._helpers import DEFAULT_WRITE_TOOL_NAMES
    from trpc_agent_sdk.tools._base_tool import BaseTool

    class _Stub(BaseAgent):

        async def _run_async_impl(self, ctx):
            yield

    svc = InMemorySessionService()
    session = await svc.create_session(app_name="t", user_id="u", session_id="s_dyn")
    agent = _Stub(name="orch")
    ctx = InvocationContext(
        session_service=svc,
        invocation_id="inv",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
        branch="",
    )
    await _enter_plan_mode_confirmed(ctx)

    class _DynamicTool(BaseTool):

        def __init__(self):
            super().__init__(name="dynamic_subagent", description="dynamic")

        def _get_declaration(self):
            return None

        async def _run_async_impl(self, *, tool_context, args):
            return {"ok": True}

    callbacks = _PlanCallbacks(
        state_key_prefix="plan",
        plan_prompt="",
        awareness_prompt="",
        write_tool_names=DEFAULT_WRITE_TOOL_NAMES,
        inject_prompt=False,
        inject_awareness=False,
        on_approval=None,
    )
    tool = _DynamicTool()

    allowed = await callbacks.before_tool(ctx, tool, {"tools": ["Read", "Grep"]}, {})
    assert allowed is None

    blocked_write = await callbacks.before_tool(ctx, tool, {"tools": ["Read", "Write"]}, {})
    assert blocked_write is not None and "PLAN_MODE_GATE" in blocked_write["error"]

    blocked_unrestricted = await callbacks.before_tool(ctx, tool, {}, {})
    assert blocked_unrestricted is not None and "PLAN_MODE_GATE" in blocked_unrestricted["error"]


@pytest.mark.asyncio
async def test_hitl_response_replaced_with_standardized_payload():
    """The model must see the state machine's standardized resume payload,
    not the raw (possibly minimal) decision the host application sent."""
    from trpc_agent_sdk.agents._base_agent import BaseAgent
    from trpc_agent_sdk.context import InvocationContext, create_agent_context
    from trpc_agent_sdk.models import LlmRequest
    from trpc_agent_sdk.plan_mode import UpdatePlanContentTool
    from trpc_agent_sdk.plan_mode._controller import _PlanCallbacks
    from trpc_agent_sdk.plan_mode._helpers import DEFAULT_WRITE_TOOL_NAMES
    from trpc_agent_sdk.plan_mode._long_running_tools import make_exit_plan_mode_tool
    from trpc_agent_sdk.types import Content, FunctionResponse, Part

    class _Stub(BaseAgent):

        async def _run_async_impl(self, ctx):
            yield

    svc = InMemorySessionService()
    session = await svc.create_session(app_name="t", user_id="u", session_id="s_hitl")
    agent = _Stub(name="orch")
    ctx = InvocationContext(
        session_service=svc,
        invocation_id="inv",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
        branch="",
    )
    await _enter_plan_mode_confirmed(ctx)
    await UpdatePlanContentTool()._run_async_impl(
        tool_context=ctx,
        args={
            "content": "## Step 1",
            "mode": "replace"
        },
    )
    exit_tool = make_exit_plan_mode_tool()
    await exit_tool._run_async_impl(tool_context=ctx, args={"summary": "review"})

    # Host resumes with a minimal, non-standard payload.
    fr = FunctionResponse(name="exit_plan_mode", response={"status": "approved"})
    request = LlmRequest(contents=[Content(role="user", parts=[Part(function_response=fr)])])

    callbacks = _PlanCallbacks(
        state_key_prefix="plan",
        plan_prompt="",
        awareness_prompt="",
        write_tool_names=DEFAULT_WRITE_TOOL_NAMES,
        inject_prompt=False,
        inject_awareness=False,
        on_approval=None,
    )
    await callbacks.before_model(ctx, request)

    resumed_fr = request.contents[0].parts[0].function_response
    assert resumed_fr.response["status"] == "approved"
    assert "message" in resumed_fr.response
    assert "plan" in resumed_fr.response


@pytest.mark.asyncio
async def test_lock_registry_released_on_approval():
    """The per-session lock registry must not grow without bound: approving
    a plan should drop its entry from ``_locks``."""
    from trpc_agent_sdk.agents._base_agent import BaseAgent
    from trpc_agent_sdk.context import InvocationContext, create_agent_context
    from trpc_agent_sdk.plan_mode import UpdatePlanContentTool
    from trpc_agent_sdk.plan_mode._lock import _locks, store_lock_key
    from trpc_agent_sdk.plan_mode._long_running_tools import (make_exit_plan_mode_tool, process_hitl_function_response)

    class _Stub(BaseAgent):

        async def _run_async_impl(self, ctx):
            yield

    svc = InMemorySessionService()
    session = await svc.create_session(app_name="t", user_id="u", session_id="s_lock_approve")
    agent = _Stub(name="orch")
    ctx = InvocationContext(
        session_service=svc,
        invocation_id="inv",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
        branch="",
    )
    await _enter_plan_mode_confirmed(ctx)
    await UpdatePlanContentTool()._run_async_impl(
        tool_context=ctx,
        args={
            "content": "## Step 1",
            "mode": "replace"
        },
    )
    exit_tool = make_exit_plan_mode_tool()
    await exit_tool._run_async_impl(tool_context=ctx, args={"summary": "review"})
    key = store_lock_key(ctx, prefix="plan", branch="orch")
    assert key in _locks
    process_hitl_function_response(ctx, name="exit_plan_mode", response={"status": "approved"})
    assert key not in _locks


@pytest.mark.asyncio
async def test_before_model_injects_awareness_without_active_plan():
    from trpc_agent_sdk.agents._base_agent import BaseAgent
    from trpc_agent_sdk.context import InvocationContext, create_agent_context
    from trpc_agent_sdk.models import LlmRequest
    from trpc_agent_sdk.plan_mode._controller import _PlanCallbacks
    from trpc_agent_sdk.plan_mode._helpers import DEFAULT_WRITE_TOOL_NAMES
    from trpc_agent_sdk.plan_mode._prompt import DEFAULT_PLAN_AWARENESS_PROMPT
    from trpc_agent_sdk.plan_mode._prompt import _PLAN_AWARENESS_MARKER

    class _Stub(BaseAgent):

        async def _run_async_impl(self, ctx):
            yield

    svc = InMemorySessionService()
    session = await svc.create_session(app_name="t", user_id="u", session_id="s_aware")
    agent = _Stub(name="orch")
    ctx = InvocationContext(
        session_service=svc,
        invocation_id="inv",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
        branch="",
    )
    request = LlmRequest()
    callbacks = _PlanCallbacks(
        state_key_prefix="plan",
        plan_prompt="ACTIVE PLAN PROMPT",
        awareness_prompt=DEFAULT_PLAN_AWARENESS_PROMPT,
        write_tool_names=DEFAULT_WRITE_TOOL_NAMES,
        inject_prompt=True,
        inject_awareness=True,
        on_approval=None,
    )

    await callbacks.before_model(ctx, request)

    instructions = str(request.config.system_instruction)
    assert _PLAN_AWARENESS_MARKER in instructions
    assert "ACTIVE PLAN PROMPT" not in instructions


@pytest.mark.asyncio
async def test_before_model_injects_active_plan_prompt_when_gate_active():
    from trpc_agent_sdk.agents._base_agent import BaseAgent
    from trpc_agent_sdk.context import InvocationContext, create_agent_context
    from trpc_agent_sdk.models import LlmRequest
    from trpc_agent_sdk.plan_mode._controller import _PlanCallbacks
    from trpc_agent_sdk.plan_mode._helpers import DEFAULT_WRITE_TOOL_NAMES
    from trpc_agent_sdk.plan_mode._prompt import DEFAULT_PLAN_AWARENESS_PROMPT

    class _Stub(BaseAgent):

        async def _run_async_impl(self, ctx):
            yield

    svc = InMemorySessionService()
    session = await svc.create_session(app_name="t", user_id="u", session_id="s_active")
    agent = _Stub(name="orch")
    ctx = InvocationContext(
        session_service=svc,
        invocation_id="inv",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
        branch="",
    )
    await _enter_plan_mode_confirmed(ctx, objective="build feature")

    request = LlmRequest()
    callbacks = _PlanCallbacks(
        state_key_prefix="plan",
        plan_prompt="ACTIVE PLAN PROMPT",
        awareness_prompt=DEFAULT_PLAN_AWARENESS_PROMPT,
        write_tool_names=DEFAULT_WRITE_TOOL_NAMES,
        inject_prompt=True,
        inject_awareness=True,
        on_approval=None,
    )

    await callbacks.before_model(ctx, request)

    instructions = str(request.config.system_instruction)
    assert "ACTIVE PLAN PROMPT" in instructions
    assert DEFAULT_PLAN_AWARENESS_PROMPT not in instructions


@pytest.mark.asyncio
async def test_ask_user_question_hitl_applies_answer_and_is_idempotent():
    from trpc_agent_sdk.agents._base_agent import BaseAgent
    from trpc_agent_sdk.context import InvocationContext, create_agent_context
    from trpc_agent_sdk.plan_mode import decode_plan, state_key
    from trpc_agent_sdk.plan_mode._long_running_tools import (
        make_ask_user_question_tool,
        process_hitl_function_response,
    )

    class _Stub(BaseAgent):

        async def _run_async_impl(self, ctx):
            yield

    svc = InMemorySessionService()
    session = await svc.create_session(app_name="t", user_id="u", session_id="s_ask")
    agent = _Stub(name="orch")
    ctx = InvocationContext(
        session_service=svc,
        invocation_id="inv",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
        branch="",
    )
    await _enter_plan_mode_confirmed(ctx, objective="build app")
    ask_tool = make_ask_user_question_tool()
    pending = await ask_tool._run_async_impl(
        tool_context=ctx,
        args={"question": "Which framework?", "options": ["React", "Vue"]},
    )
    assert pending["status"] == "pending_question"
    assert pending["question_id"] == 1

    result = process_hitl_function_response(
        ctx,
        name="ask_user_question",
        response={"answer": "React", "question_id": 1},
    )
    assert result is not None
    assert result["status"] == "answered"

    plan = decode_plan(ctx.state.get(state_key("plan", "orch")))
    assert plan is not None
    assert plan.asked_questions[0].answer == "React"

    # Re-processing the standardized payload must not corrupt state.
    assert process_hitl_function_response(
        ctx,
        name="ask_user_question",
        response=result,
    ) is None

    # pending_question payloads from the initial LRO pause are ignored on resume.
    assert process_hitl_function_response(
        ctx,
        name="ask_user_question",
        response=pending,
    ) is None


@pytest.mark.asyncio
async def test_reject_revise_reapprove_does_not_replay_old_rejection():
    """Regression: replaying an older exit_plan_mode rejection from conversation
    history must not roll a newer pending submission back to drafting."""
    from trpc_agent_sdk.agents._base_agent import BaseAgent
    from trpc_agent_sdk.context import InvocationContext, create_agent_context
    from trpc_agent_sdk.models import LlmRequest
    from trpc_agent_sdk.plan_mode import UpdatePlanContentTool, decode_plan, state_key
    from trpc_agent_sdk.plan_mode._controller import _PlanCallbacks
    from trpc_agent_sdk.plan_mode._helpers import DEFAULT_WRITE_TOOL_NAMES
    from trpc_agent_sdk.plan_mode._long_running_tools import make_exit_plan_mode_tool
    from trpc_agent_sdk.tools._base_tool import BaseTool
    from trpc_agent_sdk.types import Content, FunctionResponse, Part

    class _Stub(BaseAgent):

        async def _run_async_impl(self, ctx):
            yield

    class _WriteTool(BaseTool):

        def __init__(self):
            super().__init__(name="Write", description="write")

        def _get_declaration(self):
            return None

        async def _run_async_impl(self, *, tool_context, args):
            return {"ok": True}

    svc = InMemorySessionService()
    session = await svc.create_session(app_name="t", user_id="u", session_id="s_reapprove")
    agent = _Stub(name="orch")
    ctx = InvocationContext(
        session_service=svc,
        invocation_id="inv",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
        branch="",
    )
    await _enter_plan_mode_confirmed(ctx, objective="build player")
    await UpdatePlanContentTool()._run_async_impl(
        tool_context=ctx,
        args={"content": "## v1", "mode": "replace"},
    )
    exit_tool = make_exit_plan_mode_tool()
    await exit_tool._run_async_impl(tool_context=ctx, args={"summary": "review v1"})

    callbacks = _PlanCallbacks(
        state_key_prefix="plan",
        plan_prompt="",
        awareness_prompt="",
        write_tool_names=DEFAULT_WRITE_TOOL_NAMES,
        inject_prompt=False,
        inject_awareness=False,
        on_approval=None,
    )

    reject_request = LlmRequest(contents=[
        Content(
            role="user",
            parts=[Part(function_response=FunctionResponse(
                name="exit_plan_mode",
                response={"status": "rejected", "reviewer_note": "need more pages"},
            ))],
        ),
    ])
    await callbacks.before_model(ctx, reject_request)
    plan = decode_plan(ctx.state.get(state_key("plan", agent.name)))
    assert plan is not None
    assert plan.status == PlanStatus.DRAFTING

    await UpdatePlanContentTool()._run_async_impl(
        tool_context=ctx,
        args={"content": "## v2 full", "mode": "replace"},
    )
    await exit_tool._run_async_impl(tool_context=ctx, args={"summary": "review v2"})
    plan = decode_plan(ctx.state.get(state_key("plan", agent.name)))
    assert plan is not None
    assert plan.status == PlanStatus.PENDING_APPROVAL

    approve_request = LlmRequest(contents=[
        Content(
            role="user",
            parts=[Part(function_response=FunctionResponse(
                name="exit_plan_mode",
                response={"status": "rejected", "reviewer_note": "need more pages"},
            ))],
        ),
        Content(
            role="user",
            parts=[Part(function_response=FunctionResponse(
                name="exit_plan_mode",
                response={"status": "approved", "reviewer_note": "looks good"},
            ))],
        ),
    ])
    await callbacks.before_model(ctx, approve_request)

    plan = decode_plan(ctx.state.get(state_key("plan", agent.name)))
    assert plan is not None
    assert plan.status == PlanStatus.APPROVED

    blocked = await callbacks.before_tool(ctx, _WriteTool(), {}, {})
    assert blocked is None


@pytest.mark.asyncio
async def test_force_enter_plan_via_session_state_signal():
    from trpc_agent_sdk.agents._base_agent import BaseAgent
    from trpc_agent_sdk.context import InvocationContext, create_agent_context
    from trpc_agent_sdk.models import LlmRequest
    from trpc_agent_sdk.plan_mode import decode_plan, state_key
    from trpc_agent_sdk.plan_mode._controller import _PlanCallbacks
    from trpc_agent_sdk.plan_mode._helpers import DEFAULT_FORCE_ENTER_PLAN_STATE_KEY
    from trpc_agent_sdk.plan_mode._helpers import DEFAULT_FORCE_ENTER_PLAN_STATE_VALUE
    from trpc_agent_sdk.plan_mode._helpers import DEFAULT_WRITE_TOOL_NAMES
    from trpc_agent_sdk.tools._base_tool import BaseTool
    from trpc_agent_sdk.types import Content, Part

    class _Stub(BaseAgent):

        async def _run_async_impl(self, ctx):
            yield

    class _WriteTool(BaseTool):

        def __init__(self):
            super().__init__(name="Write", description="write")

        def _get_declaration(self):
            return None

        async def _run_async_impl(self, *, tool_context, args):
            return {"ok": True}

    svc = InMemorySessionService()
    session = await svc.create_session(app_name="t", user_id="u", session_id="s_ui_plan")
    session.state[DEFAULT_FORCE_ENTER_PLAN_STATE_KEY] = DEFAULT_FORCE_ENTER_PLAN_STATE_VALUE
    agent = _Stub(name="orch")
    ctx = InvocationContext(
        session_service=svc,
        invocation_id="inv",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
        branch="",
        user_content=Content(role="user", parts=[Part(text="Build a dashboard")]),
    )
    callbacks = _PlanCallbacks(
        state_key_prefix="plan",
        plan_prompt="ACTIVE PLAN PROMPT",
        awareness_prompt="awareness",
        write_tool_names=DEFAULT_WRITE_TOOL_NAMES,
        inject_prompt=True,
        inject_awareness=True,
        on_approval=None,
    )

    request = LlmRequest()
    await callbacks.before_model(ctx, request)

    plan = decode_plan(ctx.state.get(state_key("plan", agent.name)))
    assert plan is not None
    assert plan.status == PlanStatus.EXPLORING
    assert plan.objective == "Build a dashboard"
    assert "ACTIVE PLAN PROMPT" in str(request.config.system_instruction)

    blocked = await callbacks.before_tool(ctx, _WriteTool(), {}, {})
    assert blocked is not None and "PLAN_MODE_GATE" in blocked["error"]


@pytest.mark.asyncio
async def test_force_enter_blocks_enter_plan_mode_tool():
    from trpc_agent_sdk.agents._base_agent import BaseAgent
    from trpc_agent_sdk.context import InvocationContext, create_agent_context
    from trpc_agent_sdk.models import LlmRequest
    from trpc_agent_sdk.plan_mode._controller import _PlanCallbacks
    from trpc_agent_sdk.plan_mode._helpers import DEFAULT_FORCE_ENTER_PLAN_STATE_KEY
    from trpc_agent_sdk.plan_mode._helpers import DEFAULT_FORCE_ENTER_PLAN_STATE_VALUE
    from trpc_agent_sdk.plan_mode._helpers import DEFAULT_WRITE_TOOL_NAMES
    from trpc_agent_sdk.plan_mode._long_running_tools import make_enter_plan_mode_tool
    from trpc_agent_sdk.types import Content, Part

    class _Stub(BaseAgent):

        async def _run_async_impl(self, ctx):
            yield

    svc = InMemorySessionService()
    session = await svc.create_session(app_name="t", user_id="u", session_id="s_block_enter")
    session.state[DEFAULT_FORCE_ENTER_PLAN_STATE_KEY] = DEFAULT_FORCE_ENTER_PLAN_STATE_VALUE
    agent = _Stub(name="orch")
    ctx = InvocationContext(
        session_service=svc,
        invocation_id="inv",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
        branch="",
        user_content=Content(role="user", parts=[Part(text="Build a player")]),
    )
    callbacks = _PlanCallbacks(
        state_key_prefix="plan",
        plan_prompt="ACTIVE PLAN PROMPT",
        awareness_prompt="awareness",
        write_tool_names=DEFAULT_WRITE_TOOL_NAMES,
        inject_prompt=True,
        inject_awareness=True,
        on_approval=None,
    )
    await callbacks.before_model(ctx, LlmRequest())

    enter_tool = make_enter_plan_mode_tool()
    blocked = await callbacks.before_tool(ctx, enter_tool, {"objective": "x"}, {})
    assert blocked is not None
    assert "enter_plan_mode" in blocked["error"]
    assert "UI" in blocked["error"] or "automatic" in blocked["error"]


@pytest.mark.asyncio
async def test_plan_toolset_hides_enter_plan_mode_when_ui_plan_selected():
    from trpc_agent_sdk.agents._base_agent import BaseAgent
    from trpc_agent_sdk.context import InvocationContext, create_agent_context
    from trpc_agent_sdk.plan_mode._helpers import DEFAULT_FORCE_ENTER_PLAN_STATE_KEY
    from trpc_agent_sdk.plan_mode._helpers import DEFAULT_FORCE_ENTER_PLAN_STATE_VALUE
    from trpc_agent_sdk.plan_mode._plan_toolset import PlanToolSet

    class _Stub(BaseAgent):

        async def _run_async_impl(self, ctx):
            yield

    svc = InMemorySessionService()
    session = await svc.create_session(app_name="t", user_id="u", session_id="s_hide_enter")
    session.state[DEFAULT_FORCE_ENTER_PLAN_STATE_KEY] = DEFAULT_FORCE_ENTER_PLAN_STATE_VALUE
    agent = _Stub(name="orch")
    ctx = InvocationContext(
        session_service=svc,
        invocation_id="inv",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
        branch="",
    )

    tool_names = [tool.name for tool in await PlanToolSet().get_tools(ctx)]
    assert "enter_plan_mode" not in tool_names
    assert "update_plan_content" in tool_names
    assert "exit_plan_mode" in tool_names
    assert "ask_user_question" in tool_names


@pytest.mark.asyncio
async def test_plan_toolset_keeps_enter_plan_mode_in_agent_mode():
    from trpc_agent_sdk.agents._base_agent import BaseAgent
    from trpc_agent_sdk.context import InvocationContext, create_agent_context
    from trpc_agent_sdk.plan_mode._plan_toolset import PlanToolSet

    class _Stub(BaseAgent):

        async def _run_async_impl(self, ctx):
            yield

    svc = InMemorySessionService()
    session = await svc.create_session(app_name="t", user_id="u", session_id="s_show_enter")
    agent = _Stub(name="orch")
    ctx = InvocationContext(
        session_service=svc,
        invocation_id="inv",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
        branch="",
    )

    tool_names = [tool.name for tool in await PlanToolSet().get_tools(ctx)]
    assert "enter_plan_mode" in tool_names

