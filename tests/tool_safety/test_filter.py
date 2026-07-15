"""Tests for the ToolScriptSafetyFilter."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from trpc_agent_sdk.tools.safety._audit import InMemoryAuditSink
from trpc_agent_sdk.tools.safety._filter import BlockedExecutionError, ToolScriptSafetyFilter
from trpc_agent_sdk.tools.safety._guard import ToolSafetyGuard
from trpc_agent_sdk.tools.safety._models import SafetyDecision, ToolKind
from trpc_agent_sdk.tools.safety._policy import load_safety_policy_dict


@pytest.fixture
def flt(strict_policy_dict):
    policy = load_safety_policy_dict(strict_policy_dict)
    guard = ToolSafetyGuard(policy)
    sink = InMemoryAuditSink()
    return ToolScriptSafetyFilter(guard, audit_sink=sink)


def test_check_returns_decision_and_report(flt):
    decision, report = flt.check(
        "workspace_exec", {"command": "echo hi"},
        tool_kind=ToolKind.TOOL,
    )
    assert decision == SafetyDecision.ALLOW
    assert report.rule_ids == ("SAFE000",)
    assert len(flt.audit_sink.events) == 1  # type: ignore[attr-defined]


def test_check_blocks_dangerous(flt):
    decision, report = flt.check(
        "workspace_exec", {"command": "rm -rf /tmp/x"},
    )
    assert decision == SafetyDecision.DENY


def test_enforce_raises_on_block(flt):
    with pytest.raises(BlockedExecutionError):
        flt.enforce("workspace_exec", {"command": "rm -rf /tmp/x"})


def test_enforce_returns_report_on_allow(flt):
    report = flt.enforce("workspace_exec", {"command": "echo hi"})
    assert report.decision == SafetyDecision.ALLOW


def test_terminal_marker_is_true(flt):
    assert flt.terminal_before_handler is True


def test_audit_event_one_per_call(flt):
    for _ in range(3):
        flt.check("workspace_exec", {"command": "echo hi"})
    assert len(flt.audit_sink.events) == 3  # type: ignore[attr-defined]


def test_check_async_persists_audit_before_returning(flt):
    async def run():
        decision, _ = await flt.check_async(
            "workspace_exec", {"command": "echo hi"})
        assert decision == SafetyDecision.ALLOW
        assert len(flt.audit_sink.events) == 1  # type: ignore[attr-defined]

    asyncio.run(run())


def test_filter_audits_request_build_failure(flt):
    rsp = {}

    async def run():
        await flt._before(
            SimpleNamespace(tool_name="workspace_exec"), {}, rsp)

    asyncio.run(run())

    assert rsp["is_continue"] is False
    assert len(flt.audit_sink.events) == 1  # type: ignore[attr-defined]
    assert flt.audit_sink.events[0].decision == SafetyDecision.DENY  # type: ignore[attr-defined]


def test_filter_redacts_env_in_trace(flt):
    flt.check("workspace_exec", {
        "command": "echo hi",
        "env": {"SECRET": "value"},
    })
    # No direct way to inspect ContextVar from outside; verify the call
    # did not raise and audit did not leak env value.
    last_event = flt.audit_sink.events[-1]  # type: ignore[attr-defined]
    payload = last_event.model_dump_json()
    assert "value" not in payload


def test_skill_run_adapter(flt):
    decision, _ = flt.check(
        "skill_run", {"command": "echo hi"},
        tool_kind=ToolKind.SKILL,
    )
    assert decision == SafetyDecision.ALLOW
