# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Example-local execution governance for rule and sandbox commands."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

from .models import FilterDecision
from .models import FilterEvent
from .models import FilterReasonCode
from .models import FilterTargetType
from .models import RuntimeKind
from .sanitizer import redact_mapping
from .sanitizer import redact_text

MAX_TIMEOUT_SEC = 120.0
MAX_OUTPUT_LIMIT_BYTES = 1024 * 1024
DEFAULT_ALLOWED_ENV_KEYS = ("LANG", "LC_ALL", "PATH", "PYTHONPATH")
_DANGEROUS_COMMAND_FRAGMENTS = (
    "rm -rf",
    "sudo ",
    "chmod -R",
    "chown -R",
    "mkfs",
    "dd if=",
    "> /etc/",
    "> /usr/",
    "> /var/",
)
_NETWORK_COMMANDS = ("curl", "wget", "nc", "ncat", "telnet", "ssh", "scp")


@dataclass(frozen=True)
class ExecutionRequest:
    """A proposed command execution reviewed by the example governance policy."""

    command: list[str]
    runtime: RuntimeKind
    cwd: str
    timeout_sec: float = 30.0
    output_limit_bytes: int = 65536
    allow_local: bool = False
    network_allowed: bool = False
    script_path: str = ""
    allowed_roots: tuple[str, ...] = field(default_factory=tuple)
    env: dict[str, str] = field(default_factory=dict)
    allowed_env_keys: tuple[str, ...] = DEFAULT_ALLOWED_ENV_KEYS
    metadata: dict[str, Any] = field(default_factory=dict)


def evaluate_execution_request(task_id: str, request: ExecutionRequest) -> FilterEvent:
    """Evaluate whether a proposed execution may continue."""
    command_text = _command_text(request.command)
    base = {
        "task_id": task_id,
        "target": redact_text(command_text),
        "command": redact_text(command_text),
        "runtime": request.runtime,
        "cwd": str(Path(request.cwd).expanduser()),
        "script_path": request.script_path,
        "timeout_sec": request.timeout_sec,
        "output_limit_bytes": request.output_limit_bytes,
        "metadata": redact_mapping({
            **request.metadata, "env": request.env
        } if request.env else request.metadata),
    }
    if request.runtime is RuntimeKind.LOCAL_DEV and not request.allow_local:
        return _event(
            **base,
            decision=FilterDecision.DENY,
            reason="local-dev runtime requires explicit --allow-local",
            reason_code=FilterReasonCode.LOCAL_RUNTIME_DENIED,
            target_type=FilterTargetType.RUNTIME,
        )
    if request.timeout_sec <= 0 or request.timeout_sec > MAX_TIMEOUT_SEC:
        return _event(
            **base,
            decision=FilterDecision.DENY,
            reason=f"timeout must be between 0 and {MAX_TIMEOUT_SEC:g} seconds",
            reason_code=FilterReasonCode.BUDGET_EXCEEDED,
            target_type=FilterTargetType.BUDGET,
        )
    if request.output_limit_bytes <= 0 or request.output_limit_bytes > MAX_OUTPUT_LIMIT_BYTES:
        return _event(
            **base,
            decision=FilterDecision.DENY,
            reason=f"output limit must be between 1 and {MAX_OUTPUT_LIMIT_BYTES} bytes",
            reason_code=FilterReasonCode.OUTPUT_LIMIT_EXCEEDED,
            target_type=FilterTargetType.BUDGET,
        )
    if not _paths_are_allowed(request):
        return _event(
            **base,
            decision=FilterDecision.DENY,
            reason="execution path escapes the allowed example workspace",
            reason_code=FilterReasonCode.FORBIDDEN_PATH,
            target_type=FilterTargetType.PATH,
        )
    disallowed_env = _disallowed_env_keys(request)
    if disallowed_env:
        return _event(
            **base,
            decision=FilterDecision.DENY,
            reason=f"environment variable is not allowed: {disallowed_env[0]}",
            reason_code=FilterReasonCode.ENV_NOT_ALLOWED,
            target_type=FilterTargetType.ENV,
        )
    if _uses_network(request.command) and not request.network_allowed:
        return _event(
            **base,
            decision=FilterDecision.NEEDS_HUMAN_REVIEW,
            reason="network-capable command requires human review",
            reason_code=FilterReasonCode.NETWORK_DENIED,
            target_type=FilterTargetType.NETWORK,
        )
    if _is_dangerous_command(command_text):
        return _event(
            **base,
            decision=FilterDecision.DENY,
            reason="high-risk command is not allowed by the example policy",
            reason_code=FilterReasonCode.HIGH_RISK_COMMAND,
            target_type=FilterTargetType.COMMAND,
        )
    return _event(
        **base,
        decision=FilterDecision.ALLOW,
        reason="execution request allowed by example policy",
        reason_code=FilterReasonCode.UNKNOWN,
        target_type=FilterTargetType.COMMAND,
    )


def _event(
    *,
    task_id: str,
    decision: FilterDecision,
    reason: str,
    reason_code: FilterReasonCode,
    target_type: FilterTargetType,
    target: str,
    command: str,
    runtime: RuntimeKind,
    cwd: str,
    script_path: str,
    timeout_sec: float,
    output_limit_bytes: int,
    metadata: dict[str, Any],
) -> FilterEvent:
    return FilterEvent(
        task_id=task_id,
        decision=decision,
        reason=redact_text(reason),
        reason_code=reason_code,
        target_type=target_type,
        target=target,
        command=command,
        runtime=runtime,
        cwd=cwd,
        script_path=script_path,
        timeout_sec=timeout_sec,
        output_limit_bytes=output_limit_bytes,
        metadata=metadata,
    )


def _paths_are_allowed(request: ExecutionRequest) -> bool:
    if not request.allowed_roots:
        return True
    roots = [Path(root).expanduser().resolve() for root in request.allowed_roots]
    paths = [Path(request.cwd).expanduser()]
    if request.script_path:
        paths.append(Path(request.script_path).expanduser())
    for path in paths:
        resolved = path.resolve()
        if not any(_is_relative_to(resolved, root) for root in roots):
            return False
    return True


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _uses_network(command: list[str]) -> bool:
    return any(Path(part).name in _NETWORK_COMMANDS for part in command)


def _disallowed_env_keys(request: ExecutionRequest) -> list[str]:
    allowed = {key.upper() for key in request.allowed_env_keys}
    return sorted(key for key in request.env if key.upper() not in allowed)


def _is_dangerous_command(command_text: str) -> bool:
    lowered = command_text.lower()
    return any(fragment.lower() in lowered for fragment in _DANGEROUS_COMMAND_FRAGMENTS)


def _command_text(command: list[str]) -> str:
    return shlex.join(command)
