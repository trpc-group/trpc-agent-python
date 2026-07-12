# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for sandbox execution."""

from __future__ import annotations

from typing import Any

from trpc_agent_sdk.utils import CommandExecResult

from examples.code_review_agent.agent.diff_parser import parse_unified_diff
from examples.code_review_agent.agent.governance import SandboxRequest
from examples.code_review_agent.agent.sandbox import ContainerSandboxRunner
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


def test_container_sandbox_executes_allowlisted_script_with_stdin(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeContainerClient:
        def __init__(self, config) -> None:
            captured["config"] = config

        async def exec_run(self, cmd, command_args):
            captured["cmd"] = cmd
            captured["stdin"] = command_args.stdin
            captured["timeout"] = command_args.timeout
            return CommandExecResult(stdout='{"script":"diff_summary"}\n', stderr="", exit_code=0, is_timeout=False)

        def _cleanup_container(self) -> None:
            captured["cleaned"] = True

    from trpc_agent_sdk.code_executors.container import CommandArgs
    from trpc_agent_sdk.code_executors.container import ContainerConfig

    monkeypatch.setattr(
        "examples.code_review_agent.agent.sandbox._load_container_runtime",
        lambda: (CommandArgs, FakeContainerClient, ContainerConfig),
    )
    runner = ContainerSandboxRunner(SandboxPolicy(runtime="container", timeout_seconds=7), image="python:test")

    run = runner.run_request(
        SandboxRequest(script_name="diff_summary", command=("python", "scripts/diff_summary.py")),
        "diff --git a/a.py b/a.py\n",
    )

    assert run.runtime == "container"
    assert run.exit_code == 0
    assert run.error_type is None
    assert captured["cmd"] == ["python3", "/workspace/scripts/diff_summary.py"]
    assert captured["stdin"] == "diff --git a/a.py b/a.py\n"
    assert captured["timeout"] == 7
    assert captured["config"].host_config["network_mode"] == "none"
    assert captured["cleaned"] is True


def test_container_sandbox_records_timeout(monkeypatch) -> None:
    class FakeContainerClient:
        def __init__(self, config) -> None:
            pass

        async def exec_run(self, cmd, command_args):
            return CommandExecResult(stdout="", stderr="timed out", exit_code=-1, is_timeout=True)

        def _cleanup_container(self) -> None:
            pass

    from trpc_agent_sdk.code_executors.container import CommandArgs
    from trpc_agent_sdk.code_executors.container import ContainerConfig

    monkeypatch.setattr(
        "examples.code_review_agent.agent.sandbox._load_container_runtime",
        lambda: (CommandArgs, FakeContainerClient, ContainerConfig),
    )
    runner = ContainerSandboxRunner(SandboxPolicy(runtime="container"), image="python:test")

    run = runner.run_request(
        SandboxRequest(script_name="diff_summary", command=("python", "scripts/diff_summary.py")),
        "diff --git a/a.py b/a.py\n",
    )

    assert run.timed_out is True
    assert run.error_type == "SandboxTimeout"


def test_container_sandbox_rejects_unmapped_script_without_docker() -> None:
    runner = ContainerSandboxRunner(SandboxPolicy(runtime="container"), image="python:test")

    run = runner.run_request(SandboxRequest(script_name="sandbox_failure_probe", command=("python", "x.py")), "")

    assert run.exit_code == 1
    assert run.error_type == "SandboxScriptUnavailable"
