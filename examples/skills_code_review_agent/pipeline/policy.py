# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Review policy — the Filter decision logic (issue #92, requirement 7 & 8).

Shared by two enforcement sites (plan decision #5): the framework ``ReviewGuardFilter`` on the agent
path, and a direct gate in the sandbox runner on the deterministic path. Both call ``evaluate`` and
must refuse to execute anything that comes back ``deny`` or ``needs_human_review``.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Iterable, Literal

Decision = Literal["allow", "deny", "needs_human_review"]

# High-risk command patterns → hard deny.
_DANGEROUS = [
    (re.compile(r"\brm\s+-[rfRF]"), "recursive/force delete"),
    (re.compile(r"\b(mkfs|shred)\b|\bdd\s+if="), "disk-destructive command"),
    (re.compile(r":\s*\(\s*\)\s*\{.*\}\s*;"), "fork bomb"),
    (re.compile(r"(curl|wget)\b[^\n|]*\|\s*(sh|bash)"), "pipe-to-shell"),
    (re.compile(r"\bchmod\s+-?R?\s*777\b"), "world-writable chmod"),
    (re.compile(r"\bsudo\b"), "privilege escalation"),
    (re.compile(r">\s*/dev/sd|/etc/passwd|/etc/shadow"), "write to sensitive target"),
]

# Sensitive roots a review must never touch (temp dirs under /var/folders are intentionally allowed).
_FORBIDDEN_PATHS = ("/etc", "/root", "/boot", os.path.expanduser("~/.ssh"))

# Only these env vars are passed into the sandbox — parent-process secrets never leak in (要求7).
ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT")


def sandbox_env(allowlist: Iterable[str] = ENV_ALLOWLIST) -> dict[str, str]:
    """Return the minimal whitelisted environment for a sandbox run."""
    return {k: os.environ[k] for k in allowlist if k in os.environ}


@dataclass
class PolicyDecision:
    decision: Decision
    reason: str = ""
    category: str = "ok"  # script | path | network | budget | ok

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    def as_block(self, *, script: str = "") -> dict:
        return {"script": script, "decision": self.decision, "reason": self.reason, "category": self.category}


class ReviewPolicy:
    """Decides whether a sandbox action may run. Deny/needs_human_review must NOT reach the sandbox."""

    def __init__(self, network_allowlist: Iterable[str] | None = None, max_budget_sec: float = 120.0) -> None:
        self.network_allowlist = set(network_allowlist or ())
        self.max_budget_sec = max_budget_sec

    def evaluate(
            self,
            *,
            command: str = "",
            touched_paths: Iterable[str] = (),
            network_hosts: Iterable[str] = (),
            budget_sec: float = 0.0,
    ) -> PolicyDecision:
        for pat, why in _DANGEROUS:
            if pat.search(command):
                return PolicyDecision("deny", f"high-risk command: {why}", "script")

        for p in touched_paths:
            ap = os.path.abspath(p)
            if any(ap == fp or ap.startswith(fp + os.sep) for fp in _FORBIDDEN_PATHS):
                return PolicyDecision("deny", f"forbidden path: {p}", "path")

        unlisted = [h for h in network_hosts if h not in self.network_allowlist]
        if unlisted:
            return PolicyDecision("needs_human_review", f"non-whitelisted network: {unlisted}", "network")

        if budget_sec and budget_sec > self.max_budget_sec:
            return PolicyDecision("needs_human_review", f"over budget: {budget_sec}s > {self.max_budget_sec}s",
                                  "budget")

        return PolicyDecision("allow")
