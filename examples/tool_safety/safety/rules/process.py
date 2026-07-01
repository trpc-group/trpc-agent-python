# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""Rule: process and system command execution.

Flags subprocess/os.system/shell pipelines, background processes, privilege
escalation (sudo/su/doas), and shell-injection-prone patterns like ``eval`` or
``shell=True``.
"""
from __future__ import annotations

import ast
import re

from .base import SafetyRule
from .base import bash_lines
from .base import evidence_snippet
from .base import iter_python_calls
from .base import normalize_language
from .base import parse_python_ast
from ..policy import PolicyConfig
from ..types import RiskLevel
from ..types import SafetyFinding
from ..types import ScanInput


_PY_PROCESS_CALLS = {
    "os.system", "os.popen", "os.exec", "os.execv", "os.execve", "os.spawn",
    "subprocess.Popen", "subprocess.run", "subprocess.call", "subprocess.check_call",
    "subprocess.check_output", "subprocess.call.check_output",
    "commands.getoutput", "commands.getstatusoutput",
}

_PRIVILEGE_CMDS = {"sudo", "su", "doas", "pkexec", "runuser"}

# Shell-injection-risky Python builtins.
_INJECTION_BUILTINS = {"eval", "exec", "compile"}


class ProcessRule(SafetyRule):
    """Detect process spawning, shell injection, and privilege escalation."""

    rule_id = "R003_process_system"
    rule_name = "Process / System Command"
    risk_type = "process"
    default_level = RiskLevel.HIGH
    languages = ("python", "bash")

    def check(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        lang = normalize_language(scan_input)
        if lang == "python":
            return self._check_python(scan_input, policy)
        return self._check_bash(scan_input, policy)

    # ----- python -----

    def _check_python(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        tree = parse_python_ast(scan_input.script)
        if tree is None:
            return findings

        for node, name in iter_python_calls(tree):
            lname = name.lower()
            if lname in {c.lower() for c in _PY_PROCESS_CALLS}:
                shell_true = _has_shell_true(node)
                findings.append(self._finding(
                    f"Process spawn via {name}()",
                    node.lineno,
                    evidence=f"{name}(...)",
                    rec="Avoid spawning subprocesses; if unavoidable use shell=False and validate args.",
                    level=RiskLevel.CRITICAL if shell_true else RiskLevel.HIGH,
                    extra={"shell_true": shell_true},
                ))
            if name in _INJECTION_BUILTINS:
                findings.append(self._finding(
                    f"Use of {name}() enables shell/code injection",
                    node.lineno,
                    evidence=f"{name}(...)",
                    rec=f"Remove {name}(); it allows arbitrary code execution.",
                    level=RiskLevel.CRITICAL,
                ))
        return findings

    # ----- bash -----

    def _check_bash(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        for lineno, line in bash_lines(scan_input.script):
            tokens = line.split()
            if not tokens:
                continue
            cmd = tokens[0]
            if cmd in _PRIVILEGE_CMDS:
                findings.append(self._finding(
                    f"Privilege escalation via {cmd}",
                    lineno,
                    evidence=line,
                    rec=f"Remove {cmd}; tool scripts must not escalate privileges.",
                    level=RiskLevel.CRITICAL,
                ))
            # Background process
            if line.rstrip().endswith("&") and not line.rstrip().endswith("&&"):
                findings.append(self._finding(
                    "Background process spawn",
                    lineno,
                    evidence=line,
                    rec="Avoid backgrounding processes in tool scripts.",
                    level=RiskLevel.MEDIUM,
                ))
            # Shell pipeline chains (3+ pipes) — resource abuse signal
            if line.count("|") >= 3:
                findings.append(self._finding(
                    f"Complex shell pipeline ({line.count('|')} stages)",
                    lineno,
                    evidence=line,
                    rec="Review long pipelines for resource abuse.",
                    level=RiskLevel.LOW,
                ))
            # Command substitution backticks / $() used with dynamic content
            if re.search(r"\$\([^)]*\$\{?[A-Za-z_][A-Za-z0-9_]*\}?[^)]*\)", line):
                findings.append(self._finding(
                    "Nested command substitution with variable expansion (injection risk)",
                    lineno,
                    evidence=line,
                    rec="Avoid nesting $() with variable expansion; sanitize inputs.",
                    level=RiskLevel.HIGH,
                ))
        return findings

    def _finding(self, msg, line, evidence, rec, level=None, extra=None) -> SafetyFinding:
        meta = {"message": msg}
        if extra:
            meta.update(extra)
        return SafetyFinding(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            risk_type=self.risk_type,
            risk_level=level or self.default_level,
            evidence=evidence_snippet(evidence),
            line=line,
            recommendation=rec,
            metadata=meta,
        )


def _has_shell_true(node: ast.Call) -> bool:
    """True when a subprocess call passes shell=True."""
    for kw in node.keywords:
        if kw.arg == "shell":
            val = kw.value
            if isinstance(val, ast.Constant) and val.value is True:
                return True
            if isinstance(val, ast.Name) and val.id == "True":
                return True
    return False
