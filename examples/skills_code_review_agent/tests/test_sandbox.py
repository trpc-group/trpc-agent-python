"""Tests for sandbox module."""

import pytest

from pipeline.sandbox import execute_in_sandbox


class TestSandboxExecution:
    """Sandbox execution with safety limits."""

    def test_normal_execution(self):
        result = execute_in_sandbox(
            command=["python", "-c", "print('hello')"],
            timeout_seconds=5,
        )
        assert "hello" in result.stdout
        assert result.exit_code == 0
        assert not result.timed_out

    def test_nonzero_exit(self):
        result = execute_in_sandbox(
            command=["python", "-c", "import sys; sys.exit(42)"],
            timeout_seconds=5,
        )
        assert result.exit_code == 42
        assert not result.timed_out

    def test_timeout(self):
        result = execute_in_sandbox(
            command=["python", "-c", "import time; time.sleep(999)"],
            timeout_seconds=1,
        )
        assert result.timed_out
        # Should not crash — main process continues

    def test_command_not_found(self):
        result = execute_in_sandbox(
            command=["nonexistent_command_xyz"],
            timeout_seconds=5,
        )
        assert result.exit_code != 0

    def test_output_capture(self):
        result = execute_in_sandbox(
            command=["python", "-c",
                     "import sys; print('out'); print('err', file=sys.stderr)"],
            timeout_seconds=5,
        )
        assert "out" in result.stdout
        assert "err" in result.stderr

    def test_duration_recorded(self):
        result = execute_in_sandbox(
            command=["python", "-c", "print('hi')"],
            timeout_seconds=5,
        )
        assert result.duration_ms >= 0

    def test_env_allowlist(self):
        """Only whitelisted env vars should pass through."""
        result = execute_in_sandbox(
            command=["python", "-c", "import os; print(os.environ.get('PATH', 'nope'))"],
            env_allowlist=["PATH"],
            timeout_seconds=5,
        )
        assert "nope" not in result.stdout  # PATH should exist


class TestSandboxResilience:
    """Sandbox must not crash the main pipeline."""

    def test_failure_is_recorded_not_raised(self):
        """Sandbox failures return error info, don't propagate exceptions."""
        # This should return a result, not raise
        result = execute_in_sandbox(
            command=["python", "-c", "raise SystemExit(1)"],
            timeout_seconds=5,
        )
        # Function completed — it returned a result
        assert result is not None
        assert hasattr(result, "exit_code")
