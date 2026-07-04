from __future__ import annotations

import json
from pathlib import Path

import pytest

from examples.optimization.eval_optimize_loop.eval_loop.config import parse_optimizer_config
from examples.optimization.eval_optimize_loop.eval_loop.config import validate_inputs
from examples.optimization.eval_optimize_loop.eval_loop.loader import load_eval_cases
from examples.optimization.eval_optimize_loop.eval_loop.loader import load_optimizer_config


def test_optimizer_config_defaults_metrics_and_gate(tmp_path: Path):
    path = tmp_path / "optimizer.json"
    path.write_text(json.dumps({"seed": 7}), encoding="utf-8")

    config = load_optimizer_config(path)

    assert config.seed == 7
    assert config.metrics == {}
    assert config.gate.min_val_score_improvement == 0.01


def test_optimizer_config_rejects_bad_gate_type(tmp_path: Path):
    path = tmp_path / "optimizer.json"
    payload = {"gate": {"allow_new_hard_fail": "no"}}

    with pytest.raises(ValueError, match="gate.allow_new_hard_fail"):
        parse_optimizer_config(payload, path=path)


def test_optimizer_config_rejects_negative_cost(tmp_path: Path):
    path = tmp_path / "optimizer.json"
    payload = {"gate": {"max_total_cost": -1}}

    with pytest.raises(ValueError, match="gate.max_total_cost"):
        parse_optimizer_config(payload, path=path)


def test_validate_inputs_rejects_same_train_val_path(tmp_path: Path):
    eval_path = _write_evalset(tmp_path / "train.evalset.json", "train", ["a", "b", "c"])
    config = parse_optimizer_config({"gate": {}}, path=tmp_path / "optimizer.json")
    cases = load_eval_cases(eval_path, split="train")

    with pytest.raises(ValueError, match="must be different"):
        validate_inputs(
            train_path=eval_path,
            val_path=eval_path,
            optimizer_config_path=tmp_path / "optimizer.json",
            train_cases=cases,
            validation_cases=cases,
            config=config,
        )


def test_validate_inputs_rejects_duplicate_case_ids(tmp_path: Path):
    train_path = _write_evalset(tmp_path / "train.evalset.json", "train", ["a", "a", "c"])
    val_path = _write_evalset(tmp_path / "val.evalset.json", "validation", ["v1", "v2", "v3"])
    config = parse_optimizer_config({"gate": {}}, path=tmp_path / "optimizer.json")

    with pytest.raises(ValueError, match="duplicate case_id"):
        validate_inputs(
            train_path=train_path,
            val_path=val_path,
            optimizer_config_path=tmp_path / "optimizer.json",
            train_cases=load_eval_cases(train_path, split="train"),
            validation_cases=load_eval_cases(val_path, split="validation"),
            config=config,
        )


def test_validate_inputs_rejects_missing_protected_case(tmp_path: Path):
    train_path = _write_evalset(tmp_path / "train.evalset.json", "train", ["a", "b", "c"])
    val_path = _write_evalset(tmp_path / "val.evalset.json", "validation", ["v1", "v2", "v3"])
    config = parse_optimizer_config(
        {"gate": {"protected_case_ids": ["missing"]}},
        path=tmp_path / "optimizer.json",
    )

    with pytest.raises(ValueError, match="gate.protected_case_ids"):
        validate_inputs(
            train_path=train_path,
            val_path=val_path,
            optimizer_config_path=tmp_path / "optimizer.json",
            train_cases=load_eval_cases(train_path, split="train"),
            validation_cases=load_eval_cases(val_path, split="validation"),
            config=config,
        )


def _write_evalset(path: Path, split: str, ids: list[str]) -> Path:
    payload = {
        "split": split,
        "cases": [
            {
                "id": case_id,
                "input": "Return JSON",
                "expectation": {"type": "json", "required_keys": ["answer"], "expected_values": {"answer": case_id}},
            }
            for case_id in ids
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path
