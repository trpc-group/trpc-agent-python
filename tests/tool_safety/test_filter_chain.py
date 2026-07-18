# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""Integration tests through the real filter chain (BaseFilter.run / run_filters).

These catch regressions where setting FilterResult.error to a non-Exception
would make run_filters raise TypeError.
"""
from __future__ import annotations

import asyncio

import pytest

from trpc_agent_sdk.safety import PolicyConfig
from trpc_agent_sdk.safety import ToolSafetyFilter

try:
    from trpc_agent_sdk.filter import run_filters
except Exception:  # pylint: disable=broad-except
    run_filters = None  # type: ignore[assignment]

pytestmark = pytest.mark.skipif(run_filters is None, reason="run_filters not importable")


def _policy(**kwargs) -> PolicyConfig:
    return PolicyConfig(whitelisted_domains=[], forbidden_paths=[".env"], **kwargs)


def test_run_filters_deny_returns_structured_dict_not_typeerror():
    """DENY must return rsp.rsp dict via run_filters without raising TypeError."""
    flt = ToolSafetyFilter(_policy())

    async def _handle():
        return {"success": True, "stdout": "should not run"}

    async def _run():
        return await run_filters(None, {"command": "rm -rf /"}, [flt], _handle)

    result = asyncio.run(_run())
    assert isinstance(result, dict)
    assert result.get("success") is False
    assert result.get("error") == "TOOL_SAFETY_DENY"
    assert "command" in result


def test_run_filters_allow_continues_to_handler():
    flt = ToolSafetyFilter(_policy())

    async def _handle():
        return {"success": True, "stdout": "ok"}

    async def _run():
        return await run_filters(None, {"command": "ls -la"}, [flt], _handle)

    result = asyncio.run(_run())
    assert result == {"success": True, "stdout": "ok"}


def test_run_filters_needs_review_does_not_crash_when_not_blocking():
    """Non-blocking review must not put a string into FilterResult.error."""
    # Use a policy where medium is review and deny is critical, with a pure
    # medium signal if available; dynamic network is now HIGH/deny by design.
    # This test ensures the allow path still works when is_continue stays True.
    flt = ToolSafetyFilter(_policy(block_on_review=False))

    async def _handle():
        return {"success": True, "stdout": "ran"}

    async def _run():
        return await run_filters(None, {"command": "echo hello"}, [flt], _handle)

    result = asyncio.run(_run())
    assert result["success"] is True


def test_run_filters_needs_review_merges_warning_into_result():
    """Non-blocking review must merge safety_warning into the tool result.

    Regression for CongkeChen's review comment: setting rsp.rsp in _before is
    useless because BaseFilter.run overwrites result.rsp with the tool's return
    value in the handle() step. _after must merge the warning fields so the
    caller actually sees them.
    """
    # A bash command that triggers NEEDS_HUMAN_REVIEW (MEDIUM): backgrounding a
    # non-network process. Network backgrounding is HIGH/DENY, but plain
    # background is MEDIUM/review under the default policy.
    flt = ToolSafetyFilter(_policy(block_on_review=False))

    async def _handle():
        return {"success": True, "stdout": "ran"}

    async def _run():
        return await run_filters(None, {"command": "sleep 100 &"}, [flt], _handle)

    result = asyncio.run(_run())
    assert result["success"] is True
    assert result.get("safety_warning") == "TOOL_SAFETY_NEEDS_REVIEW"
    assert "safety_risk_level" in result
    assert "safety_rule_ids" in result


def test_base_filter_run_deny_keeps_error_none():
    """Direct BaseFilter.run must keep error=None and is_continue=False on deny."""
    flt = ToolSafetyFilter(_policy())

    async def _handle():
        return {"success": True}

    result = asyncio.run(flt.run(None, {"command": "rm -rf /"}, _handle))
    # BaseFilter.run returns FilterResult (or tuple in some adapters).
    if isinstance(result, tuple):
        rsp, error = result
        assert error is None or isinstance(error, Exception)
        # When error is None, rsp should be the structured dict.
        if error is None:
            assert isinstance(rsp, dict)
            assert rsp.get("success") is False
    else:
        assert result.is_continue is False
        assert result.error is None
        assert isinstance(result.rsp, dict)
        assert result.rsp.get("error") == "TOOL_SAFETY_DENY"
