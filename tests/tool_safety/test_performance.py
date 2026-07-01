# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""Performance test: scanning a 500-line script must complete within 1 second."""
from __future__ import annotations

import time
from pathlib import Path

from examples.tool_safety.safety import Decision
from examples.tool_safety.safety import PolicyConfig
from examples.tool_safety.safety import SafetyScanner
from examples.tool_safety.safety import ScanInput


def test_scan_500_lines_under_1s():
    """Issue criterion 4: single 500-line script scan <= 1s."""
    lines = []
    # ~250 safe lines
    for i in range(250):
        lines.append(f"x{i} = {i}")
    # ~250 lines mixing various patterns (still scannable)
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


def test_scan_real_500_line_sample_under_1s(samples_dir):
    """Scan the actual 13_safe_500_lines.py sample file (500 lines)."""
    sample = samples_dir / "13_safe_500_lines.py"
    assert sample.exists(), f"missing 500-line sample: {sample}"
    script = sample.read_text(encoding="utf-8")
    assert len(script.splitlines()) == 500

    scanner = SafetyScanner(PolicyConfig())
    start = time.perf_counter()
    report = scanner.scan(ScanInput(script=script, language="python", tool_name=sample.name))
    elapsed = time.perf_counter() - start

    assert elapsed <= 1.0, f"scan took {elapsed:.3f}s, exceeds 1s budget"
    # 500-line sample is intentionally safe.
    assert report.decision == Decision.ALLOW
