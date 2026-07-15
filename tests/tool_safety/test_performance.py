"""Performance test: scan a 500-line Python script in under 1 second."""

from __future__ import annotations

import time

import pytest

from trpc_agent_sdk.tools.safety._guard import ToolSafetyGuard
from trpc_agent_sdk.tools.safety._models import SafetyDecision, SafetyScanRequest, ScriptLanguage
from trpc_agent_sdk.tools.safety._policy import load_safety_policy_dict


def _make_large_script(lines: int = 500) -> str:
    body = ["import os", "import subprocess"]
    for i in range(lines - 5):
        body.append(f"x_{i} = {i}")
    # The dangerous call is on the last line; the scanner must still
    # reach it after walking the whole tree.
    body.append("subprocess.run('rm -rf /', shell=True)")
    body.append("")
    return "\n".join(body)


def test_scan_500_lines_under_one_second(strict_policy_dict):
    guard = ToolSafetyGuard(load_safety_policy_dict(strict_policy_dict))
    script = _make_large_script(500)
    # Warm up so import / regex compile cost does not skew the result.
    guard.scan(SafetyScanRequest(
        tool_name="t", language=ScriptLanguage.PYTHON, script=script,
    ))
    samples = []
    for _ in range(5):
        start = time.perf_counter()
        report = guard.scan(SafetyScanRequest(
            tool_name="t", language=ScriptLanguage.PYTHON, script=script,
        ))
        samples.append(time.perf_counter() - start)
    p95 = sorted(samples)[int(len(samples) * 0.95) - 1] \
        if len(samples) > 1 else samples[0]
    assert report.decision == SafetyDecision.DENY
    assert p95 < 1.0, f"p95={p95:.3f}s exceeds 1.0s budget"


def test_scan_bash_500_lines_under_one_second(strict_policy_dict):
    guard = ToolSafetyGuard(load_safety_policy_dict(strict_policy_dict))
    lines = ["# comment"] * 495 + ["rm -rf /tmp/x"]
    script = "\n".join(lines)
    # Warm up.
    guard.scan(SafetyScanRequest(
        tool_name="t", language=ScriptLanguage.BASH, script=script,
    ))
    samples = []
    for _ in range(5):
        start = time.perf_counter()
        guard.scan(SafetyScanRequest(
            tool_name="t", language=ScriptLanguage.BASH, script=script,
        ))
        samples.append(time.perf_counter() - start)
    p95 = sorted(samples)[int(len(samples) * 0.95) - 1] \
        if len(samples) > 1 else samples[0]
    assert p95 < 1.0, f"p95={p95:.3f}s exceeds 1.0s budget"
