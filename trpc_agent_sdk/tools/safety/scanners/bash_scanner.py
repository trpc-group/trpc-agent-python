# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Bash / shell payload scanner.

Uses :mod:`shlex` to split a command line into pipeline segments and inspect the
base command of each, then layers the shared regex text scan on top (which is
where rm -rf, curl|bash, installs, sudo, chmod and ssh/env reads are caught).
"""

from __future__ import annotations

import os
import re
import shlex

from ..models import RiskFinding
from ..models import ScanInput
from ..policy import SafetyPolicy
from ..rules import make_finding
from .base import ScannerABC
from .base import dedupe_findings
from .patterns import iter_forbidden_path_findings
from .patterns import iter_text_findings

# Pipeline / list separators.
_SEPARATORS = {"|", "||", "&&", ";", "&", "|&"}
# Wrapper commands whose argument is the real command to inspect.
_WRAPPERS = {"sudo", "time", "nohup", "nice", "env", "command", "exec", "xargs", "watch"}
# Commands that are already reported by dedicated text rules; do not double-flag
# them as "non-allow-listed".
_SELF_FLAGGED = {
    "rm", "curl", "wget", "sudo", "chmod", "pip", "pip3", "npm", "yarn", "pnpm",
    "apt", "apt-get", "yum", "dnf", "brew", "apk", "pacman", "sh", "bash", "zsh",
    "dash", "nc", "ncat", "telnet",
}
_RE_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _base_commands(command: str) -> list[str]:
    """Return the base command of every pipeline segment (best-effort)."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        tokens = re.split(r"[|&;]+|\s+", command)

    bases: list[str] = []
    expect_command = True
    for tok in tokens:
        if tok in _SEPARATORS:
            expect_command = True
            continue
        if not expect_command:
            continue
        if _RE_ASSIGNMENT.match(tok):  # FOO=bar prefix -> keep looking
            continue
        base = os.path.basename(tok)
        if base in _WRAPPERS:  # unwrap sudo/env/... -> next token is the command
            continue
        bases.append(base)
        expect_command = False
    return bases


class BashScanner(ScannerABC):
    """shlex-based scanner for shell payloads."""

    language = "bash"

    def scan(self, scan_input: ScanInput, policy: SafetyPolicy) -> list[RiskFinding]:
        command = scan_input.script or ""
        findings: list[RiskFinding] = []

        # 1. Shared regex text scan (rm -rf, curl|bash, installs, sudo, secrets, urls).
        for rule_id, snippet, lineno in iter_text_findings(command, policy):
            findings.append(make_finding(rule_id, snippet, lineno))

        # 1b. Policy-driven forbidden-path access (config-driven, no code change).
        for rule_id, snippet, lineno in iter_forbidden_path_findings(command, policy):
            findings.append(make_finding(rule_id, snippet, lineno))

        # 2. Base-command allow-list check (fail-safe: unknown command -> review).
        allowed = {c.strip().lower() for c in policy.allowed_commands}
        first_line = command.strip().splitlines()[0].strip() if command.strip() else command
        for base in _base_commands(command):
            low = base.lower()
            if not low or low in allowed or low in _SELF_FLAGGED:
                continue
            findings.append(make_finding("EXEC_NON_ALLOWLIST_COMMAND", first_line, 1))

        return dedupe_findings(findings)
