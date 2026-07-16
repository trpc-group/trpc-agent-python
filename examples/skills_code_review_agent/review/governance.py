# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Governance policy: script allowlist, forbidden paths, network policy,
risk heuristics and execution budget."""
import posixpath
from dataclasses import dataclass, field

DEFAULT_ALLOWED_SCRIPTS = (
    "parse_diff.py",
    "check_security.py",
    "check_async_leak.py",
    "check_db_lifecycle.py",
    "check_tests_missing.py",
    "check_secrets.py",
)

NETWORK_TOOLS = frozenset({"curl", "wget", "pip", "pip3", "ssh", "scp", "nc", "git", "apt", "apt-get"})
RISK_TOKENS = frozenset({"sudo", "docker", "chmod", "chown", "mount", "rm", "mkfs"})
PYTHON_EXECUTABLES = frozenset({"python", "python3"})


@dataclass
class GovernanceDecision:
    """Outcome of one governance check."""

    target: str
    decision: str  # allow | deny | needs_human_review
    rule: str = ""
    reason: str = ""


@dataclass
class GovernanceEngine:
    """Stateful policy engine; deny/needs_human_review must never reach the sandbox."""

    allowed_scripts: tuple = DEFAULT_ALLOWED_SCRIPTS
    max_runs: int = 20
    max_sandbox_seconds: float = 300.0
    _runs: int = field(default=0, init=False)
    _elapsed: float = field(default=0.0, init=False)

    def record_run(self, duration_s: float) -> None:
        self._runs += 1
        self._elapsed += max(0.0, duration_s)

    def _check_budget(self, target: str):
        if self._runs >= self.max_runs or self._elapsed >= self.max_sandbox_seconds:
            return GovernanceDecision(target, "deny", "budget_exceeded",
                                      f"budget: {self._runs} runs / {self._elapsed:.1f}s used")
        return None

    @staticmethod
    def _check_paths(target: str, argv):
        for arg in argv:
            if arg.startswith("/") or arg.startswith("~") or ".." in arg:
                return GovernanceDecision(target, "deny", "forbidden_path",
                                          f"path escapes workspace: {arg!r}")
        return None

    def check_script(self, script: str, argv) -> GovernanceDecision:
        target = f"{script} {' '.join(argv)}".strip()
        blocked = self._check_budget(target) or self._check_paths(target, argv)
        if blocked:
            return blocked
        if posixpath.basename(script) not in self.allowed_scripts:
            return GovernanceDecision(target, "deny", "script_allowlist",
                                      f"script {script!r} is not allowlisted")
        return GovernanceDecision(target, "allow")

    def check_command(self, command: str) -> GovernanceDecision:
        tokens = command.split()
        if not tokens:
            return GovernanceDecision(command, "deny", "empty_command", "empty command")
        head = posixpath.basename(tokens[0])
        if head in NETWORK_TOOLS or any(posixpath.basename(t) in NETWORK_TOOLS for t in tokens):
            return GovernanceDecision(command, "deny", "network_policy",
                                      "non-whitelisted network access")
        if head in RISK_TOKENS or any(posixpath.basename(t) in RISK_TOKENS for t in tokens):
            return GovernanceDecision(command, "needs_human_review", "risk_command",
                                      "high-risk command requires human review")
        if head in PYTHON_EXECUTABLES and len(tokens) >= 2:
            return self.check_script(tokens[1], tokens[2:])
        return GovernanceDecision(command, "needs_human_review", "unknown_command",
                                  f"executable {head!r} is not recognized")
