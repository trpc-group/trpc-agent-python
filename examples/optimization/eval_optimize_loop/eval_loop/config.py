"""Configuration validation for the eval/optimize loop example."""

from __future__ import annotations

import math
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

from .artifacts import validate_distinct_file_paths
from .schemas import EvalCase


@dataclass(frozen=True)
class GateConfig:
    min_val_score_improvement: float = 0.01
    allow_new_hard_fail: bool = False
    protected_case_ids: list[str] = field(default_factory=list)
    max_score_drop_per_case: float = 0.0
    max_total_cost: float | None = 1.0
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

    seed = resolve_effective_seed(payload, path=path)

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


def resolve_effective_seed(
    payload: dict[str, Any],
    *,
    path: str | Path,
    default: int = 91,
    strict_legacy: bool = True,
) -> int:
    """Resolve the legacy or official optimizer seed without audit drift.

    The official SDK schema owns ``optimize.algorithm.seed``.  A non-integer
    top-level ``seed`` may be unrelated SDK metadata and is ignored when the
    official nested seed is present.  Two integer seed declarations must agree.
    """

    path_text = str(path)
    nested_present = False
    nested_seed: Any = None
    optimize = payload.get("optimize")
    if isinstance(optimize, dict):
        algorithm = optimize.get("algorithm")
        if isinstance(algorithm, dict) and "seed" in algorithm:
            nested_present = True
            nested_seed = algorithm["seed"]

    top_present = "seed" in payload
    top_seed = payload.get("seed")
    if nested_present:
        nested = _validated_seed(
            nested_seed,
            field_name=f"{path_text}: field 'optimize.algorithm.seed'",
        )
        if top_present and isinstance(top_seed, int) and not isinstance(top_seed, bool):
            if top_seed != nested:
                raise ValueError(f"{path_text}: conflicting seed values: top-level seed={top_seed}, "
                                 f"optimize.algorithm.seed={nested}")
        return nested

    if not top_present:
        return default
    if not strict_legacy and (isinstance(top_seed, bool) or not isinstance(top_seed, int)):
        return default
    return _validated_seed(top_seed, field_name=f"{path_text}: field 'seed'")


def _validated_seed(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def validate_inputs(
    *,
    train_path: str | Path,
    val_path: str | Path,
    optimizer_config_path: str | Path,
    train_cases: list[EvalCase],
    validation_cases: list[EvalCase],
    config: OptimizerConfig,
) -> None:
    validate_distinct_file_paths(
        {
            "train": train_path,
            "validation": val_path
        },
        context="train and validation evalset paths",
    )

    _validate_cases(train_cases, split="train", path=train_path)
    _validate_cases(validation_cases, split="validation", path=val_path)
    if len(train_cases) < 3:
        raise ValueError(f"{train_path}: train evalset must contain at least 3 cases")
    if len(validation_cases) < 3:
        raise ValueError(f"{val_path}: validation evalset must contain at least 3 cases")

    validation_ids = {case.case_id for case in validation_cases}
    missing_protected = [case_id for case_id in config.gate.protected_case_ids if case_id not in validation_ids]
    if missing_protected:
        raise ValueError(
            f"{optimizer_config_path}: field 'gate.protected_case_ids' references missing validation cases: "
            f"{missing_protected}")


def _parse_gate_config(payload: dict[str, Any], *, path: str) -> GateConfig:
    allowed = {
        "min_val_score_improvement",
        "allow_new_hard_fail",
        "protected_case_ids",
        "max_score_drop_per_case",
        "max_total_cost",
    }
    extras = {key: value for key, value in payload.items() if key not in allowed}

    min_val = _finite_number(
        payload.get("min_val_score_improvement", 0.01),
        f"{path}: field 'gate.min_val_score_improvement'",
        0.0,
        1.0,
    )

    allow_new_hard_fail = payload.get("allow_new_hard_fail", False)
    if not isinstance(allow_new_hard_fail, bool):
        raise ValueError(f"{path}: field 'gate.allow_new_hard_fail' must be a boolean")

    protected_case_ids = payload.get("protected_case_ids", [])
    if not isinstance(protected_case_ids, list) or not all(isinstance(item, str) for item in protected_case_ids):
        raise ValueError(f"{path}: field 'gate.protected_case_ids' must be a list of strings")

    max_drop = _finite_number(
        payload.get("max_score_drop_per_case", 0.0),
        f"{path}: field 'gate.max_score_drop_per_case'",
        0.0,
    )

    max_total_cost_value = payload.get("max_total_cost", 1.0)
    max_total_cost = (None if max_total_cost_value is None else _finite_number(
        max_total_cost_value,
        f"{path}: field 'gate.max_total_cost'",
        0.0,
    ))

    return GateConfig(
        min_val_score_improvement=min_val,
        allow_new_hard_fail=allow_new_hard_fail,
        protected_case_ids=list(protected_case_ids),
        max_score_drop_per_case=max_drop,
        max_total_cost=max_total_cost,
        extras=extras,
    )


def _finite_number(
    value: Any,
    field_name: str,
    minimum: float,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{field_name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be a finite number")
    if number < minimum:
        raise ValueError(f"{field_name} must be a finite number greater than or equal to {minimum:g}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{field_name} must be a finite number between {minimum:g} and {maximum:g}")
    return number


def _validate_cases(cases: list[EvalCase], *, split: str, path: str | Path) -> None:
    seen: set[str] = set()
    for case in cases:
        if case.case_id in seen:
            raise ValueError(f"{path}: duplicate case_id {case.case_id!r} in {split} evalset")
        seen.add(case.case_id)
        expectation_type = case.expectation.get("type")
        if not isinstance(expectation_type, str):
            raise ValueError(f"{path}: case {case.case_id!r} field 'expectation.type' must be a string")
