"""Pre-sandbox governance filters."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .models import DiffInput
from .models import FilterDecision
from .redaction import redact_text

FORBIDDEN_PATH_MARKERS = (
    ".env",
    ".ssh/",
    "id_rsa",
    "private_key",
    ".aws/",
    "/etc/",
    "secrets/",
)

HIGH_RISK_COMMAND_PATTERNS = (
    r"curl\s+[^|]+\|\s*(sh|bash)",
    r"wget\s+[^|]+\|\s*(sh|bash)",
    r"rm\s+-rf\s+/",
    r"rm\s+-rf\s+(\.|\*)",
    r"\bgit\s+clean\s+-[A-Za-z]*[fdx][A-Za-z]*",
    r"docker\s+run\s+.*--privileged",
    r"\bdd\s+.*\bof=",
    r"\bmkfs(?:\.[A-Za-z0-9_+-]+)?\b",
    r"\bsudo\b",
    r"^\s*(sh|bash|zsh|fish|dash|ksh)\b",
    r"[;&|`<>]",
    r"\$\(",
    r":\(\)\s*\{",
)


@dataclass(slots=True)
class SandboxRequest:
    name: str
    command: str
    script_path: str
    timeout_sec: float
    max_output_bytes: int
    env: dict[str, str]
    network_required: bool = False
    network_domains: tuple[str, ...] = ()
    read_allowlist: tuple[str, ...] = ("work/", "scripts/")
    write_allowlist: tuple[str, ...] = ("work/", )


class ReviewFilterPolicy:

    def __init__(
            self,
            *,
            network_policy: str = "deny",
            timeout_budget_sec: float = 30.0,
            max_output_bytes: int = 20000,
            allowed_network_domains: tuple[str, ...] = (),
            forbidden_path_markers: tuple[str, ...] = FORBIDDEN_PATH_MARKERS,
            high_risk_command_patterns: tuple[str, ...] = HIGH_RISK_COMMAND_PATTERNS,
            sandbox_path_allowlist: tuple[str, ...] = ("scripts/", "work/"),
            sandbox_read_allowlist: tuple[str, ...] = ("scripts/", "work/", "repo/"),
            sandbox_write_allowlist: tuple[str, ...] = ("work/", ),
            schema_version: int = 1,
    ):
        self.network_policy = network_policy
        self.timeout_budget_sec = timeout_budget_sec
        self.max_output_bytes = max_output_bytes
        self.allowed_network_domains = allowed_network_domains
        self.forbidden_path_markers = forbidden_path_markers
        self.high_risk_command_patterns = high_risk_command_patterns
        self.sandbox_path_allowlist = sandbox_path_allowlist
        self.sandbox_read_allowlist = sandbox_read_allowlist
        self.sandbox_write_allowlist = sandbox_write_allowlist
        self.schema_version = schema_version
        self.last_redaction_count = 0

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        network_policy: str | None = None,
        timeout_budget_sec: float | None = None,
        max_output_bytes: int | None = None,
    ) -> "ReviewFilterPolicy":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            network_policy=network_policy or data.get("network_policy", "deny"),
            timeout_budget_sec=(timeout_budget_sec if timeout_budget_sec is not None else float(
                data.get("timeout_budget_sec", 30.0))),
            max_output_bytes=(max_output_bytes if max_output_bytes is not None else int(
                data.get("max_output_bytes", 20000))),
            allowed_network_domains=tuple(data.get("allowed_network_domains", ())),
            forbidden_path_markers=tuple(data.get("forbidden_path_markers", FORBIDDEN_PATH_MARKERS)),
            high_risk_command_patterns=tuple(data.get("high_risk_command_patterns", HIGH_RISK_COMMAND_PATTERNS)),
            sandbox_path_allowlist=tuple(data.get("sandbox_path_allowlist", ("scripts/", "work/"))),
            sandbox_read_allowlist=tuple(data.get("sandbox_read_allowlist", ("scripts/", "work/", "repo/"))),
            sandbox_write_allowlist=tuple(data.get("sandbox_write_allowlist", ("work/", ))),
            schema_version=int(data.get("schema_version", 1)),
        )

    def audit(self) -> dict[str, object]:
        return {
            "network_policy": self.network_policy,
            "timeout_budget_sec": self.timeout_budget_sec,
            "max_output_bytes": self.max_output_bytes,
            "allowed_network_domains": list(self.allowed_network_domains),
            "schema_version": self.schema_version,
            "forbidden_path_markers": list(self.forbidden_path_markers),
            "high_risk_command_patterns": list(self.high_risk_command_patterns),
            "sandbox_path_allowlist": list(self.sandbox_path_allowlist),
            "sandbox_read_allowlist": list(self.sandbox_read_allowlist),
            "sandbox_write_allowlist": list(self.sandbox_write_allowlist),
        }

    def evaluate(
        self,
        diff: DiffInput,
        requests: list[SandboxRequest],
    ) -> tuple[list[SandboxRequest], list[FilterDecision]]:
        allowed: list[SandboxRequest] = []
        decisions: list[FilterDecision] = []
        self.last_redaction_count = 0

        for file_path in diff.files:
            if _is_forbidden_path(file_path, self.forbidden_path_markers):
                decisions.append(
                    self._decision(
                        decision="needs_human_review",
                        reason="Diff touches a sensitive path that should not be staged into an automated sandbox.",
                        path=file_path,
                        policy="forbidden-path",
                        severity="high",
                    ))

        if any(decision.decision == "needs_human_review" and decision.policy == "forbidden-path"
               for decision in decisions):
            for request in requests:
                decisions.append(
                    self._decision(
                        decision="needs_human_review",
                        reason="Sandbox request blocked because the diff touches a sensitive path.",
                        command=request.env.get("CR_TEST_COMMAND", request.command),
                        path=request.script_path,
                        policy="forbidden-path-sandbox-block",
                        severity="high",
                    ))
            return [], decisions

        for request in requests:
            command_decision = self._evaluate_request(request)
            decisions.append(command_decision)
            if command_decision.decision == "allow":
                allowed.append(request)

        return allowed, decisions

    def _evaluate_request(self, request: SandboxRequest) -> FilterDecision:
        if request.timeout_sec > self.timeout_budget_sec:
            return self._decision(
                decision="deny",
                reason=f"Requested timeout {request.timeout_sec}s exceeds budget {self.timeout_budget_sec}s.",
                command=request.command,
                path=request.script_path,
                policy="timeout-budget",
                severity="medium",
            )
        if request.max_output_bytes > self.max_output_bytes:
            return self._decision(
                decision="deny",
                reason=f"Requested output cap {request.max_output_bytes} exceeds budget {self.max_output_bytes}.",
                command=request.command,
                path=request.script_path,
                policy="output-budget",
                severity="medium",
            )
        if not _path_is_allowed(request.script_path, self.sandbox_path_allowlist):
            return self._decision(
                decision="deny",
                reason="Sandbox script path is outside the configured workspace allowlist.",
                command=request.command,
                path=request.script_path,
                policy="sandbox-path-allowlist",
                severity="high",
            )
        invalid_read_paths = _invalid_allowlist_entries(request.read_allowlist, self.sandbox_read_allowlist)
        if invalid_read_paths:
            return self._decision(
                decision="deny",
                reason="Sandbox request declares read paths outside the configured workspace allowlist: "
                f"{', '.join(invalid_read_paths)}.",
                command=request.command,
                path=request.script_path,
                policy="sandbox-read-allowlist",
                severity="high",
            )
        invalid_write_paths = _invalid_allowlist_entries(request.write_allowlist, self.sandbox_write_allowlist)
        if invalid_write_paths:
            return self._decision(
                decision="deny",
                reason="Sandbox request declares write paths outside the configured workspace allowlist: "
                f"{', '.join(invalid_write_paths)}.",
                command=request.command,
                path=request.script_path,
                policy="sandbox-write-allowlist",
                severity="high",
            )
        if request.network_required:
            if self.network_policy != "allowlist":
                return self._decision(
                    decision="needs_human_review",
                    reason="Network access is not allowed by the active review policy.",
                    command=request.command,
                    path=request.script_path,
                    policy="network-policy",
                    severity="high",
                )
            disallowed = sorted(set(request.network_domains) - set(self.allowed_network_domains))
            if disallowed:
                return self._decision(
                    decision="needs_human_review",
                    reason=f"Network domains are outside the configured allowlist: {', '.join(disallowed)}.",
                    command=request.command,
                    path=request.script_path,
                    policy="network-allowlist",
                    severity="high",
                )
        command_to_check = request.env.get("CR_TEST_COMMAND", request.command)
        if _is_high_risk_command(command_to_check, self.high_risk_command_patterns):
            return self._decision(
                decision="deny",
                reason="Command contains a high-risk shell pattern and was not sent to the sandbox.",
                command=command_to_check,
                path=request.script_path,
                policy="high-risk-command",
                severity="high",
            )
        return self._decision(
            decision="allow",
            reason=(
                "Sandbox request passed path, read/write allowlist, command, network, timeout, "
                "and output-budget checks."
            ),
            command=request.command,
            path=request.script_path,
            policy="sandbox-preflight",
            severity="info",
        )

    def _decision(
        self,
        *,
        decision: str,
        reason: str,
        command: str = "",
        path: str = "",
        policy: str = "",
        severity: str = "info",
    ) -> FilterDecision:
        reason_redacted = redact_text(reason)
        command_redacted = redact_text(command)
        path_redacted = redact_text(path)
        self.last_redaction_count += reason_redacted.count + command_redacted.count + path_redacted.count
        return FilterDecision(
            decision=decision,
            reason=reason_redacted.text,
            command=command_redacted.text,
            path=path_redacted.text,
            policy=policy,
            severity=severity,
        )


def _is_forbidden_path(path: str, markers: tuple[str, ...] = FORBIDDEN_PATH_MARKERS) -> bool:
    lowered = path.lower()
    return any(marker in lowered for marker in markers)


def _is_high_risk_command(command: str, patterns: tuple[str, ...] = HIGH_RISK_COMMAND_PATTERNS) -> bool:
    return any(re.search(pattern, command) for pattern in patterns)


def _path_is_allowed(path: str, allowlist: tuple[str, ...]) -> bool:
    normalized = path.replace("\\", "/").lstrip("/")
    return any(normalized == prefix.rstrip("/") or normalized.startswith(prefix) for prefix in allowlist)


def _invalid_allowlist_entries(entries: tuple[str, ...], policy_allowlist: tuple[str, ...]) -> list[str]:
    invalid: list[str] = []
    for entry in entries:
        normalized = entry.replace("\\", "/").lstrip("/")
        if not normalized or normalized.startswith("../") or ".." in Path(normalized).parts:
            invalid.append(entry)
            continue
        if not _path_is_allowed(normalized, policy_allowlist):
            invalid.append(entry)
    return invalid
