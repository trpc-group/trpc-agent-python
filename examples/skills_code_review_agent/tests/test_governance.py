# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for governance decisions and the tool filter."""
from trpc_agent_sdk.abc import FilterResult

from review.governance import GovernanceEngine
from filters_cr.governance_filter import GovernanceToolFilter


def test_allowlisted_script_allowed():
    eng = GovernanceEngine()
    d = eng.check_script("check_security.py", ["work/inputs/changes.diff"])
    assert d.decision == "allow"


def test_unknown_script_denied():
    d = GovernanceEngine().check_script("steal_data.py", ["work/inputs/changes.diff"])
    assert d.decision == "deny"
    assert d.rule == "script_allowlist"


def test_forbidden_path_denied():
    eng = GovernanceEngine()
    assert eng.check_script("check_security.py", ["/etc/passwd"]).decision == "deny"
    assert eng.check_script("check_security.py", ["../../secrets"]).decision == "deny"


def test_budget_exceeded_denied():
    eng = GovernanceEngine(max_runs=1)
    eng.record_run(0.1)
    d = eng.check_script("check_security.py", ["work/inputs/changes.diff"])
    assert d.decision == "deny"
    assert d.rule == "budget_exceeded"


def test_command_network_tool_denied():
    d = GovernanceEngine().check_command("curl http://evil.example.com")
    assert d.decision == "deny"
    assert d.rule == "network_policy"


def test_command_risk_token_needs_review():
    d = GovernanceEngine().check_command("sudo rm -rf /")
    assert d.decision == "needs_human_review"


def test_command_allowlisted_python_script_allowed():
    d = GovernanceEngine().check_command(
        "python3 skills/code-review/scripts/check_security.py work/inputs/changes.diff")
    assert d.decision == "allow"


def test_command_unknown_executable_needs_review():
    d = GovernanceEngine().check_command("make all")
    assert d.decision == "needs_human_review"


async def test_filter_blocks_denied_command():
    events = []
    filt = GovernanceToolFilter(GovernanceEngine(), on_event=events.append)
    rsp = FilterResult()
    await filt._before(None, {"skill": "code-review", "command": "curl http://x"}, rsp)
    assert rsp.is_continue is False
    assert "blocked" in str(rsp.rsp)
    assert events and events[0].decision == "deny"


async def test_filter_allows_good_command():
    filt = GovernanceToolFilter(GovernanceEngine())
    rsp = FilterResult()
    await filt._before(None, {
        "skill": "code-review",
        "command": "python3 skills/code-review/scripts/parse_diff.py work/inputs/changes.diff",
    }, rsp)
    assert rsp.is_continue is True
