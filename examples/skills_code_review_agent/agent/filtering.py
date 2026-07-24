"""Filter governance for sandbox execution."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from .models import FilterDecision
from .models import SandboxRequest


BLOCKED_PATH_PATTERNS = (
    ".env",
    ".pem",
    ".p12",
    ".pfx",
    "id_rsa",
    "id_dsa",
    ".ssh/",
    "/etc/",
    "node_modules/",
    ".git/",
)

HIGH_RISK_COMMAND_RE = re.compile(
    r"(?i)(\brm\s+-rf\b|\bcurl\b|\bwget\b|\bnc\b|\bnetcat\b|\bssh\b|\bscp\b|"
    r"\bsudo\b|\bchmod\s+777\b|\bpip\s+install\b|\bnpm\s+install\b|\bpnpm\s+install\b|"
    r"\byarn\s+add\b|\bdocker\s+run\b|\bmkfs\b|\bdd\s+if=)"
)


class ReviewExecutionFilter:
    """Preflight policy for sandbox commands and changed paths."""

    def __init__(
        self,
        *,
        max_timeout_seconds: float = 30.0,
        max_output_bytes: int = 262144,
        allow_network_hosts: set[str] | None = None,
    ) -> None:
        self.max_timeout_seconds = max_timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.allow_network_hosts = allow_network_hosts or set()

    def evaluate_request(self, request: SandboxRequest) -> FilterDecision:
        """Decide whether a sandbox request may run."""
        command = request.display_command or " ".join(request.command)
        if request.timeout_seconds > self.max_timeout_seconds:
            return FilterDecision(
                action="deny",
                rule_id="budget.timeout",
                reason=f"timeout {request.timeout_seconds}s exceeds budget {self.max_timeout_seconds}s",
                command=command,
            )
        if request.max_output_bytes > self.max_output_bytes:
            return FilterDecision(
                action="deny",
                rule_id="budget.output",
                reason=f"output limit {request.max_output_bytes} exceeds budget {self.max_output_bytes}",
                command=command,
            )
        if HIGH_RISK_COMMAND_RE.search(command):
            return FilterDecision(
                action="needs_human_review",
                rule_id="script.high_risk_command",
                reason="command contains network, package installation, privilege or destructive operations",
                command=command,
            )
        if not request.allow_network and self._looks_like_network_command(command):
            return FilterDecision(
                action="deny",
                rule_id="network.not_whitelisted",
                reason="network access is disabled for this review sandbox run",
                command=command,
            )
        for path in list(request.input_files) + request.output_files:
            path_decision = self.evaluate_path(path)
            if not path_decision.allowed:
                path_decision.command = command
                return path_decision
        return FilterDecision(action="allow", rule_id="allow", reason="request passed filter", command=command)

    def evaluate_path(self, path: str) -> FilterDecision:
        """Deny paths that would expose host secrets or unrelated trees."""
        normalized = str(PurePosixPath(path.replace("\\", "/")))
        lowered = normalized.lower()
        for pattern in BLOCKED_PATH_PATTERNS:
            if pattern in lowered:
                return FilterDecision(
                    action="deny",
                    rule_id="path.blocked",
                    reason=f"path matches blocked pattern: {pattern}",
                    path=normalized,
                )
        if normalized.startswith("../") or "/../" in normalized:
            return FilterDecision(
                action="deny",
                rule_id="path.traversal",
                reason="path attempts to escape the review workspace",
                path=normalized,
            )
        return FilterDecision(action="allow", rule_id="allow", reason="path passed filter", path=normalized)

    @staticmethod
    def _looks_like_network_command(command: str) -> bool:
        lowered = command.lower()
        return "http://" in lowered or "https://" in lowered or "git clone" in lowered

