# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""First-stage pipeline preparation: validate inputs and isolate prompts."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from trpc_agent_sdk.evaluation import EvalSet
from trpc_agent_sdk.evaluation import OptimizeConfigFile
from trpc_agent_sdk.evaluation import TargetPrompt
from trpc_agent_sdk.evaluation import load_optimize_config

from .config import PipelineConfig
from .config import load_pipeline_config
from .prompt_workspace import PromptWorkspaceError
from .prompt_workspace import resolve_inside_example_root
from .prompt_workspace import stage_prompt_workspace
from .prompt_workspace import validate_prompt_sources
from .schemas import InputSnapshot
from .schemas import WorkspaceSnapshot


class PipelinePreparationError(ValueError):
    """The example cannot safely prepare an evaluation/optimization run."""


@dataclass(frozen=True)
class PreparedRun:
    """Validated inputs and isolated prompts handed to the next pipeline phase."""

    config: PipelineConfig
    optimizer_config: OptimizeConfigFile
    input_snapshot: InputSnapshot
    workspace: WorkspaceSnapshot
    source_target: TargetPrompt
    working_target: TargetPrompt
    example_root: Path


_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _load_evalset(path: Path, label: str) -> EvalSet:
    if not path.is_file():
        raise PipelinePreparationError(f"{label} must be a file: {path}")
    try:
        return EvalSet.model_validate_json(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise PipelinePreparationError(f"{label} is not UTF-8: {path}") from exc
    except Exception as exc:
        raise PipelinePreparationError(f"{label} is not a valid EvalSet: {path}: {exc}") from exc


def _validate_eval_case_ids(train: EvalSet, validation: EvalSet, config: PipelineConfig) -> None:
    train_ids = [case.eval_id for case in train.eval_cases]
    validation_ids = [case.eval_id for case in validation.eval_cases]
    for label, ids in (("train", train_ids), ("validation", validation_ids)):
        if len(ids) != len(set(ids)):
            raise PipelinePreparationError(f"{label} evalset contains duplicate eval_id values")
    if set(train_ids) & set(validation_ids):
        raise PipelinePreparationError("train and validation evalsets must not share eval_id values")

    known_ids = set(train_ids) | set(validation_ids)
    labels = set(config.case_labels.hard_case_ids) | set(config.case_labels.critical_case_ids)
    unknown = sorted(labels - known_ids)
    if unknown:
        raise PipelinePreparationError(f"case_labels reference unknown eval_id values: {unknown}")


def _validate_gate_metrics(config: PipelineConfig, optimizer_config: object) -> None:
    required = config.gate.required_metrics
    if not isinstance(required, list):
        return
    available = {metric.metric_name for metric in optimizer_config.evaluate.get_eval_metrics()}
    unknown = sorted(set(required) - available)
    if unknown:
        raise PipelinePreparationError(
            f"gate.required_metrics references unknown metrics {unknown}; available metrics: {sorted(available)}")


def _resolve_inputs(example_root: Path, config: PipelineConfig) -> tuple[Path, Path, Path]:
    train_path = resolve_inside_example_root(example_root, config.inputs.train_evalset, "train_evalset")
    validation_path = resolve_inside_example_root(example_root, config.inputs.validation_evalset, "validation_evalset")
    optimizer_path = resolve_inside_example_root(example_root, config.inputs.optimizer_config, "optimizer_config")
    if train_path == validation_path:
        raise PipelinePreparationError("train_evalset and validation_evalset must be different files")
    if not optimizer_path.is_file():
        raise PipelinePreparationError(f"optimizer_config must be a file: {optimizer_path}")
    return train_path, validation_path, optimizer_path


def _validate_run_id(run_id: str) -> str:
    if not _RUN_ID_RE.fullmatch(run_id):
        raise PipelinePreparationError("run_id may contain only letters, numbers, underscores, and hyphens")
    return run_id


def _new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%S_%fZ")


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def prepare_run(pipeline_config_path: str | Path, *, run_id: str | None = None) -> PreparedRun:
    """Prepare a run without evaluating, optimizing, reporting, or writing a source prompt.

    All configuration and input validation completes before a staging directory
    is created.  The final run directory appears only through an atomic rename,
    and an exception removes the staging directory.  This keeps failed setup
    from looking like a runnable or audited pipeline result.
    """
    config_path = Path(pipeline_config_path).resolve()
    config = load_pipeline_config(config_path)
    example_root = config_path.parent

    train_path, validation_path, optimizer_path = _resolve_inputs(example_root, config)
    train_evalset = _load_evalset(train_path, "train_evalset")
    validation_evalset = _load_evalset(validation_path, "validation_evalset")
    _validate_eval_case_ids(train_evalset, validation_evalset, config)

    try:
        optimizer_config = load_optimize_config(str(optimizer_path))
    except Exception as exc:
        raise PipelinePreparationError(f"optimizer_config is invalid: {optimizer_path}: {exc}") from exc
    if not optimizer_config.evaluate.get_eval_metrics():
        raise PipelinePreparationError("optimizer_config must define at least one evaluation metric")
    if optimizer_config.evaluate.num_runs < 1:
        raise PipelinePreparationError("optimizer_config evaluate.num_runs must be at least 1")
    if optimizer_config.optimize.eval_case_parallelism < 1:
        raise PipelinePreparationError("optimizer_config optimize.eval_case_parallelism must be at least 1")
    _validate_gate_metrics(config, optimizer_config)

    try:
        prompt_sources = validate_prompt_sources(example_root, config.prompts)
        runs_dir = resolve_inside_example_root(example_root, config.run.runs_dir, "runs_dir")
    except PromptWorkspaceError as exc:
        raise PipelinePreparationError(str(exc)) from exc

    configured_run_id = run_id if run_id is not None else config.run.run_id
    selected_run_id = _validate_run_id(configured_run_id or _new_run_id())
    runs_dir.mkdir(parents=True, exist_ok=True)
    final_run_dir = runs_dir / selected_run_id
    if final_run_dir.exists():
        raise FileExistsError(f"run directory already exists: {final_run_dir}")

    staging_run_dir = runs_dir / f".{selected_run_id}.tmp-{uuid4().hex}"
    try:
        staging_run_dir.mkdir()
        prompt_snapshots, source_target, working_target = stage_prompt_workspace(
            example_root=example_root,
            staging_run_dir=staging_run_dir,
            final_run_dir=final_run_dir,
            prompts=config.prompts,
            sources=prompt_sources,
        )
        workspace_dir = final_run_dir / "workspace"
        workspace = WorkspaceSnapshot(
            run_id=selected_run_id,
            run_dir=str(final_run_dir),
            workspace_dir=str(workspace_dir),
            prompts_dir=str(workspace_dir / "prompts"),
        )
        input_snapshot = InputSnapshot(
            pipeline_config_path=str(config_path),
            pipeline_config_sha256=_file_sha256(config_path),
            optimizer_config_path=str(optimizer_path),
            optimizer_config_sha256=_file_sha256(optimizer_path),
            train_evalset_path=str(train_path),
            train_evalset_sha256=_file_sha256(train_path),
            validation_evalset_path=str(validation_path),
            validation_evalset_sha256=_file_sha256(validation_path),
            prompt_snapshots=prompt_snapshots,
            seed=config.run.seed,
        )
        prepared = PreparedRun(
            config=config,
            optimizer_config=optimizer_config,
            input_snapshot=input_snapshot,
            workspace=workspace,
            source_target=source_target,
            working_target=working_target,
            example_root=example_root,
        )
        staging_run_dir.replace(final_run_dir)
        return prepared
    except BaseException:
        shutil.rmtree(staging_run_dir, ignore_errors=True)
        raise
