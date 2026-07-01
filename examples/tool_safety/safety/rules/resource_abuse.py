# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""Rule: resource abuse patterns.

Flags infinite loops, fork bombs, oversized file writes, very long sleeps,
and suspiciously high concurrency. These patterns can exhaust CPU, memory, or
disk in shared execution environments.
"""
from __future__ import annotations

import ast
import re

from .base import SafetyRule
from .base import bash_lines
from .base import evidence_snippet
from .base import get_string_literal
from .base import iter_python_calls
from .base import normalize_language
from .base import parse_python_ast
from ..policy import PolicyConfig
from ..types import RiskLevel
from ..types import SafetyFinding
from ..types import ScanInput


# Fork bomb signatures (bash).
_FORK_BOMB = re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\};\s*:", re.IGNORECASE)

# Long sleep: sleep with a numeric arg >= 3600.
_LONG_SLEEP_BASH = re.compile(r"\bsleep\s+(\d+)", re.IGNORECASE)

# Oversized write: dd of=... bs=... count=... or head -c <big> > file
_DD_WRITE = re.compile(r"\bdd\b", re.IGNORECASE)
_BIG_WRITE = re.compile(r"(head|tail|yes|/dev/zero|/dev/urandom)", re.IGNORECASE)

# Suspiciously high concurrency in python.
_HIGH_CONCURRENCY_CALLS = {
    "concurrent.futures.ThreadPoolExecutor",
    "concurrent.futures.ProcessPoolExecutor",
    "multiprocessing.Pool",
    "asyncio.gather",
}


class ResourceAbuseRule(SafetyRule):
    """Detect resource abuse patterns: infinite loops, fork bombs, big writes."""

    rule_id = "R005_resource_abuse"
    rule_name = "Resource Abuse"
    risk_type = "resource_abuse"
    default_level = RiskLevel.HIGH
    languages = ("python", "bash")

    def check(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        lang = normalize_language(scan_input)
        findings: list[SafetyFinding] = []
        if lang == "python":
            findings.extend(self._check_python(scan_input, policy))
        findings.extend(self._check_bash(scan_input, policy))
        return findings

    # ----- python -----

    def _check_python(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        tree = parse_python_ast(scan_input.script)
        if tree is None:
            return findings

        for node in ast.walk(tree):
            # Infinite loops: while True / while 1 with no break
            if isinstance(node, ast.While):
                if _is_truthy_constant(node.test) and not _has_break(node):
                    findings.append(self._finding(
                        "Infinite while loop with no break",
                        node.lineno,
                        evidence=f"while {ast.unparse(node.test)}: ...",
                        rec="Add a termination condition or bounded iteration.",
                        level=RiskLevel.HIGH,
                    ))
            # Long sleep
            if isinstance(node, ast.Call):
                fname = _call_name(node)
                if fname and "sleep" in fname.lower():
                    arg = node.args[0] if node.args else None
                    secs = _const_int(arg)
                    if secs is not None and secs >= policy.max_timeout_seconds:
                        findings.append(self._finding(
                            f"Long sleep({secs}s) exceeds timeout budget",
                            node.lineno,
                            evidence=f"sleep({secs})",
                            rec=f"Keep sleeps below {policy.max_timeout_seconds}s.",
                            level=RiskLevel.MEDIUM,
                        ))
                    elif secs is None:
                        findings.append(self._finding(
                            "sleep() with non-constant duration",
                            node.lineno,
                            evidence=f"sleep(<dynamic>)",
                            rec="Use a bounded constant sleep duration.",
                            level=RiskLevel.LOW,
                        ))
                # High concurrency
                if fname and any(c in fname for c in _HIGH_CONCURRENCY_CALLS):
                    findings.append(self._finding(
                        f"High-concurrency primitive {fname}()",
                        node.lineno,
                        evidence=f"{fname}(...)",
                        rec="Bound max_workers; unbounded pools can exhaust resources.",
                        level=RiskLevel.MEDIUM,
                    ))
        return findings

    # ----- bash -----

    def _check_bash(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        for lineno, line in bash_lines(scan_input.script):
            if _FORK_BOMB.search(line):
                findings.append(self._finding(
                    "Fork bomb detected",
                    lineno,
                    evidence=line,
                    rec="Remove fork bomb patterns entirely.",
                    level=RiskLevel.CRITICAL,
                ))
            m = _LONG_SLEEP_BASH.search(line)
            if m and int(m.group(1)) >= policy.max_timeout_seconds:
                findings.append(self._finding(
                    f"Long sleep {m.group(1)}s exceeds timeout budget",
                    lineno,
                    evidence=line,
                    rec=f"Keep sleeps below {policy.max_timeout_seconds}s.",
                    level=RiskLevel.MEDIUM,
                ))
            if _DD_WRITE.search(line):
                findings.append(self._finding(
                    "dd can write large amounts of data",
                    lineno,
                    evidence=line,
                    rec="Avoid dd in tool scripts; use bounded file operations.",
                    level=RiskLevel.HIGH,
                ))
            if _BIG_WRITE.search(line) and ">" in line:
                findings.append(self._finding(
                    "Unbounded large write via shell",
                    lineno,
                    evidence=line,
                    rec="Cap output size; unbounded writes can fill disk.",
                    level=RiskLevel.MEDIUM,
                ))
        return findings

    def _finding(self, msg, line, evidence, rec, level=None) -> SafetyFinding:
        return SafetyFinding(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            risk_type=self.risk_type,
            risk_level=level or self.default_level,
            evidence=evidence_snippet(evidence),
            line=line,
            recommendation=rec,
            metadata={"message": msg},
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_truthy_constant(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and bool(node.value) or (
        isinstance(node, ast.Name) and node.id in {"True", "__debug__"}
    )


def _has_break(node: ast.AST) -> bool:
    """True when *node*'s body contains a break statement (not inside nested loops)."""
    for child in ast.walk(node):
        # Skip nested for/while bodies' own breaks.
        if child is node:
            continue
        if isinstance(child, ast.Break):
            return True
    return False


def _const_int(node: ast.AST | None) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    return None


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts = []
        cur = func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))
    return None
