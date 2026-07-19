# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Safe and atomic materialization of optimization report artifacts."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Callable, Literal, TypeAlias
from uuid import uuid4

from pydantic import BaseModel

from .report_builder import render_optimization_markdown
from .schemas import ArtifactIndex, ArtifactReference, FailureReport
from .schemas import OptimizationReport, ReportPhase


ArtifactType: TypeAlias = Literal[
    "input",
    "prompt",
    "evaluation",
    "candidate",
    "optimizer_native",
    "report",
]

_INPUT_COPY_DISABLED = "artifacts.copy_input_files=false"
_SENSITIVE_CONFIG_KEYS = {"apikey", "authorization", "baseurl"}
_APPROVED_SENSITIVE_VALUES = {
    "",
    "${TRPC_AGENT_API_KEY}",
    "${TRPC_AGENT_BASE_URL}",
    "fake-not-used-in-offline-mode",
}
_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
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


def _normalized_sensitive_key(key: str) -> str:
    return key.replace("_", "").replace("-", "").casefold()


def _validate_sensitive_config_values(value: object, *, path: str = "$") -> None:
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_sensitive_config_values(item, path=f"{path}[{index}]")
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        item_path = f"{path}.{key}"
        if _normalized_sensitive_key(key) in _SENSITIVE_CONFIG_KEYS:
            if not isinstance(item, str) or item not in _APPROVED_SENSITIVE_VALUES:
                raise ArtifactWriteError(
                    "sensitive optimizer config value is not an approved "
                    f"placeholder: {item_path}"
                )
        else:
            _validate_sensitive_config_values(item, path=item_path)


def _validate_optimizer_config_for_copy(path: Path) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactWriteError(
            f"failed to parse optimizer config snapshot: {path}: {exc}"
        ) from exc
    _validate_sensitive_config_values(payload)


def _target_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _rename_directory_no_replace(source: Path, target: Path) -> None:
    """Atomically publish a directory without replacing an existing target.

    Linux uses renameat2 with RENAME_NOREPLACE. Platforms without that primitive
    use a controlled fallback that rechecks the target immediately before rename.
    """
    if sys.platform.startswith("linux"):
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            renameat2 = libc.renameat2
        except (AttributeError, OSError):
            renameat2 = None
        if renameat2 is not None:
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
                raise ArtifactWriteError(
                    f"report directory already exists: {target}"
                )
            if error_number not in _RENAMEAT2_UNAVAILABLE:
                raise OSError(error_number, os.strerror(error_number), target)

    if _target_exists(target):
        raise ArtifactWriteError(f"report directory already exists: {target}")
    source.rename(target)


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

        input_specs = (
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
    """Atomically write first-failure evidence without allowing replacement."""
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
