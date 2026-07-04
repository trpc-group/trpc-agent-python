"""Configuration validation for the eval/optimize loop example."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

from .schemas import EvalCase


@dataclass(frozen=True)
class GateConfig:
    min_val_score_improvement: float = 0.01
    allow_new_hard_fail: bool = False
    protected_case_ids: list[str] = field(default_factory=list)
    max_score_drop_per_case: float = 0.0
    max_total_cost: float = 1.0
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "min_val_score_improvement": self.min_val_score_improvement,
            "allow_new_hard_fail": self.allow_new_hard_fail,
            "protected_case_ids": list(self.protected_case_ids),
            "max_score_drop_per_case": self.max_score_drop_per_case,
            "max_total_cost": self.max_total_cost,
        }
        data.update(self.extras)
        return data


@dataclass(frozen=True)
class OptimizerConfig:
    seed: int = 91
    optimizer: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    gate: GateConfig = field(default_factory=GateConfig)
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "seed": self.seed,
            "optimizer": dict(self.optimizer),
            "metrics": dict(self.metrics),
            "gate": self.gate.to_dict(),
        }
        data.update(self.extras)
        return data


def parse_optimizer_config(payload: dict[str, Any], *, path: str | Path) -> OptimizerConfig:
    path_text = str(path)
    allowed = {"seed", "optimizer", "metrics", "gate"}
    extras = {key: value for key, value in payload.items() if key not in allowed}

    seed = payload.get("seed", 91)
    if not isinstance(seed, int):
        raise ValueError(f"{path_text}: field 'seed' must be an integer")

    optimizer = payload.get("optimizer", {})
    if not isinstance(optimizer, dict):
        raise ValueError(f"{path_text}: field 'optimizer' must be an object")

    metrics = payload.get("metrics", {})
    if not isinstance(metrics, dict):
        raise ValueError(f"{path_text}: field 'metrics' must be an object")

    gate_payload = payload.get("gate", {})
    if not isinstance(gate_payload, dict):
        raise ValueError(f"{path_text}: field 'gate' must be an object")

    gate = _parse_gate_config(gate_payload, path=path_text)
    return OptimizerConfig(
        seed=seed,
        optimizer=dict(optimizer),
        metrics=dict(metrics),
        gate=gate,
        extras=extras,
    )


def validate_inputs(
    *,
    train_path: str | Path,
    val_path: str | Path,
    optimizer_config_path: str | Path,
    train_cases: list[EvalCase],
    validation_cases: list[EvalCase],
    config: OptimizerConfig,
) -> None:
    train_resolved = Path(train_path).resolve()
    val_resolved = Path(val_path).resolve()
    if train_resolved == val_resolved:
        raise ValueError(f"{train_path}: train and validation evalset paths must be different")

    _validate_cases(train_cases, split="train", path=train_path)
    _validate_cases(validation_cases, split="validation", path=val_path)
    if len(train_cases) < 3:
        raise ValueError(f"{train_path}: train evalset must contain at least 3 cases")
    if len(validation_cases) < 3:
        raise ValueError(f"{val_path}: validation evalset must contain at least 3 cases")

    validation_ids = {case.case_id for case in validation_cases}
    missing_protected = [
        case_id
        for case_id in config.gate.protected_case_ids
        if case_id not in validation_ids
    ]
    if missing_protected:
        raise ValueError(
            f"{optimizer_config_path}: field 'gate.protected_case_ids' references missing validation cases: "
            f"{missing_protected}"
        )


def _parse_gate_config(payload: dict[str, Any], *, path: str) -> GateConfig:
    allowed = {
        "min_val_score_improvement",
        "allow_new_hard_fail",
        "protected_case_ids",
        "max_score_drop_per_case",
        "max_total_cost",
    }
    extras = {key: value for key, value in payload.items() if key not in allowed}

    min_val = payload.get("min_val_score_improvement", 0.01)
    if not isinstance(min_val, (int, float)) or min_val < 0 or min_val > 1:
        raise ValueError(f"{path}: field 'gate.min_val_score_improvement' must be a number between 0 and 1")

    allow_new_hard_fail = payload.get("allow_new_hard_fail", False)
    if not isinstance(allow_new_hard_fail, bool):
        raise ValueError(f"{path}: field 'gate.allow_new_hard_fail' must be a boolean")

    protected_case_ids = payload.get("protected_case_ids", [])
    if not isinstance(protected_case_ids, list) or not all(isinstance(item, str) for item in protected_case_ids):
        raise ValueError(f"{path}: field 'gate.protected_case_ids' must be a list of strings")

    max_drop = payload.get("max_score_drop_per_case", 0.0)
    if not isinstance(max_drop, (int, float)) or max_drop < 0:
        raise ValueError(f"{path}: field 'gate.max_score_drop_per_case' must be a non-negative number")

    max_total_cost = payload.get("max_total_cost", 1.0)
    if not isinstance(max_total_cost, (int, float)) or max_total_cost < 0:
        raise ValueError(f"{path}: field 'gate.max_total_cost' must be a non-negative number")

    return GateConfig(
        min_val_score_improvement=float(min_val),
        allow_new_hard_fail=allow_new_hard_fail,
        protected_case_ids=list(protected_case_ids),
        max_score_drop_per_case=float(max_drop),
        max_total_cost=float(max_total_cost),
        extras=extras,
    )


def _validate_cases(cases: list[EvalCase], *, split: str, path: str | Path) -> None:
    seen: set[str] = set()
    for case in cases:
        if case.case_id in seen:
            raise ValueError(f"{path}: duplicate case_id {case.case_id!r} in {split} evalset")
        seen.add(case.case_id)
        expectation_type = case.expectation.get("type")
        if not isinstance(expectation_type, str):
            raise ValueError(f"{path}: case {case.case_id!r} field 'expectation.type' must be a string")
