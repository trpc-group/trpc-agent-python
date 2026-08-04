# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Reporting, artifact publication, and sensitive-value handling."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable
from typing import Literal
from typing import TYPE_CHECKING
from typing import TypeAlias
from uuid import uuid4

from pydantic import BaseModel

from ..data.schemas import ArtifactIndex
from ..data.schemas import ArtifactReference
from ..data.schemas import FailureReport
from ..data.schemas import OptimizationReport
from ..data.schemas import OptimizerResourceObservation
from ..data.schemas import OptimizerResourceValue
from ..data.schemas import PipelineStageResult
from ..data.schemas import RealStageResult
from ..data.schemas import ReportPhase
from ..data.schemas import ReportProgress
from ..data.schemas import TraceCandidateProposal
from ..data.schemas import TraceStageResult


if TYPE_CHECKING:
    from .pipeline import PreparedRun

API_KEY_PLACEHOLDER = "${TRPC_AGENT_API_KEY}"
BASE_URL_PLACEHOLDER = "${TRPC_AGENT_BASE_URL}"

_SENSITIVE_CONFIG_KEYS = {
    "accesstoken",
    "apikey",
    "auth",
    "authorization",
    "authtoken",
    "baseurl",
    "bearertoken",
    "clientsecret",
    "credential",
    "credentials",
    "password",
    "passwd",
    "privatekey",
    "secret",
    "secretkey",
    "token",
    "xapikey",
}
_SENSITIVE_CONFIG_KEY_SUFFIXES = {
    "accesstoken",
    "apikey",
    "authtoken",
    "baseurl",
    "bearertoken",
    "clientsecret",
    "credential",
    "credentials",
    "endpointurl",
    "password",
    "passwd",
    "privatekey",
    "secretkey",
}
_URL_CONFIG_KEY_SUFFIXES = {"baseurl", "endpointurl"}
_APPROVED_SENSITIVE_VALUES = {
    "",
    API_KEY_PLACEHOLDER,
    BASE_URL_PLACEHOLDER,
    "fake-not-used-in-offline-mode",
}


class SensitiveConfigError(ValueError):
    """配置中存在不允许持久化的连接信息或凭据。"""


def _normalized_key(key: str) -> str:
    return key.replace("_", "").replace("-", "").casefold()


def _is_sensitive_key(key: str) -> bool:
    normalized = _normalized_key(key)
    return normalized in _SENSITIVE_CONFIG_KEYS or any(
        normalized.endswith(suffix)
        for suffix in _SENSITIVE_CONFIG_KEY_SUFFIXES
    )


def _placeholder_for_key(key: str) -> str:
    normalized = _normalized_key(key)
    if any(normalized.endswith(suffix) for suffix in _URL_CONFIG_KEY_SUFFIXES):
        return BASE_URL_PLACEHOLDER
    return API_KEY_PLACEHOLDER


def replace_persisted_sensitive_values(value: object) -> object:
    """递归替换任何可能进入运行产物的连接地址和凭据。"""
    if isinstance(value, str):
        if value.strip().casefold().startswith(("http://", "https://")):
            return BASE_URL_PLACEHOLDER
        return value
    if isinstance(value, list):
        return [replace_persisted_sensitive_values(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: (
            _placeholder_for_key(key)
            if _is_sensitive_key(key)
            else replace_persisted_sensitive_values(item)
        )
        for key, item in value.items()
    }


def validate_persisted_sensitive_values(value: object, *, path: str = "$") -> None:
    """拒绝不符合共享占位符策略的持久化配置。"""
    if isinstance(value, str):
        if value.strip().casefold().startswith(("http://", "https://")):
            raise SensitiveConfigError(
                "sensitive optimizer config value is not an approved "
                f"placeholder: {path}"
            )
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            validate_persisted_sensitive_values(item, path=f"{path}[{index}]")
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        item_path = f"{path}.{key}"
        if _is_sensitive_key(key):
            if not isinstance(item, str) or item not in _APPROVED_SENSITIVE_VALUES:
                raise SensitiveConfigError(
                    "sensitive optimizer config value is not an approved "
                    f"placeholder: {item_path}"
                )
        else:
            validate_persisted_sensitive_values(item, path=item_path)


_OPTIMIZER_SCOPE = (
    "Optimizer-only observation; excludes complete business Agent evaluation usage."
)
_OFFLINE_OPTIMIZER_REASON = "Offline mode uses a deterministic candidate provider."
_TRACE_OPTIMIZER_REASON = "Trace replay does not run a candidate provider or AgentOptimizer."
_MISSING_COST_REASON = (
    "Reflection LM calls were observed but optimizer cost was not reported."
)
_MISSING_TOKEN_REASON = (
    "Reflection LM calls were observed but optimizer token usage was not reported."
)
_INVALID_TOKEN_REASON = "Optimizer token usage was malformed or inconsistent."
_REDACTED = "[REDACTED]"
_SENSITIVE_ENV_NAMES = ("TRPC_AGENT_API_KEY", "TRPC_AGENT_BASE_URL")
_SENSITIVE_KEY_VALUE = re.compile(
    r"(?P<prefix>[\"']?(?:api[_-]?key|base[_-]?url|authorization)[\"']?\s*[:=]\s*)"
    r"(?P<value>[\"'][^\"']*[\"']|(?:(?:bearer|basic|token)\s+)?[^\s,;}\]]+)",
    re.IGNORECASE,
)
_HTTP_URL = re.compile(r"https?://[^\s,;}\]<>\"']+", re.IGNORECASE)
_BEARER_VALUE = re.compile(
    r"\bbearer(?:\s+|\s*[:=]\s*)[\"']?[^\s,;}\]\"']+[\"']?",
    re.IGNORECASE,
)


def _not_applicable_optimizer_value(
    unit: str, reason: str,
) -> OptimizerResourceValue[object]:
    return OptimizerResourceValue[object](
        status="not_applicable",
        unit=unit,
        reason=reason,
    )


def redact_error_message(error: Exception) -> str:
    """移除异常文本中的环境凭据、认证字段和连接地址。"""
    message = str(error)
    environment_values = {
        os.environ.get(name, "")
        for name in _SENSITIVE_ENV_NAMES
        if os.environ.get(name, "")
    }
    for sensitive_value in sorted(environment_values, key=len, reverse=True):
        message = message.replace(sensitive_value, _REDACTED)
    message = _SENSITIVE_KEY_VALUE.sub(
        lambda match: f"{match.group('prefix')}{_REDACTED}",
        message,
    )
    message = _BEARER_VALUE.sub(f"Bearer {_REDACTED}", message)
    return _HTTP_URL.sub(_REDACTED, message)


def _is_complete_token_usage(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    required = ("prompt", "completion", "total")
    if not all(key in value for key in required):
        return False
    if not all(type(value[key]) is int and value[key] >= 0 for key in required):
        return False
    return value["total"] == value["prompt"] + value["completion"]


def _optimizer_resources(result: PipelineStageResult) -> OptimizerResourceObservation:
    if not isinstance(result, RealStageResult):
        reason = (
            _TRACE_OPTIMIZER_REASON
            if isinstance(result, TraceStageResult)
            else _OFFLINE_OPTIMIZER_REASON
        )
        return OptimizerResourceObservation(
            scope_note=reason,
            total_rounds=_not_applicable_optimizer_value("rounds", reason),
            reflection_lm_calls=_not_applicable_optimizer_value("calls", reason),
            cost_usd=_not_applicable_optimizer_value("USD", reason),
            token_usage=_not_applicable_optimizer_value("tokens", reason),
            duration_seconds=_not_applicable_optimizer_value("seconds", reason),
        )
    native = result.optimize_result
    reflection_calls = native.total_reflection_lm_calls
    cost_missing = reflection_calls > 0 and native.total_llm_cost <= 0
    token_usage = native.total_token_usage
    token_usage_valid = _is_complete_token_usage(token_usage)
    tokens_missing = (
        not token_usage_valid
        or (reflection_calls > 0 and token_usage["total"] <= 0)
    )
    return OptimizerResourceObservation(
        scope_note=_OPTIMIZER_SCOPE,
        total_rounds=OptimizerResourceValue[int](
            status="available", value=native.total_rounds, unit="rounds",
        ),
        reflection_lm_calls=OptimizerResourceValue[int](
            status="available", value=reflection_calls, unit="calls",
        ),
        cost_usd=OptimizerResourceValue[float](
            status="unavailable" if cost_missing else "available",
            value=None if cost_missing else native.total_llm_cost,
            unit="USD",
            reason=_MISSING_COST_REASON if cost_missing else None,
        ),
        token_usage=OptimizerResourceValue[dict[str, int]](
            status="unavailable" if tokens_missing else "available",
            value=None if tokens_missing else token_usage,
            unit="tokens",
            reason=(
                _INVALID_TOKEN_REASON
                if tokens_missing and not token_usage_valid
                else _MISSING_TOKEN_REASON if tokens_missing else None
            ),
        ),
        duration_seconds=OptimizerResourceValue[float](
            status="available", value=native.duration_seconds, unit="seconds",
        ),
    )

def build_optimization_report(
    prepared: PreparedRun, result: PipelineStageResult, *, progress: ReportProgress, finished_at: datetime,
) -> OptimizationReport:
    return OptimizationReport(
        run_id=prepared.workspace.run_id, execution_mode=prepared.config.execution.mode,
        seed=prepared.input_snapshot.seed, started_at=progress.started_at, finished_at=finished_at,
        input_snapshot=prepared.input_snapshot, candidate=result.candidate,
        baseline_train=result.baseline_train, baseline_validation=result.baseline_validation,
        candidate_train=result.candidate_train, candidate_validation=result.candidate_validation,
        analysis=result.analysis, pipeline_resources=result.measurements,
        optimizer_resources=_optimizer_resources(result), gate_decision=result.gate_decision,
        writeback=result.writeback,
    )

def build_failure_report(
    prepared: PreparedRun, *, progress: ReportProgress, error: Exception,
    source_prompt_hashes: dict[str, str], existing_artifacts: list[str], generated_at: datetime,
) -> FailureReport:
    return FailureReport(
        run_id=prepared.workspace.run_id, execution_mode=prepared.config.execution.mode,
        failed_phase=progress.current_phase, exception_type=type(error).__name__,
        error_message=redact_error_message(error), generated_at=generated_at,
        input_snapshot=prepared.input_snapshot,
        source_prompt_hashes=dict(sorted(source_prompt_hashes.items())),
        completed_phases=progress.completed_phases, existing_artifacts=sorted(existing_artifacts),
    )


def render_optimization_markdown(report: OptimizationReport) -> str:
    decision = report.gate_decision.decision.upper()
    lines = [
        "# Optimization Report",
        "",
        f"- Run: `{report.run_id}`",
        f"- Mode: `{report.execution_mode}`",
        f"- Gate decision: {decision}",
        f"- Candidate: `{report.candidate.candidate_id}`",
        "",
        "## Full Evaluations",
        "",
    ]
    for label, snapshot in (
        ("Baseline train", report.baseline_train),
        ("Baseline validation", report.baseline_validation),
        ("Candidate train", report.candidate_train),
        ("Candidate validation", report.candidate_validation),
    ):
        score = snapshot.average_score if snapshot.average_score is not None else "unavailable"
        lines.append(
            f"- {label}: {snapshot.passed_case_count}/{snapshot.total_case_count} passed; "
            f"average score={score}"
        )
    lines.extend(["", "## Gate", ""])
    lines.extend(f"- Rejection: {reason}" for reason in report.gate_decision.rejection_reasons)
    lines.extend(f"- Warning: {warning}" for warning in report.gate_decision.warnings)
    if not report.gate_decision.rejection_reasons and not report.gate_decision.warnings:
        lines.append("- No rejection reasons or warnings.")
    lines.extend(["", "## Candidate Changes", ""])
    changed = report.candidate.changed_fields or ["none"]
    lines.extend(f"- {field}" for field in changed)
    lines.extend(["", "## Overfit", f"- Status: {report.analysis.overfit_status}",
                  f"- Reason: {report.analysis.overfit_reason}", "", "## Writeback",
                  f"- Status: {report.writeback.status}", f"- Reason: {report.writeback.reason}",
                  "", "## Pipeline Observations",
                  f"- Cost: {report.pipeline_resources.cost_usd.status}",
                  f"- Tokens: {report.pipeline_resources.total_tokens.status}",
                  f"- Duration: {report.pipeline_resources.duration_seconds.status}",
                  "", "## Optimizer Resources"])
    for label, observation in (
        ("Rounds", report.optimizer_resources.total_rounds),
        ("Reflection calls", report.optimizer_resources.reflection_lm_calls),
        ("Cost", report.optimizer_resources.cost_usd),
        ("Token usage", report.optimizer_resources.token_usage),
        ("Duration", report.optimizer_resources.duration_seconds),
    ):
        line = f"- {label}: {observation.status}; unit={observation.unit}"
        if observation.value is not None:
            value = observation.value
            if isinstance(value, dict):
                value = ", ".join(
                    f"{key}={item}" for key, item in sorted(value.items())
                )
            line += f"; value={value}"
        if observation.reason is not None:
            line += f"; reason={observation.reason}"
        lines.append(line)
    lines.extend(["", "## Optimizer Scope", f"- {report.optimizer_resources.scope_note}"])
    return "\n".join(lines) + "\n"


ArtifactType: TypeAlias = Literal[
    "input",
    "prompt",
    "evaluation",
    "candidate",
    "optimizer_native",
    "report",
]

_INPUT_COPY_DISABLED = "artifacts.copy_input_files=false"
_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_RENAME_EXCL = 0x4
_RENAMEAT2_UNAVAILABLE = {
    errno.ENOSYS,
    errno.EINVAL,
    getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
}


class ArtifactWriteError(RuntimeError):
    """Raised when an artifact cannot be safely materialized or discovered."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolved_run_dir(run_dir: Path) -> Path:
    if run_dir.is_symlink():
        raise ArtifactWriteError(f"run directory must not be a symbolic link: {run_dir}")
    try:
        root = run_dir.resolve(strict=True)
    except OSError as exc:
        raise ArtifactWriteError(f"run directory is unavailable: {run_dir}: {exc}") from exc
    if not root.is_dir():
        raise ArtifactWriteError(f"run directory must be a directory: {run_dir}")
    return root


def _inside_run(run_dir: Path, path: Path) -> Path:
    root = run_dir.resolve(strict=True)
    lexical = path if path.is_absolute() else root / path
    try:
        relative = lexical.relative_to(root)
    except ValueError as exc:
        raise ArtifactWriteError(f"artifact escapes run directory: {path}") from exc

    current = root
    for component in relative.parts:
        current /= component
        if current.is_symlink():
            raise ArtifactWriteError(f"artifact must not be a symbolic link: {path}")

    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise ArtifactWriteError(f"artifact is unavailable: {path}: {exc}") from exc
    if not resolved.is_relative_to(root):
        raise ArtifactWriteError(f"artifact escapes run directory: {path}")
    if not resolved.is_file():
        raise ArtifactWriteError(f"artifact must be a regular file: {path}")
    return resolved


def discover_run_artifacts(run_dir: Path) -> list[str]:
    """Return regular files below a run without ever accepting symlinks."""
    root = _resolved_run_dir(run_dir)
    paths: list[str] = []
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(directory)
        directory_names.sort()
        file_names.sort()

        retained_directories = []
        for name in directory_names:
            path = current / name
            if path.is_symlink():
                raise ArtifactWriteError(
                    f"artifact must not be a symbolic link: {path}"
                )
            relative = path.relative_to(root).as_posix()
            if ".report.tmp-" not in relative:
                retained_directories.append(name)
        directory_names[:] = retained_directories

        for name in file_names:
            path = current / name
            if path.is_symlink():
                raise ArtifactWriteError(
                    f"artifact must not be a symbolic link: {path}"
                )
            relative = path.relative_to(root).as_posix()
            if name == "failure_report.json" or ".report.tmp-" in relative:
                continue
            if path.is_file():
                paths.append(relative)
    return sorted(paths)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _json_text(model: BaseModel) -> str:
    return model.model_dump_json(by_alias=False, indent=2) + "\n"


def _validate_optimizer_config_for_copy(path: Path) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactWriteError(
            f"failed to parse optimizer config snapshot: {path}: {exc}"
        ) from exc
    try:
        validate_persisted_sensitive_values(payload)
    except SensitiveConfigError as exc:
        raise ArtifactWriteError(str(exc)) from exc


def _rename_directory_no_replace(source: Path, target: Path) -> None:
    """Atomically publish a directory without replacing an existing target.

    The caller creates source and target as siblings beneath the resolved run
    directory, so the operation cannot cross a filesystem or Windows volume.
    Each supported platform uses an atomic no-replace primitive. Platforms
    without that primitive fail closed rather than risking a replacement race.
    """
    if source.parent.resolve() != target.parent.resolve():
        raise ArtifactWriteError(
            "atomic report publication requires sibling source and target paths"
        )
    if sys.platform.startswith("linux"):
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            renameat2 = libc.renameat2
        except (AttributeError, OSError):
            raise ArtifactWriteError(
                "atomic no-replace unavailable: Linux renameat2 is unavailable"
            )
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        ctypes.set_errno(0)
        result = renameat2(
            _AT_FDCWD,
            os.fsencode(source),
            _AT_FDCWD,
            os.fsencode(target),
            _RENAME_NOREPLACE,
        )
        if result == 0:
            return
        error_number = ctypes.get_errno()
        if error_number == errno.EEXIST:
            raise ArtifactWriteError(f"report directory already exists: {target}")
        if error_number in _RENAMEAT2_UNAVAILABLE:
            raise ArtifactWriteError(
                "atomic no-replace unavailable: Linux renameat2 does not support "
                f"RENAME_NOREPLACE ({os.strerror(error_number)})"
            )
        raise OSError(error_number, os.strerror(error_number), target)

    if sys.platform.startswith("win"):
        try:
            os.rename(source, target)
        except FileExistsError as exc:
            raise ArtifactWriteError(f"report directory already exists: {target}") from exc
        return

    if sys.platform == "darwin":
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            renamex_np = libc.renamex_np
        except (AttributeError, OSError):
            raise ArtifactWriteError(
                "atomic no-replace unavailable: Darwin renamex_np is unavailable"
            )
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        ctypes.set_errno(0)
        result = renamex_np(os.fsencode(source), os.fsencode(target), _RENAME_EXCL)
        if result == 0:
            return
        error_number = ctypes.get_errno()
        if error_number == errno.EEXIST:
            raise ArtifactWriteError(f"report directory already exists: {target}")
        if error_number in _RENAMEAT2_UNAVAILABLE:
            raise ArtifactWriteError(
                "atomic no-replace unavailable: Darwin renamex_np does not support "
                f"RENAME_EXCL ({os.strerror(error_number)})"
            )
        raise OSError(error_number, os.strerror(error_number), target)

    raise ArtifactWriteError(
        f"atomic no-replace unavailable: unsupported platform {sys.platform}"
    )


def _published_relative_path(root: Path, path: Path) -> str:
    relative = path.relative_to(root)
    if relative.parts and relative.parts[0].startswith(".report.tmp-"):
        relative = Path("report", *relative.parts[1:])
    return relative.as_posix()


def _available_reference(
    run_dir: Path,
    path: Path,
    *,
    artifact_id: str,
    artifact_type: ArtifactType,
    required: bool,
    produced_by: ReportPhase,
) -> ArtifactReference:
    root = run_dir.resolve(strict=True)
    resolved = _inside_run(root, path)
    return ArtifactReference(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        relative_path=_published_relative_path(root, path),
        required=required,
        produced_by=produced_by,
        status="available",
        size_bytes=resolved.stat().st_size,
        sha256=_sha256(resolved),
    )


def _unavailable_input_reference(
    *, artifact_id: str, produced_by: ReportPhase
) -> ArtifactReference:
    return ArtifactReference(
        artifact_id=artifact_id,
        artifact_type="input",
        required=True,
        produced_by=produced_by,
        status="unavailable",
        unavailable_reason=_INPUT_COPY_DISABLED,
    )


def _safe_prompt_name(field_name: str) -> str:
    safe = "".join(
        character if character.isalnum() or character in "._-" else "_"
        for character in field_name
    )
    return safe if safe not in {"", ".", ".."} else "prompt"


def _validate_available_references(
    root: Path, staging: Path, index: ArtifactIndex
) -> None:
    for reference in index.artifacts:
        if reference.status != "available":
            continue
        if reference.relative_path is None:
            raise ArtifactWriteError(
                f"available artifact has no relative path: {reference.artifact_id}"
            )
        relative = Path(reference.relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ArtifactWriteError(
                f"artifact path is not run-relative: {reference.relative_path}"
            )
        if relative.parts and relative.parts[0] == "report":
            path = staging.joinpath(*relative.parts[1:])
        else:
            path = root / relative
        resolved = _inside_run(root, path)
        if resolved.stat().st_size != reference.size_bytes:
            raise ArtifactWriteError(
                f"artifact size changed during staging: {reference.relative_path}"
            )
        if _sha256(resolved) != reference.sha256:
            raise ArtifactWriteError(
                f"artifact hash changed during staging: {reference.relative_path}"
            )


def _copy_input(
    *,
    root: Path,
    staging: Path,
    source: Path,
    expected_sha256: str,
    destination_name: str,
    artifact_id: str,
    produced_by: ReportPhase,
    content_validator: Callable[[Path], None] | None = None,
) -> ArtifactReference:
    if source.is_symlink():
        raise ArtifactWriteError(f"input must not be a symbolic link: {source}")
    try:
        actual_sha256 = _sha256(source)
    except OSError as exc:
        raise ArtifactWriteError(f"failed to read input {source}: {exc}") from exc
    if actual_sha256 != expected_sha256:
        raise ArtifactWriteError(f"input hash mismatch: {source}")
    if content_validator is not None:
        content_validator(source)

    destination = staging / "inputs" / destination_name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    if _sha256(destination) != expected_sha256:
        raise ArtifactWriteError(f"input hash changed while copying: {source}")
    return _available_reference(
        root,
        destination,
        artifact_id=artifact_id,
        artifact_type="input",
        required=True,
        produced_by=produced_by,
    )


def publish_report_bundle(
    report: OptimizationReport,
    *,
    run_dir: Path,
    copy_input_files: bool,
) -> ArtifactIndex:
    """Build a complete report in staging and atomically publish its directory."""
    staging: Path | None = None
    try:
        root = _resolved_run_dir(run_dir)
        target = root / "report"
        if target.exists() or target.is_symlink():
            raise ArtifactWriteError(f"report directory already exists: {target}")

        existing_paths = discover_run_artifacts(root)
        native_paths = [
            relative
            for relative in existing_paths
            if relative.startswith("optimizer/")
            or ("/" not in relative and relative.endswith(".runtime.json"))
        ]

        staging = root / f".report.tmp-{uuid4().hex}"
        staging.mkdir()
        references: list[ArtifactReference] = []

        report_json = staging / "optimization_report.json"
        _write_text(report_json, _json_text(report))
        references.append(
            _available_reference(
                root,
                report_json,
                artifact_id="report.optimization_json",
                artifact_type="report",
                required=True,
                produced_by="reporting",
            )
        )

        report_markdown = staging / "optimization_report.md"
        _write_text(report_markdown, render_optimization_markdown(report))
        references.append(
            _available_reference(
                root,
                report_markdown,
                artifact_id="report.optimization_markdown",
                artifact_type="report",
                required=True,
                produced_by="reporting",
            )
        )

        evaluations = (
            ("baseline_train", report.baseline_train, "baseline_train"),
            ("baseline_validation", report.baseline_validation, "baseline_validation"),
            ("candidate_train", report.candidate_train, "candidate_train"),
            (
                "candidate_validation",
                report.candidate_validation,
                "candidate_validation",
            ),
        )
        for name, evaluation, produced_by in evaluations:
            path = staging / "evaluations" / f"{name}.json"
            _write_text(path, _json_text(evaluation))
            references.append(
                _available_reference(
                    root,
                    path,
                    artifact_id=f"evaluation.{name}",
                    artifact_type="evaluation",
                    required=True,
                    produced_by=produced_by,
                )
            )

        for index, snapshot in enumerate(report.input_snapshot.prompt_snapshots):
            path = (
                staging
                / "prompts"
                / "baseline"
                / f"{index:03d}-{_safe_prompt_name(snapshot.field_name)}.md"
            )
            _write_text(path, snapshot.content)
            references.append(
                _available_reference(
                    root,
                    path,
                    artifact_id=f"prompt.baseline.{snapshot.field_name}",
                    artifact_type="prompt",
                    required=True,
                    produced_by="baseline_train",
                )
            )

        for index, (field_name, content) in enumerate(report.candidate.prompts.items()):
            path = (
                staging
                / "prompts"
                / "candidate"
                / f"{index:03d}-{_safe_prompt_name(field_name)}.md"
            )
            _write_text(path, content)
            references.append(
                _available_reference(
                    root,
                    path,
                    artifact_id=f"prompt.candidate.{field_name}",
                    artifact_type="prompt",
                    required=True,
                    produced_by="candidate_generation",
                )
            )

        input_specs = [
            (
                "input.pipeline_config",
                Path(report.input_snapshot.pipeline_config_path),
                report.input_snapshot.pipeline_config_sha256,
                "pipeline_config.json",
                "baseline_train",
            ),
            (
                "input.optimizer_config",
                Path(report.input_snapshot.optimizer_config_path),
                report.input_snapshot.optimizer_config_sha256,
                "optimizer_config.json",
                "candidate_generation",
            ),
            (
                "input.train_evalset",
                Path(report.input_snapshot.train_evalset_path),
                report.input_snapshot.train_evalset_sha256,
                "train_evalset.json",
                "baseline_train",
            ),
            (
                "input.validation_evalset",
                Path(report.input_snapshot.validation_evalset_path),
                report.input_snapshot.validation_evalset_sha256,
                "validation_evalset.json",
                "baseline_validation",
            ),
        ]
        if (
            isinstance(report.candidate, TraceCandidateProposal)
            and report.input_snapshot.trace_inputs is not None
        ):
            trace = report.input_snapshot.trace_inputs.scenarios[
                report.candidate.scenario
            ]
            input_specs.extend(
                [
                    (
                        "input.trace.candidate_train",
                        Path(trace.train_evalset_path),
                        trace.train_evalset_sha256,
                        "candidate_train_trace.json",
                        "candidate_train",
                    ),
                    (
                        "input.trace.candidate_validation",
                        Path(trace.validation_evalset_path),
                        trace.validation_evalset_sha256,
                        "candidate_validation_trace.json",
                        "candidate_validation",
                    ),
                ]
            )
        for artifact_id, source, expected_hash, destination_name, produced_by in input_specs:
            if copy_input_files:
                content_validator = (
                    _validate_optimizer_config_for_copy
                    if artifact_id == "input.optimizer_config"
                    else None
                )
                references.append(
                    _copy_input(
                        root=root,
                        staging=staging,
                        source=source,
                        expected_sha256=expected_hash,
                        destination_name=destination_name,
                        artifact_id=artifact_id,
                        produced_by=produced_by,
                        content_validator=content_validator,
                    )
                )
            else:
                references.append(
                    _unavailable_input_reference(
                        artifact_id=artifact_id,
                        produced_by=produced_by,
                    )
                )

        for relative in native_paths:
            native_path = root / relative
            if native_path.name == "optimizer.runtime.json":
                _validate_optimizer_config_for_copy(native_path)
            references.append(
                _available_reference(
                    root,
                    native_path,
                    artifact_id=f"optimizer_native.{relative}",
                    artifact_type="optimizer_native",
                    required=False,
                    produced_by="candidate_generation",
                )
            )

        index = ArtifactIndex(
            run_id=report.run_id,
            generated_at=report.finished_at,
            artifacts=references,
        )
        index_path = staging / "artifact_index.json"
        _write_text(index_path, _json_text(index))

        OptimizationReport.model_validate_json(report_json.read_text(encoding="utf-8"))
        validated_index = ArtifactIndex.model_validate_json(
            index_path.read_text(encoding="utf-8")
        )
        _validate_available_references(root, staging, validated_index)

        _rename_directory_no_replace(staging, target)
        staging = None
        return validated_index
    except Exception as exc:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        raise ArtifactWriteError(f"failed to publish report bundle: {exc}") from exc


def write_failure_report(report: FailureReport, *, run_dir: Path) -> Path:
    """Atomically write first-failure evidence without allowing replacement.

    The temporary and target paths are siblings beneath the resolved run
    directory, which keeps the hard-link operation on one filesystem.
    """
    temporary: Path | None = None
    try:
        root = _resolved_run_dir(run_dir)
        target = root / "failure_report.json"
        if target.exists() or target.is_symlink():
            raise ArtifactWriteError(f"failure report already exists: {target}")
        temporary = root / f".failure_report.tmp-{uuid4().hex}"
        _write_text(temporary, _json_text(report))
        FailureReport.model_validate_json(temporary.read_text(encoding="utf-8"))
        try:
            os.link(temporary, target)
        except FileExistsError as exc:
            raise ArtifactWriteError(f"failure report already exists: {target}") from exc
        temporary.unlink()
        temporary = None
        return target
    except Exception as exc:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise ArtifactWriteError(f"failed to write failure report: {exc}") from exc
