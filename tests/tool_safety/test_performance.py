# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""Performance test: scanning a 500-line script must complete within 1 second."""
from __future__ import annotations

import time

from trpc_agent_sdk.safety import PolicyConfig
from trpc_agent_sdk.safety import SafetyScanner
from trpc_agent_sdk.safety import ScanInput


def test_scan_500_lines_under_1s():
    lines = []
    for i in range(250):
        lines.append(f"x{i} = {i}")
    for i in range(250):
        lines.append(f"def f{i}():\n    return {i}")
    script = "\n".join(lines)
    assert len(script.splitlines()) >= 500

    scanner = SafetyScanner(PolicyConfig())
    start = time.perf_counter()
    report = scanner.scan(ScanInput(script=script, language="python"))
    elapsed = time.perf_counter() - start

    assert elapsed <= 1.0, f"scan took {elapsed:.3f}s, exceeds 1s budget"
    assert report.scan_duration_ms <= 1000.0
