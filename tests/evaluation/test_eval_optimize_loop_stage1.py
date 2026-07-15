# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Offline tests for stage-one eval/optimization pipeline preparation."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from examples.optimization.eval_optimize_loop.config import PipelineConfig
from examples.optimization.eval_optimize_loop.config import load_pipeline_config
from examples.optimization.eval_optimize_loop.pipeline import PipelinePreparationError
from examples.optimization.eval_optimize_loop.pipeline import prepare_run
from examples.optimization.eval_optimize_loop.prompt_workspace import SourcePromptDriftError
from examples.optimization.eval_optimize_loop.prompt_workspace import verify_source_hashes
from examples.optimization.eval_optimize_loop.schemas import ObservableValue


_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE = _REPO_ROOT / "examples" / "optimization" / "eval_optimize_loop"


def _copy_example(tmp_path: Path) -> Path:
    target = tmp_path / "eval_optimize_loop"
    shutil.copytree(_EXAMPLE, target, ignore=shutil.ignore_patterns("runs", "__pycache__"))
    return target


def _read_config(root: Path) -> dict:
    return json.loads((root / "pipeline.json").read_text(encoding="utf-8"))


def _write_config(root: Path, payload: dict) -> Path:
    path = root / "pipeline.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_evalset(root: Path, relative: str, eval_set_id: str, case_ids: list[str]) -> None:
    payload = {
        "eval_set_id": eval_set_id,
        "eval_cases": [
            {
                "eval_id": case_id,
                "conversation": [
                    {
                        "invocation_id": case_id,
                        "user_content": {"parts": [{"text": "input"}], "role": "user"},
                        "final_response": {"parts": [{"text": "output"}], "role": "model"},
                    }
                ],
            }
            for case_id in case_ids
        ],
    }
    (root / relative).write_text(json.dumps(payload), encoding="utf-8")


def test_pipeline_config_loads_complete_example_and_camel_case(tmp_path: Path):
    root = _copy_example(tmp_path)
    config = load_pipeline_config(root / "pipeline.json")
    assert config.config_version == 1
    assert config.execution.mode == "fake"
    assert config.prompts[0].name == "system_prompt"
    assert config.gate.required_metrics == ["final_response_avg_score"]

    payload = _read_config(root)
    payload["configVersion"] = payload.pop("config_version")
    payload["caseLabels"] = payload.pop("case_labels")
    payload["caseLabels"]["hardCaseIds"] = payload["caseLabels"].pop("hard_case_ids")
    camel_path = root / "camel.json"
    camel_path.write_text(json.dumps(payload), encoding="utf-8")
    camel_config = load_pipeline_config(camel_path)
    assert camel_config.case_labels.hard_case_ids == ["val_knowledge_recall"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update({"unexpected": True}),
        lambda payload: payload["execution"].update({"mode": "maybe"}),
        lambda payload: payload["gate"].update({"min_validation_score_delta": -0.01}),
        lambda payload: payload["gate"].update({"severe_case_score_drop": -0.1}),
        lambda payload: payload["gate"].update({"severe_case_score_drop": 1.1}),
        lambda payload: payload["budget"].update({"max_tokens": -1}),
        lambda payload: payload["reporting"].update({"write_json": False, "write_markdown": False}),
        lambda payload: payload.update({"prompts": [{"name": "system_prompt", "callback": "x"}]}),
    ],
)
def test_pipeline_config_rejects_invalid_values(tmp_path: Path, mutate):
    root = _copy_example(tmp_path)
    payload = _read_config(root)
    mutate(payload)
    with pytest.raises(ValidationError):
        PipelineConfig.model_validate(payload)


def test_pipeline_config_rejects_duplicate_prompt_names(tmp_path: Path):
    root = _copy_example(tmp_path)
    payload = _read_config(root)
    payload["prompts"].append({"name": "system_prompt", "path": "prompts/system.md"})
    with pytest.raises(ValidationError, match="duplicate field names"):
        PipelineConfig.model_validate(payload)


def test_observable_value_never_treats_unavailable_as_zero():
    unavailable = ObservableValue(status="unavailable", reason="SDK does not expose cost")
    assert unavailable.value is None
    with pytest.raises(ValidationError):
        ObservableValue(status="unavailable", value=0)
    with pytest.raises(ValidationError):
        ObservableValue(status="available")


@pytest.mark.asyncio
async def test_prepare_run_creates_isolated_prompt_workspace(tmp_path: Path):
    root = _copy_example(tmp_path)
    source = root / "prompts" / "system.md"
    baseline = source.read_text(encoding="utf-8")

    prepared = prepare_run(root / "pipeline.json", run_id="stage1_test")

    assert Path(prepared.workspace.run_dir).is_dir()
    assert prepared.source_target.names() == ["system_prompt"]
    assert prepared.working_target.names() == ["system_prompt"]
    source_values = await prepared.source_target.read_all()
    working_values = await prepared.working_target.read_all()
    assert source_values == working_values == {"system_prompt": baseline}
    snapshot = prepared.input_snapshot.prompt_snapshots[0]
    assert snapshot.source_path == str(source.resolve())
    assert snapshot.working_path != snapshot.source_path
    assert len(snapshot.sha256) == 64
    assert len(prepared.input_snapshot.pipeline_config_sha256) == 64
    assert len(prepared.input_snapshot.optimizer_config_sha256) == 64
    assert len(prepared.input_snapshot.train_evalset_sha256) == 64
    assert len(prepared.input_snapshot.validation_evalset_sha256) == 64
    verify_source_hashes(prepared.input_snapshot.prompt_snapshots)

    await prepared.working_target.write_all({"system_prompt": "candidate"})
    assert source.read_text(encoding="utf-8") == baseline
    assert await prepared.working_target.read_all() == {"system_prompt": "candidate"}

    source.write_text("concurrent source edit", encoding="utf-8")
    with pytest.raises(SourcePromptDriftError, match="system_prompt"):
        verify_source_hashes(prepared.input_snapshot.prompt_snapshots)


def test_prepare_run_does_not_overwrite_existing_run(tmp_path: Path):
    root = _copy_example(tmp_path)
    prepare_run(root / "pipeline.json", run_id="same_run")
    with pytest.raises(FileExistsError, match="already exists"):
        prepare_run(root / "pipeline.json", run_id="same_run")


@pytest.mark.parametrize("label_key", ["hard_case_ids", "critical_case_ids"])
def test_prepare_run_rejects_unknown_case_labels(tmp_path: Path, label_key: str):
    root = _copy_example(tmp_path)
    payload = _read_config(root)
    payload["case_labels"][label_key] = ["missing_case"]
    with pytest.raises(PipelinePreparationError, match="unknown eval_id"):
        prepare_run(_write_config(root, payload), run_id="bad_labels")
    assert not (root / "runs").exists()


def test_prepare_run_rejects_shared_dataset_path(tmp_path: Path):
    root = _copy_example(tmp_path)
    payload = _read_config(root)
    payload["inputs"]["validation_evalset"] = payload["inputs"]["train_evalset"]
    with pytest.raises(PipelinePreparationError, match="different files"):
        prepare_run(_write_config(root, payload), run_id="shared_data")


def test_prepare_run_rejects_duplicate_or_overlapping_eval_ids(tmp_path: Path):
    root = _copy_example(tmp_path)
    _write_evalset(root, "data/train.evalset.json", "train", ["same", "same"])
    with pytest.raises(PipelinePreparationError, match="duplicate eval_id"):
        prepare_run(root / "pipeline.json", run_id="duplicate_case")

    _write_evalset(root, "data/train.evalset.json", "train", ["same"])
    _write_evalset(root, "data/val.evalset.json", "val", ["same"])
    payload = _read_config(root)
    payload["case_labels"] = {}
    with pytest.raises(PipelinePreparationError, match="must not share eval_id"):
        prepare_run(_write_config(root, payload), run_id="overlap_case")


def test_prepare_run_rejects_unknown_required_metric(tmp_path: Path):
    root = _copy_example(tmp_path)
    payload = _read_config(root)
    payload["gate"]["required_metrics"] = ["does_not_exist"]
    with pytest.raises(PipelinePreparationError, match="unknown metrics"):
        prepare_run(_write_config(root, payload), run_id="bad_metric")


def test_prepare_run_rejects_path_escape_and_non_utf8_prompt(tmp_path: Path):
    root = _copy_example(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    payload = _read_config(root)
    payload["prompts"][0]["path"] = "../outside.md"
    with pytest.raises(PipelinePreparationError, match="escapes the example root"):
        prepare_run(_write_config(root, payload), run_id="escape")

    payload["prompts"][0]["path"] = "prompts/binary.md"
    (root / "prompts" / "binary.md").write_bytes(b"\xff\xfe")
    with pytest.raises(PipelinePreparationError, match="not UTF-8"):
        prepare_run(_write_config(root, payload), run_id="binary")


def test_prepare_run_rejects_duplicate_prompt_paths(tmp_path: Path):
    root = _copy_example(tmp_path)
    payload = _read_config(root)
    payload["prompts"].append({"name": "router_prompt", "path": "prompts/system.md"})
    with pytest.raises(PipelinePreparationError, match="multiple prompt fields"):
        prepare_run(_write_config(root, payload), run_id="duplicate_path")


def test_prepare_run_removes_staging_workspace_on_failure(tmp_path: Path, monkeypatch):
    root = _copy_example(tmp_path)

    def fail_staging(**kwargs):
        (kwargs["staging_run_dir"] / "workspace").mkdir()
        raise RuntimeError("injected staging failure")

    monkeypatch.setattr(
        "examples.optimization.eval_optimize_loop.pipeline.stage_prompt_workspace",
        fail_staging,
    )
    with pytest.raises(RuntimeError, match="injected"):
        prepare_run(root / "pipeline.json", run_id="failed_run")

    runs_dir = root / "runs"
    assert not (runs_dir / "failed_run").exists()
    assert list(runs_dir.iterdir()) == []
