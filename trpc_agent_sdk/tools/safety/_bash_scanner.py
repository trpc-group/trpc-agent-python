# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Syntax-aware (L2) scanner for Bash / shell commands.

Rather than only matching raw text, this layer tokenises each command with
``shlex`` and inspects *structure*: pipelines, command substitution, base64
decoding, redirections and the actual command names being run. This catches
constructs that slip past line-oriented regexes, e.g. ``$(printf ...)`` chains
or ``base64 -d | bash``.

Findings carry ``SH*`` rule ids and ``layer="ast"``.
"""

from __future__ import annotations

import re
import shlex
from typing import Optional

from ._policy import SafetyPolicy
from ._types import RiskCategory
from ._types import RiskLevel
from ._types import RuleHit

_PIPE_TO_INTERP_RE = re.compile(r"\|\s*(sudo\s+)?(bash|sh|zsh|python3?)\b", re.IGNORECASE)
_URL_RE = re.compile(r"https?://([^/\s'\"]+)", re.IGNORECASE)
_HOST_ARG_RE = re.compile(r"\b([a-z0-9.-]+\.[a-z]{2,})\b", re.IGNORECASE)


class _BashSafetyAnalyzer:
    """Structural analyser for a single shell script."""

    def __init__(self, policy: SafetyPolicy) -> None:
        self._policy = policy
        self.hits: list[RuleHit] = []

    def analyze(self, script: str) -> list[RuleHit]:
        """Analyse every logical line of the script.

        Known limitation: analysis is line-oriented (``splitlines``) and does not
        reassemble backslash continuations or ``heredoc`` bodies. A command split
        across physical lines -- e.g. ``curl \\`` then the URL on the next line --
        can therefore evade per-line domain extraction. This is an accepted
        static-analysis blind spot; defence in depth (the runtime sandbox and the
        allow-listed egress policy) is expected to backstop it.
        """
        for lineno, raw_line in enumerate(script.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            self._analyze_line(line, lineno)
        return self.hits

    def _analyze_line(self, line: str, lineno: int) -> None:
        tokens = self._safe_split(line)
        pipes_to_interp = bool(_PIPE_TO_INTERP_RE.search(line))

        # Command substitution feeding an interpreter: $(...) | bash / | sh
        if ("$(" in line or "`" in line) and pipes_to_interp:
            self._add("SH010", RiskCategory.PROCESS_SYSTEM_COMMAND, RiskLevel.HIGH,
                      "Command substitution piped into an interpreter", line, lineno,
                      "Executing the output of a substitution is a common injection vector.")

        # base64 -d | sh — decode-then-execute
        if "base64" in tokens and pipes_to_interp:
            self._add("SH011", RiskCategory.PROCESS_SYSTEM_COMMAND, RiskLevel.HIGH,
                      "base64-decoded payload piped into an interpreter", line, lineno,
                      "Decoding then executing content hides the real command; forbidden.")

        # Download piped straight into a shell: curl ... | bash
        if self._has_downloader(tokens) and pipes_to_interp:
            self._add("SH012", RiskCategory.PROCESS_SYSTEM_COMMAND, RiskLevel.HIGH,
                      "Remote content piped into an interpreter", line, lineno,
                      "curl|bash executes untrusted remote code; fetch, review, then run.")

        # Network egress with domain awareness.
        self._check_network(line, tokens, lineno)

        # Forbidden path access anywhere on the line.
        self._check_forbidden_path(line, lineno)

    def _check_network(self, line: str, tokens: list[str], lineno: int) -> None:
        """Flag network egress, downgrading whitelisted destinations."""
        if not self._has_downloader(tokens):
            return
        domains = self._extract_domains(line)
        if not domains:
            # Downloader present but destination not statically known.
            self._add("SH020", RiskCategory.NETWORK_EXFILTRATION, RiskLevel.MEDIUM,
                      "Network egress to an undetermined destination", line, lineno,
                      "Destination could not be verified against the allow-list; review.")
            return
        for domain in domains:
            if not self._policy.domain_allowed(domain):
                self._add("SH021", RiskCategory.NETWORK_EXFILTRATION, RiskLevel.HIGH,
                          f"Network egress to non-whitelisted domain: {domain}", line, lineno,
                          "Destination is not on the allowed_domains list; blocked.")

    def _check_forbidden_path(self, line: str, lineno: int) -> None:
        lowered = line.lower()
        matched = [f for f in self._policy.forbidden_paths if f.lower() in lowered]
        if not matched:
            return
        # A single line can reference several forbidden paths (e.g.
        # ``cat ~/.ssh/id_rsa`` hits both ``~/.ssh`` and ``id_rsa``). Emit one
        # SH030 hit that names every match: distinct hits would share the same
        # ``(rule_id, line)`` key and be collapsed by the scanner's dedupe, so
        # listing them here is what keeps the audit trail complete.
        joined = ", ".join(f"'{f}'" for f in matched)
        self._add("SH030", RiskCategory.SENSITIVE_INFO_LEAK, RiskLevel.CRITICAL, "Access to a forbidden/sensitive path",
                  line, lineno, f"Line references forbidden path(s): {joined}.")

    # -- token helpers ---------------------------------------------------------
    @staticmethod
    def _safe_split(line: str) -> list[str]:
        """Tokenise a line, tolerating unbalanced quotes."""
        try:
            return shlex.split(line, posix=True)
        except ValueError:
            return line.split()

    @staticmethod
    def _has_downloader(tokens: list[str]) -> bool:
        bases = {tok.split("/")[-1] for tok in tokens}
        return bool(bases & {"curl", "wget"})

    def _extract_domains(self, line: str) -> list[str]:
        """Extract candidate domains from URLs and host-like arguments."""
        domains = [m.group(1).split("@")[-1].split(":")[0] for m in _URL_RE.finditer(line)]
        if domains:
            return domains
        # No scheme: look for host-like bare arguments (e.g. wget example.com/x).
        return [m.group(1) for m in _HOST_ARG_RE.finditer(line)]

    def _add(self, rule_id: str, category: RiskCategory, level: RiskLevel, title: str, evidence: str,
             line: Optional[int], recommendation: str) -> None:
        self.hits.append(
            RuleHit(
                rule_id=rule_id,
                category=category,
                risk_level=level,
                title=title,
                evidence=evidence,
                line=line,
                recommendation=recommendation,
                layer="ast",
            ))


def scan_bash(script: str, policy: SafetyPolicy) -> list[RuleHit]:
    """Run the structural shell layer over a Bash script.

    Args:
        script: The shell script / command line to analyse.
        policy: Active safety policy (allow-lists, forbidden paths).

    Returns:
        A list of :class:`RuleHit` produced by structural analysis.
    """
    return _BashSafetyAnalyzer(policy).analyze(script)
