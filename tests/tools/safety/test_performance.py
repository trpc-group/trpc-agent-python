# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Performance guardrails for the static scanner."""

from __future__ import annotations

import time

from trpc_agent_sdk.tools.safety._models import SafetyDecision
from trpc_agent_sdk.tools.safety._models import SafetyScanRequest
from trpc_agent_sdk.tools.safety._scanner import ToolSafetyScanner


def test_scans_five_hundred_line_python_script_under_one_second():
    lines = [f"value_{index} = {index} * 2" for index in range(499)]
    lines.append("print(value_498)")
    script = "\n".join(lines)
    assert len(script.splitlines()) == 500
    scanner = ToolSafetyScanner()
    request = SafetyScanRequest(script=script, language="python", tool_name="performance_test")

    started = time.perf_counter()
    report = scanner.scan(request)
    elapsed = time.perf_counter() - started

    assert report.decision == SafetyDecision.ALLOW
    assert elapsed < 1.0, f"500-line scan took {elapsed:.3f}s"
    assert report.duration_ms < 1000
