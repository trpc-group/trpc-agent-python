# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Sandbox adapter for the example rule-runner command."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .governance import ExecutionRequest
from .governance import evaluate_execution_request
from .models import FilterDecision
from .models import FilterEvent
from .models import FilterReasonCode
from .models import FilterTargetType
from .models import RuntimeKind
from .models import SandboxRun
from .models import SandboxStatus
from .sandbox_workspace import _RuntimeAdapterResult  # noqa: F401
from .sandbox_workspace import _RuntimeUnavailableError
from .sandbox_workspace import _WorkspaceRuntimeAdapter  # noqa: F401
from .sandbox_workspace import _failure_result
from .sandbox_workspace import _resolve_runtime_adapter
from .sandbox_workspace import _truncate
from .sanitizer import redact_mapping
from .sanitizer import redact_text


def run_rule_script(
    task_id: str,
    runtime: RuntimeKind,
    allow_local: bool,
    input_path: str | Path,
    manifest_path: str | Path,
    output_path: str | Path,
    timeout_sec: float = 30.0,
    output_limit_bytes: int = 65536,
    container_image: str = "python:3-slim",
    docker_base_url: str = "",
) -> tuple[SandboxRun, list[FilterEvent], dict[str, Any]]:
    """Run or simulate the bundled rule runner under example governance."""
    input_file = Path(input_path).expanduser().resolve()
    manifest_file = Path(manifest_path).expanduser().resolve()
    output_file = Path(output_path).expanduser().resolve()
    script_path = Path(__file__).parents[1] / "skills" / "code-review" / "scripts" / "rule_runner.py"
    command = [
        sys.executable,
        str(script_path),
        "--input",
        str(input_file),
        "--manifest",
        str(manifest_file),
        "--output",
        str(output_file),
    ]
    request = ExecutionRequest(
        command=command,
        runtime=runtime,
        cwd=str(Path(__file__).parents[1]),
        timeout_sec=timeout_sec,
        output_limit_bytes=output_limit_bytes,
        allow_local=allow_local,
        script_path=str(script_path),
        allowed_roots=(str(Path(__file__).parents[1]), str(output_file.parent)),
        metadata={
            "input": str(input_file),
            "manifest": str(manifest_file),
            "output": str(output_file)
        },
    )
    filter_event = evaluate_execution_request(task_id, request)
    sandbox_run = SandboxRun(
        task_id=task_id,
        runtime=runtime,
        command=filter_event.command,
        timeout_sec=timeout_sec,
        output_limit_bytes=output_limit_bytes,
    )

    if filter_event.decision is FilterDecision.DENY:
        sandbox_run.status = SandboxStatus.DENIED
        sandbox_run.stderr = filter_event.reason
        sandbox_run.error_type = filter_event.reason_code.value
        result = _failure_result(filter_event.reason)
        _write_json(output_file, result)
        return sandbox_run, [filter_event], result
    if filter_event.decision is FilterDecision.NEEDS_HUMAN_REVIEW:
        sandbox_run.status = SandboxStatus.NEEDS_HUMAN_REVIEW
        sandbox_run.stderr = filter_event.reason
        sandbox_run.error_type = filter_event.reason_code.value
        result = _failure_result(filter_event.reason)
        _write_json(output_file, result)
        return sandbox_run, [filter_event], result
    if runtime is RuntimeKind.DRY_RUN:
        return _run_dry(input_file, manifest_file, output_file, sandbox_run, filter_event, timeout_sec,
                        output_limit_bytes)
    if runtime is RuntimeKind.LOCAL_DEV:
        return _run_local(command, output_file, sandbox_run, filter_event, timeout_sec, output_limit_bytes)
    resolved = _resolve_runtime_adapter(
        runtime,
        container_image=container_image,
        docker_base_url=docker_base_url,
        timeout_sec=timeout_sec,
    )
    if not resolved.available:
        filter_event.decision = FilterDecision.NEEDS_HUMAN_REVIEW
        filter_event.reason = resolved.reason
        filter_event.reason_code = FilterReasonCode.SANDBOX_UNAVAILABLE
        filter_event.target_type = FilterTargetType.RUNTIME
        sandbox_run.status = SandboxStatus.NEEDS_HUMAN_REVIEW
        sandbox_run.stderr = resolved.reason
        sandbox_run.error_type = "RuntimeUnavailable"
        result = _failure_result(resolved.reason)
        _write_json(output_file, result)
        return sandbox_run, [filter_event], result
    if resolved.adapter is not None:
        try:
            adapter_run, result = resolved.adapter.run_rule_script(
                task_id,
                input_file,
                manifest_file,
                output_file,
                timeout_sec,
                output_limit_bytes,
            )
            adapter_run.command = filter_event.command
            adapter_run.timeout_sec = timeout_sec
            adapter_run.output_limit_bytes = output_limit_bytes
            result = redact_mapping(result)
            _write_json(output_file, result)
            return adapter_run, [filter_event], result
        except _RuntimeUnavailableError as ex:
            filter_event.decision = FilterDecision.NEEDS_HUMAN_REVIEW
            filter_event.reason = redact_text(str(ex))
            filter_event.reason_code = FilterReasonCode.SANDBOX_UNAVAILABLE
            filter_event.target_type = FilterTargetType.RUNTIME
            sandbox_run.status = SandboxStatus.NEEDS_HUMAN_REVIEW
            sandbox_run.stderr = filter_event.reason
            sandbox_run.error_type = "RuntimeUnavailable"
            result = _failure_result(str(ex))
            _write_json(output_file, result)
            return sandbox_run, [filter_event], result
        except Exception as ex:  # pragma: no cover - defensive optional adapter boundary
            sandbox_run.status = SandboxStatus.FAILED
            sandbox_run.stderr = redact_text(str(ex))
            sandbox_run.error_type = type(ex).__name__
            result = _failure_result(str(ex))
            _write_json(output_file, result)
            return sandbox_run, [filter_event], result
    sandbox_run.status = SandboxStatus.NEEDS_HUMAN_REVIEW
    sandbox_run.stderr = "runtime is not connected in this example"
    result = _failure_result(sandbox_run.stderr)
    _write_json(output_file, result)
    return sandbox_run, [filter_event], result


def _run_dry(
    input_file: Path,
    manifest_file: Path,
    output_file: Path,
    sandbox_run: SandboxRun,
    filter_event: FilterEvent,
    timeout_sec: float,
    output_limit_bytes: int,
) -> tuple[SandboxRun, list[FilterEvent], dict[str, Any]]:
    started = time.monotonic()
    try:
        result = _load_rule_runner_module().run(input_file, manifest_file)
        result = redact_mapping(result)
        _write_json(output_file, result)
        sandbox_run.stdout, sandbox_run.stdout_truncated = _truncate(json.dumps(result, ensure_ascii=True),
                                                                     output_limit_bytes)
        sandbox_run.exit_code = 0
        sandbox_run.status = SandboxStatus.SUCCESS
    except Exception as ex:  # pragma: no cover - defensive artifact conversion
        result = _failure_result(str(ex))
        _write_json(output_file, result)
        sandbox_run.status = SandboxStatus.FAILED
        sandbox_run.exit_code = 1
        sandbox_run.stderr = redact_text(str(ex))
        sandbox_run.error_type = type(ex).__name__
    sandbox_run.duration_ms = int((time.monotonic() - started) * 1000)
    sandbox_run.timeout_sec = timeout_sec
    return sandbox_run, [filter_event], result


def _run_local(
    command: list[str],
    output_file: Path,
    sandbox_run: SandboxRun,
    filter_event: FilterEvent,
    timeout_sec: float,
    output_limit_bytes: int,
) -> tuple[SandboxRun, list[FilterEvent], dict[str, Any]]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
        )
        stdout, stdout_truncated = _truncate(redact_text(completed.stdout), output_limit_bytes)
        stderr, stderr_truncated = _truncate(redact_text(completed.stderr), output_limit_bytes)
        sandbox_run.stdout = stdout
        sandbox_run.stderr = stderr
        sandbox_run.stdout_truncated = stdout_truncated
        sandbox_run.stderr_truncated = stderr_truncated
        sandbox_run.exit_code = completed.returncode
        sandbox_run.status = SandboxStatus.SUCCESS if completed.returncode == 0 else SandboxStatus.FAILED
        sandbox_run.error_type = "" if completed.returncode == 0 else "CommandFailed"
    except subprocess.TimeoutExpired as ex:
        stdout, stdout_truncated = _truncate(redact_text((ex.stdout or "") if isinstance(ex.stdout, str) else ""),
                                             output_limit_bytes)
        stderr, stderr_truncated = _truncate(redact_text((ex.stderr or "") if isinstance(ex.stderr, str) else ""),
                                             output_limit_bytes)
        sandbox_run.stdout = stdout
        sandbox_run.stderr = stderr or f"Command timed out after {timeout_sec:g}s"
        sandbox_run.stdout_truncated = stdout_truncated
        sandbox_run.stderr_truncated = stderr_truncated
        sandbox_run.status = SandboxStatus.TIMEOUT
        sandbox_run.exit_code = None
        sandbox_run.error_type = "TimeoutExpired"
        _write_json(output_file, _failure_result(sandbox_run.stderr))
    except OSError as ex:
        sandbox_run.status = SandboxStatus.FAILED
        sandbox_run.exit_code = None
        sandbox_run.stderr = redact_text(str(ex))
        sandbox_run.error_type = type(ex).__name__
        _write_json(output_file, _failure_result(str(ex)))
    sandbox_run.duration_ms = int((time.monotonic() - started) * 1000)
    if output_file.is_file() and sandbox_run.status is SandboxStatus.SUCCESS:
        try:
            result = redact_mapping(json.loads(output_file.read_text(encoding="utf-8")))
            _write_json(output_file, result)
        except (OSError, json.JSONDecodeError) as ex:
            result = _failure_result(f"rule result could not be read: {ex}")
            sandbox_run.status = SandboxStatus.FAILED
            sandbox_run.error_type = type(ex).__name__
    else:
        result = _failure_result(sandbox_run.stderr)
    return sandbox_run, [filter_event], result


def _load_rule_runner_module():
    import importlib.util

    script_path = Path(__file__).parents[1] / "skills" / "code-review" / "scripts" / "rule_runner.py"
    spec = importlib.util.spec_from_file_location("skills_code_review_rule_runner", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load rule runner: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redact_mapping(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
