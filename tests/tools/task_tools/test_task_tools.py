# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the Task tool family (:mod:`trpc_agent_sdk.tools.task_tools`)."""

from __future__ import annotations

import asyncio

import pytest

from trpc_agent_sdk.agents._base_agent import BaseAgent
from trpc_agent_sdk.context import InvocationContext, create_agent_context
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools.task_tools import (
    DEFAULT_STATE_KEY_PREFIX,
    TaskCreateTool,
    TaskGetTool,
    TaskListTool,
    TaskStatus,
    TaskStore,
    TaskToolSet,
    TaskUpdateTool,
    decode_store,
    detect_cycle,
    get_task_store,
    render_task_list,
    state_key,
)
from trpc_agent_sdk.tools.task_tools._models import TaskRecord
from trpc_agent_sdk.types import Content, EventActions, Part


class _StubAgent(BaseAgent):
    async def _run_async_impl(self, ctx):
        yield


@pytest.fixture
def session_bundle():
    service = InMemorySessionService()
    session = asyncio.run(
        service.create_session(app_name="test", user_id="u1", session_id="s1")
    )
    agent = _StubAgent(name="task_planner")
    ctx = InvocationContext(
        session_service=service,
        invocation_id="inv-1",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
        branch="",
    )
    return service, session, agent, ctx


async def _create(ctx, subject, **kwargs):
    tool = TaskCreateTool()
    return await tool._run_async_impl(tool_context=ctx, args={"subject": subject, **kwargs})


async def _update(ctx, task_id, **kwargs):
    tool = TaskUpdateTool()
    return await tool._run_async_impl(tool_context=ctx, args={"taskId": task_id, **kwargs})


class TestTaskCreate:
    @pytest.mark.asyncio
    async def test_assigns_incrementing_id(self, session_bundle):
        _, _, agent, ctx = session_bundle
        first = await _create(ctx, "A")
        second = await _create(ctx, "B")
        assert first["task"]["id"] == "1"
        assert second["task"]["id"] == "2"
        assert state_key(DEFAULT_STATE_KEY_PREFIX, agent.name) in ctx.state._delta

    @pytest.mark.asyncio
    async def test_rejects_empty_subject(self, session_bundle):
        _, _, _, ctx = session_bundle
        result = await _create(ctx, "   ")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_id_not_reused_after_delete(self, session_bundle):
        _, _, _, ctx = session_bundle
        await _create(ctx, "A")
        await _update(ctx, "1", status="deleted")
        third = await _create(ctx, "C")
        assert third["task"]["id"] == "2"


class TestTaskUpdate:
    @pytest.mark.asyncio
    async def test_status_transition(self, session_bundle):
        _, _, _, ctx = session_bundle
        await _create(ctx, "A")
        res = await _update(ctx, "1", status="in_progress")
        assert res["task"]["status"] == "in_progress"
        res = await _update(ctx, "1", status="completed")
        assert res["task"]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_not_found(self, session_bundle):
        _, _, _, ctx = session_bundle
        res = await _update(ctx, "99", status="completed")
        assert "NOT_FOUND" in res["error"]

    @pytest.mark.asyncio
    async def test_single_in_progress_enforced(self, session_bundle):
        _, _, _, ctx = session_bundle
        await _create(ctx, "A")
        await _create(ctx, "B")
        await _update(ctx, "1", status="in_progress")
        res = await _update(ctx, "2", status="in_progress")
        assert "error" in res
        assert "in_progress" in res["error"]

    @pytest.mark.asyncio
    async def test_single_in_progress_can_be_disabled(self, session_bundle):
        _, _, _, ctx = session_bundle
        await _create(ctx, "A")
        await _create(ctx, "B")
        tool = TaskUpdateTool(enforce_single_in_progress=False)
        await tool._run_async_impl(tool_context=ctx, args={"taskId": "1", "status": "in_progress"})
        res = await tool._run_async_impl(tool_context=ctx, args={"taskId": "2", "status": "in_progress"})
        assert "error" not in res

    @pytest.mark.asyncio
    async def test_two_way_dependency_edges(self, session_bundle):
        _, _, _, ctx = session_bundle
        await _create(ctx, "schema")
        await _create(ctx, "endpoints")
        res = await _update(ctx, "2", addBlockedBy=["1"])
        assert res["task"]["blockedBy"] == ["1"]
        store = get_task_store(ctx.session, branch="task_planner")
        assert store.tasks["1"].blocks == ["2"]

    @pytest.mark.asyncio
    async def test_complete_unblocks_downstream(self, session_bundle):
        _, _, _, ctx = session_bundle
        await _create(ctx, "schema")
        await _create(ctx, "endpoints")
        await _update(ctx, "2", addBlockedBy=["1"])
        res = await _update(ctx, "1", status="completed")
        assert res["unblocked"] == ["2"]
        store = get_task_store(ctx.session, branch="task_planner")
        assert store.tasks["2"].blocked_by == []

    @pytest.mark.asyncio
    async def test_cycle_rejected(self, session_bundle):
        _, _, _, ctx = session_bundle
        await _create(ctx, "A")
        await _create(ctx, "B")
        await _update(ctx, "2", addBlockedBy=["1"])
        res = await _update(ctx, "1", addBlockedBy=["2"])
        assert "INVALID_DEPENDENCY" in res["error"]

    @pytest.mark.asyncio
    async def test_missing_dependency_rejected(self, session_bundle):
        _, _, _, ctx = session_bundle
        await _create(ctx, "A")
        res = await _update(ctx, "1", addBlockedBy=["99"])
        assert "INVALID_DEPENDENCY" in res["error"]

    @pytest.mark.asyncio
    async def test_deleted_cannot_be_modified(self, session_bundle):
        _, _, _, ctx = session_bundle
        await _create(ctx, "A")
        await _update(ctx, "1", status="deleted")
        res = await _update(ctx, "1", status="in_progress")
        assert "error" in res


class TestTaskGetAndList:
    @pytest.mark.asyncio
    async def test_get_includes_description(self, session_bundle):
        _, _, _, ctx = session_bundle
        await _create(ctx, "A", description="long detail")
        res = await TaskGetTool()._run_async_impl(tool_context=ctx, args={"taskId": "1"})
        assert res["task"]["description"] == "long detail"

    @pytest.mark.asyncio
    async def test_get_not_found(self, session_bundle):
        _, _, _, ctx = session_bundle
        res = await TaskGetTool()._run_async_impl(tool_context=ctx, args={"taskId": "1"})
        assert "NOT_FOUND" in res["error"]

    @pytest.mark.asyncio
    async def test_list_omits_description_and_filters_deleted(self, session_bundle):
        _, _, _, ctx = session_bundle
        await _create(ctx, "A", description="should not appear")
        await _create(ctx, "B")
        await _update(ctx, "2", status="deleted")
        res = await TaskListTool()._run_async_impl(tool_context=ctx, args={})
        assert len(res["tasks"]) == 1
        assert "description" not in res["tasks"][0]
        assert res["stats"]["pending"] == 1

    @pytest.mark.asyncio
    async def test_list_include_deleted(self, session_bundle):
        _, _, _, ctx = session_bundle
        await _create(ctx, "A")
        await _update(ctx, "1", status="deleted")
        res = await TaskListTool()._run_async_impl(tool_context=ctx, args={"includeDeleted": True})
        assert len(res["tasks"]) == 1


class TestBranchIsolation:
    @pytest.mark.asyncio
    async def test_branches_are_independent(self, session_bundle):
        service, session, agent, _ = session_bundle
        ctx_a = InvocationContext(
            session_service=service, invocation_id="i", agent=agent,
            agent_context=create_agent_context(), session=session, branch="a",
        )
        ctx_b = InvocationContext(
            session_service=service, invocation_id="i", agent=agent,
            agent_context=create_agent_context(), session=session, branch="b",
        )
        await _create(ctx_a, "task in a")
        await _create(ctx_b, "task in b")
        store_a = decode_store(ctx_a.state._delta[state_key(DEFAULT_STATE_KEY_PREFIX, "a")])
        store_b = decode_store(ctx_b.state._delta[state_key(DEFAULT_STATE_KEY_PREFIX, "b")])
        assert store_a.tasks["1"].subject == "task in a"
        assert store_b.tasks["1"].subject == "task in b"


class TestPersistence:
    @pytest.mark.asyncio
    async def test_store_survives_append_event_and_get_session(self, session_bundle):
        service, session, agent, ctx = session_bundle
        await _create(ctx, "A")
        await _update(ctx, "1", status="in_progress")

        event = Event(
            invocation_id="inv-1",
            author=agent.name,
            content=Content(parts=[Part.from_text(text="tool result")]),
            actions=EventActions(state_delta=dict(ctx.event_actions.state_delta)),
        )
        await service.append_event(session, event)

        stored = await service.get_session(app_name="test", user_id="u1", session_id="s1")
        store = get_task_store(stored, branch=agent.name)
        assert store.tasks["1"].status == TaskStatus.IN_PROGRESS


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_parallel_create_assigns_unique_ids(self, session_bundle):
        _, _, _, ctx = session_bundle
        tool = TaskCreateTool()
        results = await asyncio.gather(
            *[
                tool._run_async_impl(tool_context=ctx, args={"subject": f"task-{i}"})
                for i in range(20)
            ]
        )
        ids = sorted(int(r["task"]["id"]) for r in results)
        assert ids == list(range(1, 21))

    @pytest.mark.asyncio
    async def test_parallel_mixed_ops_preserve_all_tasks(self, session_bundle):
        _, _, _, ctx = session_bundle
        create = TaskCreateTool()
        update = TaskUpdateTool()
        await create._run_async_impl(tool_context=ctx, args={"subject": "seed"})
        await asyncio.gather(
            create._run_async_impl(tool_context=ctx, args={"subject": "A"}),
            create._run_async_impl(tool_context=ctx, args={"subject": "B"}),
            update._run_async_impl(tool_context=ctx, args={"taskId": "1", "status": "in_progress"}),
        )
        store = get_task_store(ctx.session, branch="task_planner")
        assert set(store.tasks) == {"1", "2", "3"}
        assert store.tasks["1"].status == TaskStatus.IN_PROGRESS


class TestHelpers:
    def test_state_key(self):
        assert state_key(DEFAULT_STATE_KEY_PREFIX, "") == "tasks"
        assert state_key(DEFAULT_STATE_KEY_PREFIX, "planner") == "tasks:planner"

    def test_decode_dirty_data_degrades_to_empty(self):
        assert decode_store("not json").tasks == {}
        assert decode_store(None).tasks == {}

    def test_detect_cycle_on_clean_store(self):
        store = TaskStore()
        store.tasks["1"] = TaskRecord(id="1", subject="A")
        store.tasks["2"] = TaskRecord(id="2", subject="B", blockedBy=["1"])
        assert detect_cycle(store) is None

    def test_render_task_list(self):
        store = TaskStore()
        store.tasks["1"] = TaskRecord(id="1", subject="Done", status=TaskStatus.COMPLETED)
        store.tasks["2"] = TaskRecord(
            id="2", subject="Active", activeForm="Doing active", status=TaskStatus.IN_PROGRESS
        )
        store.tasks["3"] = TaskRecord(id="3", subject="Wait", blockedBy=["2"])
        rendered = render_task_list(store)
        assert "✅ #1 Done" in rendered
        assert "🔄 #2 Doing active" in rendered
        assert "blocked by: 2" in rendered

class TestProcessRequest:
    @pytest.mark.asyncio
    async def test_injects_prompt_once(self, session_bundle):
        _, _, _, ctx = session_bundle
        llm_request = LlmRequest()
        await TaskCreateTool().process_request(tool_context=ctx, llm_request=llm_request)
        await TaskUpdateTool().process_request(tool_context=ctx, llm_request=llm_request)
        text = str(llm_request.config.system_instruction)
        assert text.count("structured task board via the tools") == 1
        assert "task_create" in llm_request.tools_dict
        assert "task_update" in llm_request.tools_dict


class TestTaskToolSet:
    @pytest.mark.asyncio
    async def test_returns_four_tools(self):
        tools = await TaskToolSet().get_tools()
        names = {t.name for t in tools}
        assert names == {"task_create", "task_update", "task_get", "task_list"}
