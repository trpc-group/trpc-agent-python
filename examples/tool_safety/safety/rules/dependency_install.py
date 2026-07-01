# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""Rule: dependency installation that mutates the runtime environment.

Flags ``pip install``, ``npm install``, ``yarn add``, ``apt install`` and
similar package-manager commands that change the execution environment.
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


# Bash package manager invocations.
_INSTALL_REGEXES = [
    re.compile(r"\bpip3?\s+install\b", re.IGNORECASE),
    re.compile(r"\bpython\s+-m\s+pip\s+install\b", re.IGNORECASE),
    re.compile(r"\bnpm\s+install\b", re.IGNORECASE),
    re.compile(r"\bnpm\s+ci\b", re.IGNORECASE),
    re.compile(r"\bnpx\s+install\b", re.IGNORECASE),
    re.compile(r"\byarn\s+add\b", re.IGNORECASE),
    re.compile(r"\bapt(?:-get)?\s+install\b", re.IGNORECASE),
    re.compile(r"\baptitude\s+install\b", re.IGNORECASE),
    re.compile(r"\bdnf\s+install\b", re.IGNORECASE),
    re.compile(r"\byum\s+install\b", re.IGNORECASE),
    re.compile(r"\bbrew\s+install\b", re.IGNORECASE),
    re.compile(r"\bconda\s+install\b", re.IGNORECASE),
    re.compile(r"\bpoetry\s+add\b", re.IGNORECASE),
    re.compile(r"\buv\s+pip\s+install\b", re.IGNORECASE),
    re.compile(r"\bgo\s+get\b", re.IGNORECASE),
    re.compile(r"\bcargo\s+add\b", re.IGNORECASE),
    re.compile(r"\bgem\s+install\b", re.IGNORECASE),
    re.compile(r"\bcomposer\s+require\b", re.IGNORECASE),
]

# Python: os.system("pip install ...") is caught by process rule + this rule's
# substring check; we additionally flag importlib / pip programmatic installs.
_PY_INSTALL_CALLS = {
    "importlib.metadata.distribution",  # not install, ignore
    "pip.main",
    "subprocess.run",  # already covered by process rule, but we add context
}


class DependencyInstallRule(SafetyRule):
    """Detect package installation commands that mutate the environment."""

    rule_id = "R004_dependency_install"
    rule_name = "Dependency Installation"
    risk_type = "dependency_install"
    default_level = RiskLevel.HIGH
    languages = ("python", "bash")

    def check(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        lang = normalize_language(scan_input)
        findings: list[SafetyFinding] = []
        if lang == "python":
            findings.extend(self._check_python(scan_input, policy))
        # Bash check runs for both languages when the script contains shell-ish
        # install commands (a python script may still embed them in strings).
        findings.extend(self._check_shell_substrings(scan_input, policy))
        return findings

    def _check_python(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        tree = parse_python_ast(scan_input.script)
        if tree is None:
            return findings
        for node, name in iter_python_calls(tree):
            lname = name.lower()
            if lname == "pip.main":
                findings.append(self._finding(
                    "Programmatic pip install via pip.main()",
                    node.lineno,
                    evidence=f"{name}(...)",
                    rec="Do not install packages at runtime; declare dependencies up front.",
                ))
        return findings

    def _check_shell_substrings(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        for lineno, line in bash_lines(scan_input.script):
            for pat in _INSTALL_REGEXES:
                if pat.search(line):
                    findings.append(self._finding(
                        f"Dependency install: {evidence_snippet(line)}",
                        lineno,
                        evidence=line,
                        rec="Pin dependencies in a lockfile instead of installing at runtime.",
                    ))
                    break
        # Also scan string literals in python source for embedded install cmds.
        if "python" in (scan_input.language or "") or normalize_language(scan_input) == "python":
            tree = parse_python_ast(scan_input.script)
            if tree is not None:
                for node in ast.walk(tree):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str):
                        for pat in _INSTALL_REGEXES:
                            if pat.search(node.value):
                                findings.append(self._finding(
                                    "Embedded dependency install in string literal",
                                    getattr(node, "lineno", None),
                                    evidence=node.value,
                                    rec="Do not embed install commands in string literals.",
                                ))
                                break
        return findings

    def _finding(self, msg, line, evidence, rec) -> SafetyFinding:
        return SafetyFinding(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            risk_type=self.risk_type,
            risk_level=self.default_level,
            evidence=evidence_snippet(evidence),
            line=line,
            recommendation=rec,
            metadata={"message": msg},
        )
