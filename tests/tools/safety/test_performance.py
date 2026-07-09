# Copyright (C) 2026 Tencent. All rights reserved.
# tRPC-Agent-Python is licensed under Apache-2.0.
from __future__ import annotations

from trpc_agent_sdk.tools.safety._policy import load_policy
from trpc_agent_sdk.tools.safety._scanner import scan


def test_500_lines_under_1_second():
    line = "x = 1\nprint(x)\n"  # benign line
    script = line * 250  # 500 lines
    report = scan(load_policy(), script, language="python")
    assert report.scan_duration_ms < 1000, f"took {report.scan_duration_ms}ms"