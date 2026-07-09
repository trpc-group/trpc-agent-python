# Copyright (C) 2026 Tencent. All rights reserved.
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unified scan entry: dispatch to python/bash, time, aggregate."""
from __future__ import annotations

import re
import time
from typing import Optional

from trpc_agent_sdk.tools.safety._bash_scanner import scan_bash
from trpc_agent_sdk.tools.safety._decision import aggregate
from trpc_agent_sdk.tools.safety._policy import Policy
from trpc_agent_sdk.tools.safety._python_scanner import scan_python
from trpc_agent_sdk.tools.safety._types import SafetyReport

_PY_HINTS = re.compile(r"^\s*(import |from |def |class |print\()", re.MULTILINE)


def detect_language(script: str) -> str:
    """Heuristic: python if it has python markers, else bash."""
    if _PY_HINTS.search(script):
        return "python"
    if re.search(r"^\s*(rm |curl |pip |sudo |apt |npm |ls |cat |echo )", script, re.MULTILINE):
        return "bash"
    return "python"  # default


def scan(policy: Policy,
         script: str,
         language: str = "auto",
         meta: Optional[dict] = None) -> SafetyReport:
    """Scan one script; return an aggregated SafetyReport.

    Args:
        policy: resolved policy.
        script: script content.
        language: "python" | "bash" | "auto".
        meta: optional dict (tool_name, cwd, ...) reserved for audit/OTel.
    """
    lang = detect_language(script) if language == "auto" else language
    start = time.perf_counter()
    if lang == "python":
        findings = scan_python(policy, script)
    else:
        findings = scan_bash(policy, script)
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    report = aggregate(findings, policy)
    report.scan_duration_ms = elapsed_ms
    return report