# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""Tests for ToolSafetyFilter integration with the SDK filter chain."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from trpc_agent_sdk.safety import PolicyConfig

try:
    from trpc_agent_sdk.safety import ToolSafetyFilter
    from trpc_agent_sdk.safety import _SDK_AVAILABLE
    from trpc_agent_sdk.abc import FilterResult
except Exception:  # pylint: disable=broad-except
    _SDK_AVAILABLE = False
    ToolSafetyFilter = None  # type: ignore[assignment]
    FilterResult = None  # type: ignore[assignment]

pytestmark = pytest.mark.skipif(not _SDK_AVAILABLE, reason="tRPC-Agent SDK not importable")


def _make_filter(tmp_path: Path, **kwargs) -> ToolSafetyFilter:
    policy = PolicyConfig(
        whitelisted_domains=[],
        forbidden_paths=[".env"],
        block_on_review=kwargs.pop("block_on_review", False),
    )
    return ToolSafetyFilter(
        policy=policy,
        audit_path=str(tmp_path / "audit.jsonl"),
        **kwargs,
    )


def test_filter_blocks_dangerous_script(tmp_path: Path):
    flt = _make_filter(tmp_path)
    rsp = FilterResult()
    req = {"command": "rm -rf / && cat /etc/shadow"}
    asyncio.run(flt._before(None, req, rsp))  # pylint: disable=protected-access

    assert rsp.is_continue is False
    assert rsp.rsp["error"] == "TOOL_SAFETY_DENY"

    audit_path = Path(tmp_path / "audit.jsonl")
    assert audit_path.exists()
    line = audit_path.read_text(encoding="utf-8").strip().splitlines()[-1]
    rec = json.loads(line)
    assert rec["decision"] == "deny"
    assert rec["intercepted"] is True
    assert rec["tool_name"]


def test_filter_allows_safe_script(tmp_path: Path):
    flt = _make_filter(tmp_path)
    rsp = FilterResult()
    req = {"command": "ls -la"}
    asyncio.run(flt._before(None, req, rsp))  # pylint: disable=protected-access
    assert rsp.is_continue is True


def test_filter_dynamic_network_is_denied_by_default(tmp_path: Path):
    """Dynamic network targets are HIGH → DENY under default policy."""
    flt = _make_filter(tmp_path, block_on_review=False)
    rsp = FilterResult()
    req = {"command": "curl $URL"}
    asyncio.run(flt._before(None, req, rsp))  # pylint: disable=protected-access
    assert rsp.is_continue is False
    assert rsp.rsp["success"] is False
    assert rsp.rsp["error"] == "TOOL_SAFETY_DENY"


def test_filter_block_on_review_deny_still_blocks(tmp_path: Path):
    """When block_on_review=True, DENY (HIGH) findings must still block.

    This is a sanity check that block_on_review does not weaken DENY handling.
    The pure-MEDIUM review-block path is covered by
    test_filter_block_on_review_pure_medium_returns_needs_review.
    """
    flt = _make_filter(tmp_path, block_on_review=True)
    rsp = FilterResult()
    # curl $URL is HIGH/deny under default policy; block_on_review must NOT
    # downgrade a DENY to a non-blocking review.
    req = {"command": "curl $URL"}
    asyncio.run(flt._before(None, req, rsp))  # pylint: disable=protected-access
    assert rsp.is_continue is False
    assert rsp.rsp["error"] == "TOOL_SAFETY_DENY"
    assert rsp.rsp.get("success") is False


def test_filter_block_on_review_pure_medium_returns_needs_review(tmp_path: Path):
    """block_on_review=True + pure MEDIUM signal must return TOOL_SAFETY_NEEDS_REVIEW.

    'sleep 100 &' is a non-network background process → MEDIUM →
    NEEDS_HUMAN_REVIEW under the default policy (deny=HIGH, review=MEDIUM).
    With block_on_review=True the filter must block and emit the review error
    code (not DENY, since the decision is not DENY).
    """
    flt = _make_filter(tmp_path, block_on_review=True)
    rsp = FilterResult()
    req = {"command": "sleep 100 &"}
    asyncio.run(flt._before(None, req, rsp))  # pylint: disable=protected-access
    assert rsp.is_continue is False
    assert rsp.rsp["error"] == "TOOL_SAFETY_NEEDS_REVIEW"
    assert rsp.rsp.get("success") is False


def test_filter_extracts_code_blocks(tmp_path: Path):
    flt = _make_filter(tmp_path)
    rsp = FilterResult()
    req = {"code_blocks": [{"code": "import os\nos.system('rm -rf /')"}]}
    asyncio.run(flt._before(None, req, rsp))  # pylint: disable=protected-access
    assert rsp.is_continue is False


def test_filter_sleep_at_threshold_is_allowed(tmp_path: Path):
    """sleep N where N == max_timeout_seconds must NOT be flagged.

    The recommendation is "keep sleeps below N seconds", so the boundary
    N itself must be allowed (strictly greater-than, not >=). Both Python
    and bash forms of sleep(300) with the default 300s budget must ALLOW.
    """
    flt = _make_filter(tmp_path)
    # Python form: sleep(300)
    rsp_py = FilterResult()
    asyncio.run(flt._before(None, {"code": "import time\ntime.sleep(300)"}, rsp_py))  # pylint: disable=protected-access
    assert rsp_py.is_continue is True
    # Bash form: sleep 300
    rsp_sh = FilterResult()
    asyncio.run(flt._before(None, {"command": "sleep 300"}, rsp_sh))  # pylint: disable=protected-access
    assert rsp_sh.is_continue is True


def test_filter_contextvar_does_not_leak_across_calls(tmp_path: Path):
    """Regression: if a previous _before stashed a non-blocking review report
    and _after was skipped (e.g. handle errored or earlier filter set
    is_continue=False), the stash must NOT leak into the next call's _after.

    We simulate the leak scenario directly: stash a report, then run _before
    on a safe ALLOW command. _before must reset the stash at entry so the
    subsequent _after does not inject a stale safety_warning.

    Note: ContextVar.set inside a coroutine only affects that coroutine's
    context copy, so both the stash setup and the assertion must run inside
    the same asyncio.run to observe the leak.
    """
    from trpc_agent_sdk.safety._filter import _REVIEW_REPORT
    from trpc_agent_sdk.safety import SafetyScanner
    from trpc_agent_sdk.safety import ScanInput

    async def _scenario():
        # Prime the ContextVar with a stale report as if a prior _after was
        # skipped in this same async context.
        stale_report = SafetyScanner(PolicyConfig()).scan(ScanInput(script="sleep 100 &", language="bash"))
        assert stale_report.decision.value == "needs_human_review"
        _REVIEW_REPORT.set(stale_report)

        # Now scan a safe ALLOW command. _before must clear the stash at entry.
        flt = _make_filter(tmp_path)
        rsp = FilterResult()
        await flt._before(None, {"command": "echo hello"}, rsp)  # pylint: disable=protected-access
        assert rsp.is_continue is True
        # The stash must be None after _before (no new review was stashed for ALLOW).
        assert _REVIEW_REPORT.get() is None

    asyncio.run(_scenario())
