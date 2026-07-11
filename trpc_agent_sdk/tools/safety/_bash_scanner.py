# Copyright (C) 2026 Tencent. All rights reserved.
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Bash script scanner built on the quote-aware shell parser."""
from __future__ import annotations

import re

from trpc_agent_sdk.tools.safety._policy import Policy
from trpc_agent_sdk.tools.safety._rules import R_FS_RECURSIVE_DELETE
from trpc_agent_sdk.tools.safety._rules import R_NET_HTTP
from trpc_agent_sdk.tools.safety._rules import R_PKG_INSTALL
from trpc_agent_sdk.tools.safety._rules import R_PROC_PRIVILEGE_ESCALATION
from trpc_agent_sdk.tools.safety._rules import R_PROC_SHELL_PIPE
from trpc_agent_sdk.tools.safety._rules import R_RES_FORK_BOMB
from trpc_agent_sdk.tools.safety._rules import R_RES_LARGE_WRITE
from trpc_agent_sdk.tools.safety._rules import R_RES_LONG_SLEEP
from trpc_agent_sdk.tools.safety._shell_parse import has_pipeline
from trpc_agent_sdk.tools.safety._shell_parse import has_shell_bypass
from trpc_agent_sdk.tools.safety._types import Finding

_DOMAIN_RE = re.compile(r"https?://([^/\s'\"]+)", re.IGNORECASE)
_SLEEP_RE = re.compile(r"\bsleep\s+(\d+)")
_FORK_BOMB_RE = re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;")
# dd/truncate producing GB+ files, or head -c with a very large byte count.
_LARGE_WRITE_RE = re.compile(r"\bdd\b[^;\n]*?bs=\s*\d+\s*[GT]|\btruncate\b[^;\n]*?-s\s+\d+\s*[GT]", re.IGNORECASE)
_HEAD_C_RE = re.compile(r"\bhead\b[^;\n]*?-c\s+(\d+)")
_HUGE_BYTES = 10_000_000


def scan_bash(policy: Policy, script: str) -> list[Finding]:
    """Return findings for a bash script."""
    findings: list[Finding] = []
    rule_meta = policy.rules
    max_ev = policy.max_evidence_chars

    def add(rule_id: str, evidence: str, rec: str) -> None:
        meta = rule_meta[rule_id]
        findings.append(Finding(
            rule_id=rule_id,
            risk_level=meta.risk_level,
            rule_decision=meta.decision,
            evidence=evidence[:max_ev],
            recommendation=rec,
            language="bash",
        ))

    joined = script

    # Recursive delete
    if re.search(r"\brm\b[^;\n]*-r[f]?", joined) and ("/" == _rm_target(joined) or
                                                      re.search(r"rm\s+-[rf]+\s+/", joined)):
        add(R_FS_RECURSIVE_DELETE, "rm -rf against root/system path",
            "Refuse recursive delete of system paths.")

    # Fork bomb
    if _FORK_BOMB_RE.search(joined):
        add(R_RES_FORK_BOMB, "fork bomb pattern", "Refuse fork bomb.")

    # Dependency install
    if re.search(r"\b(pip|pip3|npm|yarn|apt|apt-get|yum|brew)\s+install\b", joined):
        add(R_PKG_INSTALL, "dependency install command",
            "Installing deps changes the runtime environment; review.")

    # Privilege escalation
    if re.search(r"\b(sudo|su|doas)\b", joined):
        add(R_PROC_PRIVILEGE_ESCALATION, "privilege escalation command",
            "Privilege escalation requires review.")

    # Long sleep (>= policy.max_timeout_seconds)
    for m in _SLEEP_RE.finditer(joined):
        if int(m.group(1)) >= policy.max_timeout_seconds:
            add(R_RES_LONG_SLEEP, f"sleep {m.group(1)}",
                f"sleep >= {policy.max_timeout_seconds}s is suspicious.")
            break

    # Large write: dd/truncate with GB+ size, or head -c with a huge byte count.
    if _LARGE_WRITE_RE.search(joined):
        add(R_RES_LARGE_WRITE, "dd/truncate with GB+ size",
            "Very large file generation; possible disk exhaustion. Review.")
    for m in _HEAD_C_RE.finditer(joined):
        if int(m.group(1)) >= _HUGE_BYTES:
            add(R_RES_LARGE_WRITE, f"head -c {m.group(1)}",
                "Very large write; possible disk exhaustion. Review.")
            break

    # Shell pipe / bypass
    if has_shell_bypass(joined):
        add(R_PROC_SHELL_PIPE, "shell interpreter bypass (sh -c / $() / backtick)",
            "Handing strings to a fresh shell bypasses static checks.")
    elif has_pipeline(joined):
        # curl ... | sh is especially dangerous
        if re.search(r"\b(curl|wget)\b", joined) and re.search(r"\|\s*(sh|bash)\b", joined):
            add(R_PROC_SHELL_PIPE, "piping remote content into a shell",
                "Remote-to-shell pipe executes untrusted code.")
        else:
            add(R_PROC_SHELL_PIPE, "shell pipeline", "Pipeline chains commands; review.")

    # Network egress to non-whitelisted domains
    for m in _DOMAIN_RE.finditer(joined):
        host = m.group(1).lower()
        root_domain = ".".join(host.split(".")[-2:]) if len(host.split(".")) >= 2 else host
        if root_domain not in policy.whitelisted_domains and host not in policy.whitelisted_domains:
            add(R_NET_HTTP, f"network egress to {host}",
                f"{host} is not whitelisted; review or allowlist.")

    return findings


def _rm_target(joined: str) -> str:
    m = re.search(r"\brm\s+-\S*\s+(\S+)", joined)
    return m.group(1) if m else ""
