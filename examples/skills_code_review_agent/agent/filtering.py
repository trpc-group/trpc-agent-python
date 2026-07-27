"""Filter governance for sandbox execution."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from .models import FilterDecision, SandboxRequest

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

_ALLOWED_WORKSPACE_SCHEMES = {"artifact", "skill", "workspace"}
_URI_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.-]*)://", re.IGNORECASE)
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[a-z]:/", re.IGNORECASE)

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
        max_output_files: int = 32,
    ) -> None:
        self.max_timeout_seconds = max_timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.max_output_files = max_output_files

    @staticmethod
    def scan_targets(request: SandboxRequest) -> list[str]:
        """Every string the policy must inspect for one request.

        The display command is caller-supplied prose. Judging a request on it
        alone lets a benign label carry a hostile argv, so the real argv -- both
        as individual tokens and joined, to catch operators split across
        elements -- is always scanned as well.
        """
        targets = [*request.command, " ".join(request.command)]
        if request.display_command:
            targets.append(request.display_command)
        return [target for target in targets if target]

    def evaluate_request(self, request: SandboxRequest) -> FilterDecision:
        """Decide whether a sandbox request may run."""
        targets = self.scan_targets(request)
        command = request.display_command or " ".join(request.command)
        if request.timeout_seconds > self.max_timeout_seconds:
            return FilterDecision(
                action="deny",
                rule_id="budget.timeout",
                reason=f"timeout {request.timeout_seconds}s exceeds budget {self.max_timeout_seconds}s",
                command=command,
            )
        if request.max_output_bytes < 0 or request.max_output_bytes > self.max_output_bytes:
            return FilterDecision(
                action="deny",
                rule_id="budget.output",
                reason=(
                    f"output limit {request.max_output_bytes} is outside budget "
                    f"0..{self.max_output_bytes}"
                ),
                command=command,
            )
        if len(request.output_files) > self.max_output_files:
            return FilterDecision(
                action="deny",
                rule_id="budget.output_files",
                reason=(
                    f"output file count {len(request.output_files)} exceeds budget "
                    f"{self.max_output_files}"
                ),
                command=command,
            )
        risky = next((target for target in targets if HIGH_RISK_COMMAND_RE.search(target)), "")
        if risky:
            return FilterDecision(
                action="needs_human_review",
                rule_id="script.high_risk_command",
                reason="command contains network, package installation, privilege or destructive operations"
                       f" (matched: {risky[:200]})",
                command=command,
            )
        networked = next((target for target in targets if self._looks_like_network_command(target)), "")
        if not request.allow_network and networked:
            return FilterDecision(
                action="deny",
                rule_id="network.not_whitelisted",
                reason=f"network access is disabled for this review sandbox run (matched: {networked[:200]})",
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
        raw = str(path).strip().replace("\\", "/")
        normalized = str(PurePosixPath(raw))
        lowered = normalized.lower()
        for pattern in BLOCKED_PATH_PATTERNS:
            if pattern in lowered:
                return FilterDecision(
                    action="deny",
                    rule_id="path.blocked",
                    reason=f"path matches blocked pattern: {pattern}",
                    path=normalized,
                )
        scheme_match = _URI_SCHEME_RE.match(raw)
        if scheme_match:
            scheme = scheme_match.group(1).lower()
            if scheme == "host":
                return FilterDecision(
                    action="deny",
                    rule_id="path.host_access",
                    reason="model-driven tools cannot stage arbitrary host files",
                    path=raw,
                )
            if scheme not in _ALLOWED_WORKSPACE_SCHEMES:
                return FilterDecision(
                    action="deny",
                    rule_id="path.scheme",
                    reason=f"path scheme is not allowlisted: {scheme}",
                    path=raw,
                )
        elif raw.startswith(("/", "//", "~/")) or _WINDOWS_ABSOLUTE_RE.match(raw):
            return FilterDecision(
                action="deny",
                rule_id="path.absolute",
                reason="host absolute paths are not allowed in the review workspace",
                path=raw,
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
