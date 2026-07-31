# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Example-local execution governance for rule and sandbox commands."""

from __future__ import annotations

import re
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
_NETWORK_COMMANDS = ("curl", "wget", "nc", "ncat", "telnet", "ssh", "scp")
_SHELL_EXECUTABLES = {"sh", "bash"}
_SHELL_PAYLOAD_FLAGS = {"-c", "-lc"}
_SENSITIVE_PATH_ROOTS = ("/etc", "/usr", "/var", "/dev")
_WRITE_COMMANDS = {"tee", "cp", "mv", "install"}
_SENSITIVE_REDIRECT_RE = re.compile(r"(?i)(?:^|[\s;&|\n])(?:\d+)?\s*>{1,2}\s*/\s*(?:etc|usr|var|dev)(?:/|\b)")
_UNSUPPORTED_SHELL_RE = re.compile(r"(?:\$\(|`)")


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
    referenced_paths: tuple[str, ...] = field(default_factory=tuple)
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
    if _is_dangerous_command(request.command):
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
    roots = [_resolve_governance_path(root) for root in request.allowed_roots]
    paths = [_resolve_governance_path(request.cwd)]
    if request.script_path:
        paths.append(_resolve_governance_path(request.script_path))
    paths.extend(_resolve_governance_path(path) for path in request.referenced_paths if path)
    for resolved in paths:
        if not any(_is_relative_to(resolved, root) for root in roots):
            return False
    return True


def _resolve_governance_path(path: str) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _uses_network(command: list[str]) -> bool:
    analysis = _analyze_command(command)
    if analysis.executable in _NETWORK_COMMANDS:
        return True
    for payload in analysis.shell_payloads:
        if _payload_invokes_known_command(payload, _NETWORK_COMMANDS):
            return True
    return False


def _disallowed_env_keys(request: ExecutionRequest) -> list[str]:
    allowed = {key.upper() for key in request.allowed_env_keys}
    return sorted(key for key in request.env if key.upper() not in allowed)


def _is_dangerous_command(command: list[str]) -> bool:
    analysis = _analyze_command(command)
    argv = analysis.argv
    if _argv_is_dangerous(argv):
        return True
    return any(_payload_is_dangerous(payload) for payload in analysis.shell_payloads)


def _command_text(command: list[str]) -> str:
    return shlex.join(command)


@dataclass(frozen=True)
class _CommandAnalysis:
    argv: tuple[str, ...]
    executable: str
    shell_payloads: tuple[str, ...] = ()


def _analyze_command(command: list[str]) -> _CommandAnalysis:
    argv = tuple(command)
    executable = Path(argv[0]).name.lower() if argv else ""
    shell_payloads: list[str] = []
    if executable in _SHELL_EXECUTABLES:
        for index, part in enumerate(argv[:-1]):
            if part in _SHELL_PAYLOAD_FLAGS:
                shell_payloads.append(argv[index + 1])
    return _CommandAnalysis(argv=argv, executable=executable, shell_payloads=tuple(shell_payloads))


def _argv_is_dangerous(argv: tuple[str, ...]) -> bool:
    if not argv:
        return False
    executable = Path(argv[0]).name.lower()
    arguments = [item.lower() for item in argv[1:]]
    if executable == "rm" and _rm_is_recursive_force(arguments):
        return True
    if executable == "chmod" and _has_recursive_flag(arguments):
        return True
    if executable == "chown" and _has_recursive_flag(arguments):
        return True
    if executable == "mkfs":
        return True
    if executable == "dd" and any(
            item.startswith(("if=", "of=")) and _is_sensitive_path(item.split("=", 1)[1]) for item in arguments):
        return True
    if executable in _WRITE_COMMANDS and _argv_writes_sensitive_path(arguments):
        return True
    return False


def _rm_is_recursive_force(arguments: list[str]) -> bool:
    long_flags = {item for item in arguments if item.startswith("--")}
    if {"--recursive", "--force"} <= long_flags:
        return True
    short_flags: set[str] = set()
    for item in arguments:
        if item.startswith("-") and not item.startswith("--"):
            short_flags.update(item[1:])
    return "r" in short_flags and "f" in short_flags


def _has_recursive_flag(arguments: list[str]) -> bool:
    for item in arguments:
        if item == "--recursive":
            return True
        if item.startswith("-") and not item.startswith("--") and "r" in item[1:]:
            return True
    return False


def _payload_invokes_known_command(payload: str, known_commands: tuple[str, ...]) -> bool:
    lowered = payload.lower()
    for command_name in known_commands:
        pattern = rf"(^|[;&|\n][&|]?\s*){re.escape(command_name)}(\s|$)"
        if re.search(pattern, lowered):
            return True
    return False


def _payload_is_dangerous(payload: str) -> bool:
    if _UNSUPPORTED_SHELL_RE.search(payload):
        return True
    if _SENSITIVE_REDIRECT_RE.search(payload):
        return True
    for command in re.split(r"[;&|\n]+", payload):
        try:
            argv = tuple(shlex.split(command))
        except ValueError:
            argv = tuple(command.split())
        if _argv_is_dangerous(argv):
            return True
    return False


def _argv_writes_sensitive_path(arguments: list[str]) -> bool:
    return any(not item.startswith("-") and _is_sensitive_path(item) for item in arguments)


def _is_sensitive_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    if not normalized.startswith("/"):
        return False
    normalized = "/" + normalized.lstrip("/")
    return any(normalized == root or normalized.startswith(f"{root}/") for root in _SENSITIVE_PATH_ROOTS)
