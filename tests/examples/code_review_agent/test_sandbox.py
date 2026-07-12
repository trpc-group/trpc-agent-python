# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for fake sandbox execution."""

from __future__ import annotations

from examples.code_review_agent.agent.diff_parser import parse_unified_diff
from examples.code_review_agent.agent.governance import SandboxRequest
from examples.code_review_agent.agent.sandbox import FakeSandboxRunner
from examples.code_review_agent.agent.schemas import SandboxPolicy


def test_fake_sandbox_success_records_summary() -> None:
    parsed = parse_unified_diff("diff --git a/a.py b/a.py\n")
    runner = FakeSandboxRunner(SandboxPolicy())

    run = runner.run_request(SandboxRequest(script_name="diff_summary", command=("python", "scripts/diff_summary.py")), parsed)

    assert run.exit_code == 0
    assert "files=" in run.stdout_excerpt


def test_fake_sandbox_failure_is_recorded() -> None:
    runner = FakeSandboxRunner(SandboxPolicy())

    run = runner.run_request(
        SandboxRequest(script_name="sandbox_failure_probe", command=("python", "scripts/sandbox_failure_probe.py")),
        parse_unified_diff(""),
    )

    assert run.exit_code == 2
    assert run.error_type == "SandboxCommandFailed"


def test_fake_sandbox_timeout_is_recorded() -> None:
    runner = FakeSandboxRunner(SandboxPolicy())

    run = runner.run_request(
        SandboxRequest(script_name="timeout_probe", command=("python", "scripts/timeout_probe.py")), parse_unified_diff("")
    )

    assert run.timed_out is True
    assert run.error_type == "SandboxTimeout"


def test_fake_sandbox_truncates_output() -> None:
    runner = FakeSandboxRunner(SandboxPolicy(max_output_bytes=5))

    run = runner.run_request(SandboxRequest(script_name="diff_summary", command=("python", "scripts/diff_summary.py")), parse_unified_diff(""))

    assert run.output_truncated is True
    assert len(run.stdout_excerpt.encode("utf-8")) <= 5
