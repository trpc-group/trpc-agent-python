# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Cron tool for trpc-claw scheduling reminders and tasks.

Implemented as a :class:`~trpc_agent_sdk.tools.BaseTool` subclass.


"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from typing import List
from typing import Optional

from nanobot.cron.service import CronService
from nanobot.cron.types import CronSchedule
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

# ---------------------------------------------------------------------------
# Metadata keys — import these in callers to avoid magic strings
# ---------------------------------------------------------------------------

CRON_CHANNEL_KEY = "cron_channel"
CRON_CHAT_ID_KEY = "cron_chat_id"
CRON_IN_CONTEXT_KEY = "cron_in_context"

_DESCRIPTION = "Schedule reminders and recurring tasks. Actions: add, list, remove."


class CronTool(BaseTool):
    """trpc_agent_sdk tool to schedule reminders and recurring tasks.

    Supports three actions via the ``action`` parameter:

    * ``add``    — create a new scheduled job (requires ``message`` + one of
                   ``every_seconds`` / ``cron_expr`` / ``at``).
    * ``list``   — list all active jobs.
    * ``remove`` — delete a job by ``job_id``.

    Per-invocation delivery context (channel, chat_id) and the
    in-cron-callback guard are read from ``tool_context.agent_context``
    metadata using :data:`CRON_CHANNEL_KEY`, :data:`CRON_CHAT_ID_KEY`, and
    :data:`CRON_IN_CONTEXT_KEY`.

    Args:
        cron_service:  The :class:`CronService` instance that manages jobs.
        filters_name:  Optional filter names forwarded to
                       :class:`~trpc_agent_sdk.tools.BaseTool`.
        filters:       Optional filter instances forwarded to
                       :class:`~trpc_agent_sdk.tools.BaseTool`.
    """

    def __init__(
        self,
        cron_service: CronService,
        filters_name: Optional[List[str]] = None,
        filters: Optional[List[BaseFilter]] = None,
    ) -> None:
        super().__init__(
            name="cron",
            description=_DESCRIPTION,
            filters_name=filters_name,
            filters=filters,
        )
        self._cron = cron_service

    # ------------------------------------------------------------------
    # BaseTool interface
    # ------------------------------------------------------------------

    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            name="cron",
            description=_DESCRIPTION,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "action":
                    Schema(
                        type=Type.STRING,
                        enum=["add", "list", "remove"],
                        description="Action to perform",
                    ),
                    "message":
                    Schema(
                        type=Type.STRING,
                        description="Reminder message (required for add)",
                    ),
                    "every_seconds":
                    Schema(
                        type=Type.INTEGER,
                        description="Repeat interval in seconds (for recurring tasks)",
                    ),
                    "cron_expr":
                    Schema(
                        type=Type.STRING,
                        description="Cron expression like '0 9 * * *' (for scheduled tasks)",
                    ),
                    "tz":
                    Schema(
                        type=Type.STRING,
                        description="IANA timezone for cron_expr (e.g. 'America/Vancouver')",
                    ),
                    "at":
                    Schema(
                        type=Type.STRING,
                        description="ISO datetime for one-time execution (e.g. '2026-02-12T10:30:00')",
                    ),
                    "job_id":
                    Schema(
                        type=Type.STRING,
                        description="Job ID (required for remove)",
                    ),
                },
                required=["action"],
            ),
        )

    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        agent_ctx = tool_context.agent_context
        channel: str = agent_ctx.get_metadata(CRON_CHANNEL_KEY, "") if agent_ctx else ""
        chat_id: str = agent_ctx.get_metadata(CRON_CHAT_ID_KEY, "") if agent_ctx else ""
        in_cron: bool = agent_ctx.get_metadata(CRON_IN_CONTEXT_KEY, False) if agent_ctx else False

        action = args.get("action", "")

        if action == "add":
            if in_cron:
                return "Error: cannot schedule new jobs from within a cron job execution"
            return self._add_job(
                channel=channel,
                chat_id=chat_id,
                message=args.get("message", ""),
                every_seconds=args.get("every_seconds"),
                cron_expr=args.get("cron_expr"),
                tz=args.get("tz"),
                at=args.get("at"),
            )
        if action == "list":
            return self._list_jobs()
        if action == "remove":
            return self._remove_job(args.get("job_id"))
        return f"Unknown action: {action!r}"

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _add_job(
        self,
        channel: str,
        chat_id: str,
        message: str,
        every_seconds: Optional[int],
        cron_expr: Optional[str],
        tz: Optional[str],
        at: Optional[str],
    ) -> str:
        if not message:
            return "Error: message is required for add"
        if not channel or not chat_id:
            return (f"Error: no delivery context — set {CRON_CHANNEL_KEY!r} and "
                    f"{CRON_CHAT_ID_KEY!r} in agent_context metadata before calling this tool")
        if tz and not cron_expr:
            return "Error: tz can only be used together with cron_expr"
        if tz:
            from zoneinfo import ZoneInfo
            try:
                ZoneInfo(tz)
            except Exception:
                return f"Error: unknown timezone '{tz}'"

        delete_after = False
        if every_seconds:
            schedule = CronSchedule(kind="every", every_ms=every_seconds * 1000)
        elif cron_expr:
            schedule = CronSchedule(kind="cron", expr=cron_expr, tz=tz)
        elif at:
            try:
                dt = datetime.fromisoformat(at)
            except ValueError:
                return f"Error: invalid ISO datetime '{at}'. Expected YYYY-MM-DDTHH:MM:SS"
            schedule = CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))
            delete_after = True
        else:
            return "Error: one of every_seconds, cron_expr, or at is required"

        job = self._cron.add_job(
            name=message[:30],
            schedule=schedule,
            message=message,
            deliver=True,
            channel=channel,
            to=chat_id,
            delete_after_run=delete_after,
        )
        return f"Created job '{job.name}' (id: {job.id})"

    def _list_jobs(self) -> str:
        jobs = self._cron.list_jobs()
        if not jobs:
            return "No scheduled jobs."
        lines = [f"- {j.name} (id: {j.id}, {j.schedule.kind})" for j in jobs]
        return "Scheduled jobs:\n" + "\n".join(lines)

    def _remove_job(self, job_id: Optional[str]) -> str:
        if not job_id:
            return "Error: job_id is required for remove"
        if self._cron.remove_job(job_id):
            return f"Removed job {job_id}"
        return f"Job {job_id} not found"
