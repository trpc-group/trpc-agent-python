# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Sandbox runners for the code review dry-run example."""

from __future__ import annotations

import time
from collections.abc import Sequence

from .filters import redact_text
from .governance import SandboxRequest
from .schemas import ParsedDiff
from .schemas import SandboxPolicy
from .schemas import SandboxRun


class FakeSandboxRunner:
    """Deterministic sandbox runner that never executes host commands."""

    def __init__(self, policy: SandboxPolicy) -> None:
        self._policy = policy

    def run_requests(self, requests: Sequence[SandboxRequest], parsed_diff: ParsedDiff) -> list[SandboxRun]:
        """Run all allowed requests deterministically."""
        return [self.run_request(request, parsed_diff) for request in requests]

    def run_request(self, request: SandboxRequest, parsed_diff: ParsedDiff) -> SandboxRun:
        """Run one fake sandbox request."""
        started = time.monotonic()
        stdout = ""
        stderr = ""
        exit_code = 0
        timed_out = False
        error_type = None

        if request.script_name == "diff_summary":
            stdout = f"files={len(parsed_diff.files)} hunks={parsed_diff.hunk_count} changed_lines={parsed_diff.changed_line_count}"
        elif request.script_name == "static_rules":
            stdout = "static_rules completed"
        elif request.script_name == "sandbox_failure_probe":
            exit_code = 2
            stderr = "simulated sandbox failure for fixture coverage"
            error_type = "SandboxCommandFailed"
        elif request.script_name == "timeout_probe":
            timed_out = True
            exit_code = 124
            stderr = "simulated sandbox timeout"
            error_type = "SandboxTimeout"
        else:
            exit_code = 1
            stderr = f"unsupported fake sandbox script: {request.script_name}"
            error_type = "UnsupportedScript"

        stdout_excerpt, stdout_truncated = _cap_output(redact_text(stdout), self._policy.max_output_bytes)
        stderr_excerpt, stderr_truncated = _cap_output(redact_text(stderr), self._policy.max_output_bytes)
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        return SandboxRun(
            id=f"sandbox-{request.script_name}",
            script_name=request.script_name,
            runtime=self._policy.runtime,
            decision="allow",
            exit_code=exit_code,
            timed_out=timed_out,
            duration_ms=duration_ms,
            stdout_excerpt=stdout_excerpt,
            stderr_excerpt=stderr_excerpt,
            output_truncated=stdout_truncated or stderr_truncated,
            error_type=error_type,
        )


def _cap_output(text: str, max_bytes: int) -> tuple[str, bool]:
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text, False
    capped = raw[:max_bytes].decode("utf-8", errors="ignore")
    return capped, True
