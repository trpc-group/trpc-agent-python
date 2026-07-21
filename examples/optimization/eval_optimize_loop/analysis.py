# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Stage 3a orchestration for normalization, attribution, and case diff."""

from __future__ import annotations

from .attribution import attribute_evaluation
from .case_diff import compare_evaluations
from .evaluation_adapter import standardize_snapshot
from .schemas import EvaluationAnalysis
from .schemas import EvaluationSnapshot
from .schemas import ObservableValue
from .schemas import OverfitStatus


def _overfit_status(
    train_delta: ObservableValue,
    validation_delta: ObservableValue,
) -> tuple[OverfitStatus, str]:
    if train_delta.status != "available" or validation_delta.status != "available":
        return "unavailable", "Train or validation score delta is unavailable."
    train_value = float(train_delta.value)
    validation_value = float(validation_delta.value)
    if train_value > 0.0 and validation_value < 0.0:
        return (
            "detected",
            f"Train score improved by {train_value:.6f} while validation regressed by "
            f"{validation_value:.6f}.",
        )
    return (
        "not_detected",
        f"Train score delta is {train_value:.6f}; validation score delta is "
        f"{validation_value:.6f}.",
    )


def build_evaluation_analysis(
    *,
    baseline_train: EvaluationSnapshot,
    baseline_validation: EvaluationSnapshot,
    candidate_train: EvaluationSnapshot,
    candidate_validation: EvaluationSnapshot,
    hard_case_ids: set[str],
    critical_case_ids: set[str],
    severe_case_score_drop: float,
) -> EvaluationAnalysis:
    """Build stage 3a analysis from the four complete evaluation snapshots."""
    normalized_baseline_train = attribute_evaluation(standardize_snapshot(baseline_train))
    normalized_baseline_validation = attribute_evaluation(standardize_snapshot(baseline_validation))
    normalized_candidate_train = attribute_evaluation(standardize_snapshot(candidate_train))
    normalized_candidate_validation = attribute_evaluation(standardize_snapshot(candidate_validation))

    train_diff = compare_evaluations(
        normalized_baseline_train,
        normalized_candidate_train,
        hard_case_ids=hard_case_ids,
        critical_case_ids=critical_case_ids,
        severe_case_score_drop=severe_case_score_drop,
    )
    validation_diff = compare_evaluations(
        normalized_baseline_validation,
        normalized_candidate_validation,
        hard_case_ids=hard_case_ids,
        critical_case_ids=critical_case_ids,
        severe_case_score_drop=severe_case_score_drop,
    )
    overfit_status, overfit_reason = _overfit_status(
        train_diff.score_delta,
        validation_diff.score_delta,
    )
    return EvaluationAnalysis(
        baseline_train=normalized_baseline_train,
        baseline_validation=normalized_baseline_validation,
        candidate_train=normalized_candidate_train,
        candidate_validation=normalized_candidate_validation,
        train_diff=train_diff,
        validation_diff=validation_diff,
        overfit_status=overfit_status,
        overfit_reason=overfit_reason,
    )
