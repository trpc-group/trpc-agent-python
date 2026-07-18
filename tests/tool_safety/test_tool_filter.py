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


def test_filter_review_does_not_block_by_default(tmp_path: Path):
    flt = _make_filter(tmp_path, block_on_review=False)
    rsp = FilterResult()
    req = {"command": "curl $URL"}
    asyncio.run(flt._before(None, req, rsp))  # pylint: disable=protected-access
    assert rsp.is_continue is True


def test_filter_block_on_review(tmp_path: Path):
    flt = _make_filter(tmp_path, block_on_review=True)
    rsp = FilterResult()
    req = {"command": "curl $URL"}
    asyncio.run(flt._before(None, req, rsp))  # pylint: disable=protected-access
    assert rsp.is_continue is False
    assert rsp.rsp["error"] in ("TOOL_SAFETY_DENY", "TOOL_SAFETY_NEEDS_REVIEW")


def test_filter_extracts_code_blocks(tmp_path: Path):
    flt = _make_filter(tmp_path)
    rsp = FilterResult()
    req = {"code_blocks": [{"code": "import os\nos.system('rm -rf /')"}]}
    asyncio.run(flt._before(None, req, rsp))  # pylint: disable=protected-access
    assert rsp.is_continue is False
