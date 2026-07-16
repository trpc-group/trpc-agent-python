# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool-level filter guarding LLM-initiated skill_run calls."""
from trpc_agent_sdk.abc import FilterType
from trpc_agent_sdk.filter import BaseFilter

from review.governance import GovernanceEngine


class GovernanceToolFilter(BaseFilter):
    """Blocks skill_run commands the GovernanceEngine does not allow."""

    def __init__(self, engine: GovernanceEngine, on_event=None):
        super().__init__()
        self._type = FilterType.TOOL
        self._name = "cr_governance"
        self._engine = engine
        self._on_event = on_event

    async def _before(self, ctx, req, rsp):
        command = ""
        if isinstance(req, dict):
            command = str(req.get("command", "") or "")
        if not command:
            return
        decision = self._engine.check_command(command)
        if self._on_event is not None:
            self._on_event(decision)
        if decision.decision != "allow":
            rsp.rsp = {
                "error": f"blocked by governance filter ({decision.rule})",
                "decision": decision.decision,
                "reason": decision.reason,
            }
            rsp.is_continue = False
