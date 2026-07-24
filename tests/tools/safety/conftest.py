# Copyright (C) 2026 Tencent. All rights reserved.
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared fixtures for tool-safety tests.

Redirects the safety audit jsonl log to a per-test temp path so integration
tests (filter / executor guard) that invoke record_safety_decision() do not
write audit files into the repository working tree.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _redirect_safety_audit(tmp_path, monkeypatch):
    """Point TRPC_AGENT_TOOL_SAFETY_AUDIT at a temp file for every test."""
    monkeypatch.setenv(
        "TRPC_AGENT_TOOL_SAFETY_AUDIT", str(tmp_path / "test_audit.jsonl")
    )
    yield tmp_path / "test_audit.jsonl"
