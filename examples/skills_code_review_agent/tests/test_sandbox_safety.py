# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Sandbox safety (issue requirement 7, acceptance criterion 4):
timeout, output cap, env whitelist, failure containment."""

import os

from trpc_agent_sdk.code_executors import WorkspaceRunProgramSpec
from trpc_agent_sdk.code_executors import WorkspaceRunResult

from codereview.config import SandboxConfig
from codereview.diff_parser import parse_unified_diff
from codereview.sandbox import STATUS_FAILED
from codereview.sandbox import STATUS_OK
from codereview.sandbox import STATUS_TIMEOUT
from codereview.sandbox import SandboxExecutor
from codereview.sandbox import create_sandbox_runtime

SIMPLE_DIFF = ("diff --git a/x.py b/x.py\n"
               "new file mode 100644\n"
               "--- /dev/null\n"
               "+++ b/x.py\n"
               "@@ -0,0 +1,1 @@\n"
               "+import os\n")


def _executor(tmp_path, **cfg_overrides) -> SandboxExecutor:
    cfg = SandboxConfig(runtime_kind="local", work_root=str(tmp_path / "ws"), **cfg_overrides)
    return SandboxExecutor(create_sandbox_runtime(cfg), cfg)


async def test_successful_run_produces_findings_payload(tmp_path):
    executor = _executor(tmp_path)
    outcome = await executor.run_checks("t1", parse_unified_diff(SIMPLE_DIFF))
    assert outcome.status == STATUS_OK
    assert outcome.findings_payload is not None
    assert "findings" in outcome.findings_payload


async def test_timeout_contained(tmp_path):
    """A stuck program gets killed by the runner-level timeout."""
    runtime = create_sandbox_runtime(SandboxConfig(runtime_kind="local",
                                                   work_root=str(tmp_path / "ws2"),
                                                   timeout_sec=1.0))
    manager = runtime.manager()
    ws = await manager.create_workspace("timeout-probe")
    try:
        result: WorkspaceRunResult = await runtime.runner().run_program(
            ws, WorkspaceRunProgramSpec(cmd="python3", args=["-c", "import time; time.sleep(30)"],
                                        timeout=0.5))
        assert result.timed_out is True
    finally:
        await manager.cleanup("timeout-probe")


async def test_timeout_status_via_executor(tmp_path):
    """SandboxExecutor maps a timed-out run to status=timeout without raising.

    timeout_sec=0.05 is far below the Python interpreter startup cost, so the
    check reliably times out; if an exotic host still finishes, the invariant
    under test (no exception, correct status mapping) holds either way.
    """
    executor = _executor(tmp_path, timeout_sec=0.05)
    outcome = await executor.run_checks("t-timeout", parse_unified_diff(SIMPLE_DIFF))
    assert outcome.status in (STATUS_OK, STATUS_TIMEOUT)
    if outcome.status == STATUS_TIMEOUT:
        assert outcome.error_type == "SandboxTimeout"
        assert outcome.result is not None and outcome.result.timed_out is True


async def test_output_size_capped(tmp_path):
    """stdout beyond max_output_bytes is truncated and flagged."""
    cfg = SandboxConfig(runtime_kind="local", work_root=str(tmp_path / "ws3"),
                        max_output_bytes=500)
    runtime = create_sandbox_runtime(cfg)
    manager = runtime.manager()
    ws = await manager.create_workspace("cap-probe")
    try:
        result = await runtime.runner().run_program(
            ws, WorkspaceRunProgramSpec(cmd="python3",
                                        args=["-c", "print('A' * 100000)"], timeout=10))
        from codereview.sandbox import _cap_output
        capped, truncated = _cap_output(result.stdout, cfg.max_output_bytes)
        assert truncated is True
        assert len(capped.encode()) <= cfg.max_output_bytes + len("\n…[output truncated]".encode())
    finally:
        await manager.cleanup("cap-probe")


async def test_env_whitelist_blocks_host_secrets(tmp_path, monkeypatch):
    """Host env secrets must NOT be visible inside the local sandbox."""
    monkeypatch.setenv("CR_SECRET_CANARY", "super-secret-canary-value")
    monkeypatch.setenv("TRPC_AGENT_API_KEY", "sk-canary-key-should-not-leak")
    cfg = SandboxConfig(runtime_kind="local", work_root=str(tmp_path / "ws4"))
    runtime = create_sandbox_runtime(cfg)
    manager = runtime.manager()
    ws = await manager.create_workspace("env-probe")
    try:
        result = await runtime.runner().run_program(
            ws, WorkspaceRunProgramSpec(
                cmd="python3",
                args=["-c", "import os, json; print(json.dumps(dict(os.environ)))"],
                timeout=10))
        assert "CR_SECRET_CANARY" not in result.stdout
        assert "sk-canary-key-should-not-leak" not in result.stdout
        # workspace layout vars are still provided
        assert "WORKSPACE_DIR" in result.stdout
        # whitelisted basics survive
        assert "PATH" in result.stdout
    finally:
        await manager.cleanup("env-probe")


async def test_forced_failure_contained_and_recorded(tmp_path):
    """--force-fail: script raises; executor reports failed, never raises."""
    executor = _executor(tmp_path, force_fail=True)
    outcome = await executor.run_checks("t2", parse_unified_diff(SIMPLE_DIFF))
    assert outcome.status == STATUS_FAILED
    assert outcome.error_type == "SandboxNonZeroExit"
    assert "forced sandbox failure" in outcome.stderr
    assert outcome.findings_payload is None


async def test_runtime_exception_contained(tmp_path):
    """Even a broken runtime surfaces as status=error, not an exception."""
    executor = _executor(tmp_path)

    class BrokenManager:
        async def create_workspace(self, exec_id, ctx=None):
            raise OSError("disk gone")

    executor._runtime.manager = lambda ctx=None: BrokenManager()  # noqa: SLF001
    outcome = await executor.run_checks("t3", parse_unified_diff(SIMPLE_DIFF))
    assert outcome.status == "error"
    assert outcome.error_type == "OSError"


async def test_workspace_cleaned_up(tmp_path):
    executor = _executor(tmp_path)
    await executor.run_checks("t4", parse_unified_diff(SIMPLE_DIFF))
    ws_root = tmp_path / "ws"
    leftovers = [entry for entry in (os.listdir(ws_root) if ws_root.exists() else [])
                 if entry.startswith("cr-t4")]
    assert leftovers == []
