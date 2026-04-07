"""Unit tests for trpc_agent_sdk.server.openclaw.tools.cron module."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.server.openclaw.tools.cron import (
    CRON_CHANNEL_KEY,
    CRON_CHAT_ID_KEY,
    CRON_IN_CONTEXT_KEY,
    CronTool,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_context(channel="telegram", chat_id="123", in_cron=False):
    ctx = MagicMock(spec=InvocationContext)
    agent_ctx = MagicMock()

    def _get_metadata(key, default=None):
        mapping = {
            CRON_CHANNEL_KEY: channel,
            CRON_CHAT_ID_KEY: chat_id,
            CRON_IN_CONTEXT_KEY: in_cron,
        }
        return mapping.get(key, default)

    agent_ctx.get_metadata = MagicMock(side_effect=_get_metadata)
    ctx.agent_context = agent_ctx
    return ctx


def _mock_cron_service():
    svc = MagicMock()
    job = MagicMock()
    job.name = "test-job"
    job.id = "job-123"
    svc.add_job.return_value = job
    svc.list_jobs.return_value = []
    svc.remove_job.return_value = True
    return svc


# ---------------------------------------------------------------------------
# CronTool._run_async_impl — action dispatch
# ---------------------------------------------------------------------------


class TestRunAsyncImplDispatch:

    async def test_add_action(self):
        svc = _mock_cron_service()
        tool = CronTool(cron_service=svc)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"action": "add", "message": "reminder", "every_seconds": 60},
        )
        assert "Created job" in result
        svc.add_job.assert_called_once()

    async def test_list_action(self):
        svc = _mock_cron_service()
        tool = CronTool(cron_service=svc)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"action": "list"},
        )
        assert "No scheduled jobs" in result

    async def test_remove_action(self):
        svc = _mock_cron_service()
        tool = CronTool(cron_service=svc)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"action": "remove", "job_id": "job-123"},
        )
        assert "Removed" in result

    async def test_unknown_action(self):
        svc = _mock_cron_service()
        tool = CronTool(cron_service=svc)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"action": "invalid"},
        )
        assert "Unknown action" in result

    async def test_in_cron_guard(self):
        svc = _mock_cron_service()
        tool = CronTool(cron_service=svc)
        ctx = _tool_context(in_cron=True)
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"action": "add", "message": "test", "every_seconds": 60},
        )
        assert "cannot schedule" in result

    async def test_no_agent_context(self):
        svc = _mock_cron_service()
        tool = CronTool(cron_service=svc)
        ctx = MagicMock(spec=InvocationContext)
        ctx.agent_context = None
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"action": "list"},
        )
        assert "No scheduled jobs" in result


# ---------------------------------------------------------------------------
# CronTool._add_job
# ---------------------------------------------------------------------------


class TestAddJob:

    async def test_missing_message(self):
        svc = _mock_cron_service()
        tool = CronTool(cron_service=svc)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"action": "add", "message": "", "every_seconds": 60},
        )
        assert "message is required" in result

    async def test_missing_channel(self):
        svc = _mock_cron_service()
        tool = CronTool(cron_service=svc)
        ctx = _tool_context(channel="", chat_id="123")
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"action": "add", "message": "test", "every_seconds": 60},
        )
        assert "no delivery context" in result

    async def test_missing_chat_id(self):
        svc = _mock_cron_service()
        tool = CronTool(cron_service=svc)
        ctx = _tool_context(channel="telegram", chat_id="")
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"action": "add", "message": "test", "every_seconds": 60},
        )
        assert "no delivery context" in result

    async def test_tz_without_cron_expr(self):
        svc = _mock_cron_service()
        tool = CronTool(cron_service=svc)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"action": "add", "message": "test", "every_seconds": 60, "tz": "US/Pacific"},
        )
        assert "tz can only be used together with cron_expr" in result

    async def test_invalid_tz(self):
        svc = _mock_cron_service()
        tool = CronTool(cron_service=svc)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"action": "add", "message": "test", "cron_expr": "0 9 * * *", "tz": "Invalid/Zone"},
        )
        assert "unknown timezone" in result

    async def test_valid_tz_with_cron_expr(self):
        svc = _mock_cron_service()
        tool = CronTool(cron_service=svc)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"action": "add", "message": "test", "cron_expr": "0 9 * * *", "tz": "America/Vancouver"},
        )
        assert "Created job" in result

    async def test_every_seconds(self):
        svc = _mock_cron_service()
        tool = CronTool(cron_service=svc)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"action": "add", "message": "test", "every_seconds": 120},
        )
        assert "Created job" in result
        call_kwargs = svc.add_job.call_args
        schedule = call_kwargs.kwargs.get("schedule") or call_kwargs[1].get("schedule")
        assert schedule.kind == "every"
        assert schedule.every_ms == 120_000

    async def test_cron_expr(self):
        svc = _mock_cron_service()
        tool = CronTool(cron_service=svc)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"action": "add", "message": "test", "cron_expr": "0 9 * * *"},
        )
        assert "Created job" in result
        call_kwargs = svc.add_job.call_args
        schedule = call_kwargs.kwargs.get("schedule") or call_kwargs[1].get("schedule")
        assert schedule.kind == "cron"

    async def test_at_valid_iso(self):
        svc = _mock_cron_service()
        tool = CronTool(cron_service=svc)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"action": "add", "message": "test", "at": "2026-02-12T10:30:00"},
        )
        assert "Created job" in result
        call_kwargs = svc.add_job.call_args
        assert call_kwargs.kwargs.get("delete_after_run") or call_kwargs[1].get("delete_after_run")

    async def test_at_invalid_iso(self):
        svc = _mock_cron_service()
        tool = CronTool(cron_service=svc)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"action": "add", "message": "test", "at": "not-a-date"},
        )
        assert "invalid ISO datetime" in result

    async def test_missing_schedule(self):
        svc = _mock_cron_service()
        tool = CronTool(cron_service=svc)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"action": "add", "message": "test"},
        )
        assert "one of every_seconds, cron_expr, or at is required" in result


# ---------------------------------------------------------------------------
# CronTool._list_jobs
# ---------------------------------------------------------------------------


class TestListJobs:

    async def test_no_jobs(self):
        svc = _mock_cron_service()
        svc.list_jobs.return_value = []
        tool = CronTool(cron_service=svc)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"action": "list"},
        )
        assert "No scheduled jobs" in result

    async def test_with_jobs(self):
        svc = _mock_cron_service()
        job1 = MagicMock()
        job1.name = "job-a"
        job1.id = "id-a"
        job1.schedule.kind = "every"
        job2 = MagicMock()
        job2.name = "job-b"
        job2.id = "id-b"
        job2.schedule.kind = "cron"
        svc.list_jobs.return_value = [job1, job2]
        tool = CronTool(cron_service=svc)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"action": "list"},
        )
        assert "Scheduled jobs:" in result
        assert "job-a" in result
        assert "job-b" in result
        assert "id-a" in result
        assert "every" in result
        assert "cron" in result


# ---------------------------------------------------------------------------
# CronTool._remove_job
# ---------------------------------------------------------------------------


class TestRemoveJob:

    async def test_missing_job_id(self):
        svc = _mock_cron_service()
        tool = CronTool(cron_service=svc)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"action": "remove"},
        )
        assert "job_id is required" in result

    async def test_job_found(self):
        svc = _mock_cron_service()
        svc.remove_job.return_value = True
        tool = CronTool(cron_service=svc)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"action": "remove", "job_id": "job-123"},
        )
        assert "Removed job job-123" in result

    async def test_job_not_found(self):
        svc = _mock_cron_service()
        svc.remove_job.return_value = False
        tool = CronTool(cron_service=svc)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"action": "remove", "job_id": "nonexistent"},
        )
        assert "not found" in result


# ---------------------------------------------------------------------------
# CronTool._get_declaration
# ---------------------------------------------------------------------------


class TestCronDeclaration:

    def test_declaration_name(self):
        svc = _mock_cron_service()
        tool = CronTool(cron_service=svc)
        decl = tool._get_declaration()
        assert decl.name == "cron"

    def test_declaration_required_action(self):
        svc = _mock_cron_service()
        tool = CronTool(cron_service=svc)
        decl = tool._get_declaration()
        assert "action" in decl.parameters.required
