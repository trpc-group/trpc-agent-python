from __future__ import annotations

import json
from pathlib import Path

import pytest

from examples.optimization.eval_optimize_loop.pipeline.audit import (
    write_environment_snapshot,
    write_input_snapshot,
)
from examples.optimization.eval_optimize_loop.pipeline.config import load_pipeline_config
from trpc_agent_sdk.evaluation import TargetPrompt


def _write_evalset(path: Path, *ids: str) -> None:
    path.write_text(
        json.dumps({"eval_set_id": path.stem, "eval_cases": [{"eval_id": eval_id} for eval_id in ids]}),
        encoding="utf-8",
    )


def _full_sdk_sections() -> dict[str, object]:
    return {
        "evaluate": {"metrics": [{"metric_name": "quality", "threshold": 0.8}]},
        "optimize": {
            "algorithm": {
                "name": "gepa_reflective",
                "reflection_lm": {},
                "max_metric_calls": 1,
            }
        },
    }


def _write_config(
    tmp_path: Path,
    *,
    train_ids: tuple[str, ...] = ("train-1", ),
    validation_ids: tuple[str, ...] = ("validation-1", ),
    pipeline_overrides: dict[str, object] | None = None,
    sdk_sections: bool = False,
) -> Path:
    _write_evalset(tmp_path / "train.evalset.json", *train_ids)
    _write_evalset(tmp_path / "validation.evalset.json", *validation_ids)
    pipeline: dict[str, object] = {
        "datasets": {
            "train_path": "train.evalset.json",
            "validation_path": "validation.evalset.json",
        },
        "metric_weights": {"quality": 1.0},
        "metric_floors": {"quality": 0.8},
    }
    pipeline.update(pipeline_overrides or {})
    payload: dict[str, object] = {"pipeline": pipeline}
    if sdk_sections:
        payload.update(_full_sdk_sections())
    else:
        payload["evaluate"] = {"metrics": [{"metric_name": "quality", "threshold": 0.8}]}
    config_path = tmp_path / "optimizer.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    return config_path


def test_fake_mode_loads_without_live_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    for name in ("TRPC_AGENT_API_KEY", "TRPC_AGENT_BASE_URL", "TRPC_AGENT_MODEL_NAME"):
        monkeypatch.delenv(name, raising=False)

    config = load_pipeline_config(config_path, mode="fake")

    assert config.sdk_config is None
    assert config.pipeline.datasets.train_path.name == "train.evalset.json"


def test_live_mode_lists_all_missing_named_environment_variables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, sdk_sections=True)
    for name in ("TRPC_AGENT_API_KEY", "TRPC_AGENT_BASE_URL", "TRPC_AGENT_MODEL_NAME"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError, match="TRPC_AGENT_API_KEY.*TRPC_AGENT_BASE_URL.*TRPC_AGENT_MODEL_NAME"):
        load_pipeline_config(config_path, mode="live")


def test_live_mode_validates_default_dataset_paths_when_credentials_are_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in ("TRPC_AGENT_API_KEY", "TRPC_AGENT_BASE_URL", "TRPC_AGENT_MODEL_NAME"):
        monkeypatch.setenv(name, "configured")
    config_path = tmp_path / "optimizer.json"
    config_path.write_text(
        json.dumps(
            {
                **_full_sdk_sections(),
                "pipeline": {"metric_weights": {"quality": 1.0}, "metric_floors": {"quality": 0.8}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="train dataset does not exist"):
        load_pipeline_config(config_path, mode="live")


@pytest.mark.parametrize(
    ("pipeline_overrides", "train_ids", "validation_ids", "message"),
    [
        ({"datasets": {"train_path": "missing.json", "validation_path": "validation.evalset.json"}}, ("train-1", ), ("validation-1", ), "train dataset"),
        ({"datasets": {"train_path": "train.evalset.json", "validation_path": "train.evalset.json"}}, ("train-1", ), ("validation-1", ), "different"),
        ({}, ("duplicate", "duplicate"), ("validation-1", ), "duplicate"),
        ({}, ("shared", ), ("shared", ), "shared"),
        ({"gate": {"critical_case_ids": ["missing-critical"]}}, ("train-1", ), ("validation-1", ), "critical"),
    ],
)
def test_dataset_validation_rejects_invalid_inputs(
    tmp_path: Path,
    pipeline_overrides: dict[str, object],
    train_ids: tuple[str, ...],
    validation_ids: tuple[str, ...],
    message: str,
) -> None:
    config_path = _write_config(
        tmp_path,
        pipeline_overrides=pipeline_overrides,
        train_ids=train_ids,
        validation_ids=validation_ids,
    )

    with pytest.raises(ValueError, match=message):
        load_pipeline_config(config_path, mode="fake")


@pytest.mark.parametrize(
    ("pipeline_overrides", "message"),
    [
        ({"metric_weights": {"quality": -0.1}}, "non-negative"),
        ({"metric_weights": {"quality": 0.0}}, "positive"),
        ({"metric_floors": {"unknown": 0.5}}, "unknown metric"),
    ],
)
def test_metric_validation_rejects_invalid_weights_and_floors(
    tmp_path: Path, pipeline_overrides: dict[str, object], message: str
) -> None:
    config_path = _write_config(tmp_path, pipeline_overrides=pipeline_overrides)

    with pytest.raises(ValueError, match=message):
        load_pipeline_config(config_path, mode="trace")


def test_metric_validation_rejects_nan_weight_from_json(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, pipeline_overrides={"metric_weights": {"quality": float("nan")}})

    assert "NaN" in config_path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="finite"):
        load_pipeline_config(config_path, mode="fake")


def test_input_snapshot_has_stable_digests_and_redacts_secrets(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["evaluate"]["api_key"] = "literal-secret"
    raw["evaluate"]["nested_token"] = "other-secret"
    raw["evaluate"].update(
        {
            "access_key": "access-secret",
            "client_secret": "client-secret",
            "credential": "credential-secret",
            "password": "password-secret",
        }
    )
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Support users safely.", encoding="utf-8")
    target_prompt = TargetPrompt().add_path("system", str(prompt_path))
    config = load_pipeline_config(config_path, mode="fake")

    first = write_input_snapshot(config, target_prompt, tmp_path / "first")
    second = write_input_snapshot(config, target_prompt, tmp_path / "second")

    assert first.config_digest == second.config_digest
    assert first.train_dataset_digest == second.train_dataset_digest
    assert first.validation_dataset_digest == second.validation_dataset_digest
    assert first.prompt_digest == second.prompt_digest
    snapshot = (tmp_path / "first" / "input.snapshot.json").read_text(encoding="utf-8")
    for secret in ("literal-secret", "other-secret", "access-secret", "client-secret", "credential-secret", "password-secret"):
        assert secret not in snapshot
    assert "***REDACTED***" in snapshot


def test_environment_snapshot_excludes_arbitrary_environment_variables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARBITRARY_SECRET", "must-not-appear")

    snapshot_path = write_environment_snapshot("trace", 42, tmp_path)

    snapshot = snapshot_path.read_text(encoding="utf-8")
    assert "ARBITRARY_SECRET" not in snapshot
    assert "must-not-appear" not in snapshot
    assert json.loads(snapshot)["mode"] == "trace"
