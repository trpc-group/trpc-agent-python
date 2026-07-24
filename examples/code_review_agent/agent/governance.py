# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Pre-sandbox governance for the code review dry-run example."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from .filters import redact_text
from .schemas import FilterDecision
from .schemas import SandboxPolicy

_ALLOWED_SCRIPTS = {"static_rules", "diff_summary", "sandbox_failure_probe", "timeout_probe"}
_RISKY_TOKENS = (
    "rm ",
    "sudo",
    "curl",
    "wget",
    "ssh",
    " nc ",
    "chmod",
    "chown",
    "pip install",
    "npm install",
    ";",
    "&&",
    "||",
    "> /",
)
_FORBIDDEN_PATH_PARTS = {".env", ".ssh", "id_rsa", "id_dsa", "credentials", "secrets"}


@dataclass(frozen=True)
class SandboxRequest:
    """A script request that must pass governance before sandbox execution."""

    script_name: str
    command: tuple[str, ...]
    input_paths: tuple[str, ...] = ()
    requires_network: bool = False
    estimated_output_bytes: int = 0


def build_default_sandbox_requests(changed_files: list[str]) -> list[SandboxRequest]:
    """Build deterministic sandbox requests for the review pipeline."""
    requests = [
        SandboxRequest(script_name="diff_summary", command=("python", "scripts/diff_summary.py"), input_paths=tuple(changed_files)),
        SandboxRequest(script_name="static_rules", command=("python", "scripts/static_rules.py"), input_paths=tuple(changed_files)),
    ]
    if any("sandbox_failure" in path for path in changed_files):
        requests.append(
            SandboxRequest(
                script_name="sandbox_failure_probe",
                command=("python", "scripts/sandbox_failure_probe.py"),
                input_paths=tuple(changed_files),
            )
        )
    if any("timeout" in path for path in changed_files):
        requests.append(
            SandboxRequest(script_name="timeout_probe", command=("python", "scripts/timeout_probe.py"), input_paths=tuple(changed_files))
        )
    return requests


def evaluate_sandbox_requests(
    requests: list[SandboxRequest], policy: SandboxPolicy, *, max_scripts: int = 8
) -> tuple[list[SandboxRequest], list[FilterDecision]]:
    """Return allowed requests and governance decisions."""
    allowed: list[SandboxRequest] = []
    decisions: list[FilterDecision] = []

    if len(requests) > max_scripts:
        return [], [
            FilterDecision(
                filter_name="sandbox_budget",
                decision="deny",
                reason=f"Sandbox request count {len(requests)} exceeds budget {max_scripts}.",
                stage="pre_sandbox",
            )
        ]

    for request in requests:
        decision = _evaluate_request(request, policy)
        decisions.append(decision)
        if decision.decision == "allow":
            allowed.append(request)
    return allowed, decisions


def _evaluate_request(request: SandboxRequest, policy: SandboxPolicy) -> FilterDecision:
    if request.script_name not in _ALLOWED_SCRIPTS:
        return _decision(request, "deny", f"Script {request.script_name} is not in the sandbox allowlist.")

    command_text = " ".join(request.command)
    lowered = f" {command_text.lower()} "
    for token in _RISKY_TOKENS:
        if token in lowered:
            return _decision(request, "deny", f"Command contains high-risk token `{redact_text(token.strip())}`.")

    if request.requires_network and not policy.network_allowed:
        return _decision(request, "deny", "Network access is not allowlisted for sandbox execution.")

    for path in request.input_paths:
        if _is_forbidden_path(path):
            return _decision(request, "deny", f"Input path `{redact_text(path)}` is forbidden for sandbox execution.", path=path)

    if request.estimated_output_bytes > policy.max_output_bytes:
        return _decision(request, "needs_human_review", "Estimated sandbox output exceeds configured output budget.")

    return _decision(request, "allow", "Sandbox request passed pre-execution governance.")


def _decision(request: SandboxRequest, decision: str, reason: str, *, path: str | None = None) -> FilterDecision:
    return FilterDecision(
        filter_name="sandbox_governance",
        decision=decision,
        reason=redact_text(reason),
        stage="pre_sandbox",
        script_name=request.script_name,
        path=path,
    )


def _is_forbidden_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if ".." in PurePosixPath(normalized).parts or normalized.startswith("/etc/") or normalized.startswith("/var/run/"):
        return True
    return any(part.lower() in _FORBIDDEN_PATH_PARTS for part in PurePosixPath(normalized).parts)
