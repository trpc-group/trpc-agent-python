from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from examples.optimization.eval_optimize_loop.eval_loop.attribution import attribute_failure
from examples.optimization.eval_optimize_loop.eval_loop.gate import AcceptanceGate
from examples.optimization.eval_optimize_loop.eval_loop.report import compute_case_deltas
from examples.optimization.eval_optimize_loop.eval_loop.schemas import CaseResult
from examples.optimization.eval_optimize_loop.eval_loop.schemas import CostSummary
from examples.optimization.eval_optimize_loop.eval_loop.schemas import EvalResult
from examples.optimization.eval_optimize_loop.eval_loop.schemas import GateDecision
from examples.optimization.eval_optimize_loop.run_pipeline import DEFAULT_PROMPT
from examples.optimization.eval_optimize_loop.run_pipeline import DEFAULT_TRAIN
from examples.optimization.eval_optimize_loop.run_pipeline import DEFAULT_VAL
from examples.optimization.eval_optimize_loop.run_pipeline import run_pipeline

FIXTURES = Path(__file__).parent / "fixtures"


def test_holdout_gate_fixture_has_ten_unique_scenarios() -> None:
    scenarios = _load_fixture("holdout_gate_cases.json")

    assert len(scenarios) >= 10
    scenario_ids = [str(scenario["id"]) for scenario in scenarios]
    assert len(scenario_ids) == len(set(scenario_ids))
    expected_labels = {bool(scenario["expected"]) for scenario in scenarios}
    assert expected_labels == {False, True}
    logical_inputs = [
        json.dumps(
            {
                key: value
                for key, value in scenario.items() if key not in {"id", "expected"}
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) for scenario in scenarios
    ]
    assert len(logical_inputs) == len(set(logical_inputs))


def test_gate_thresholds_reject_always_false_predictions() -> None:
    scenarios = _load_fixture("holdout_gate_cases.json")
    expected = [bool(scenario["expected"]) for scenario in scenarios]

    assert not _gate_acceptance_thresholds_met(
        expected,
        predictions=[False] * len(expected),
    )


def test_attribution_fixture_has_eight_unique_evidence_inputs() -> None:
    scenarios = _load_fixture("attribution_cases.json")

    assert len(scenarios) >= 8
    evidence_inputs = [(str(scenario["error_code"]), str(scenario["evidence"])) for scenario in scenarios]
    assert len(evidence_inputs) == len(set(evidence_inputs))
    error_codes = [str(scenario["error_code"]) for scenario in scenarios]
    assert len(error_codes) == len(set(error_codes))


def test_holdout_gate_decision_accuracy_is_at_least_eighty_percent() -> None:
    scenarios = _load_fixture("holdout_gate_cases.json")
    expected_labels: list[bool] = []
    predictions: list[bool] = []
    for scenario in scenarios:
        expected_labels.append(bool(scenario["expected"]))
        inputs = {key: value for key, value in scenario.items() if key != "expected"}
        decision = _decision_for_scenario(inputs)
        predictions.append(decision.accepted)

    overall_accuracy, positive_recall, negative_specificity = (_gate_classification_rates(expected_labels, predictions))
    assert overall_accuracy >= 0.80
    assert positive_recall >= 0.80
    assert negative_specificity >= 0.80


def test_independent_attribution_accuracy_is_at_least_seventy_five_percent() -> None:
    scenarios = _load_fixture("attribution_cases.json")
    inputs = [{key: value for key, value in scenario.items() if key != "expected"} for scenario in scenarios]
    predictions = [_attribute_scenario(item) for item in inputs]

    correct = sum(prediction[0] == scenario["expected"]
                  for prediction, scenario in zip(predictions, scenarios, strict=True))
    assert correct / len(scenarios) >= 0.75
    assert all(prediction[1] and prediction[2] for prediction in predictions)


def test_fake_trace_pipeline_finishes_under_three_minutes(tmp_path: Path) -> None:
    inputs_dir = tmp_path / "inputs"
    inputs_dir.mkdir()
    train_path = inputs_dir / "train.evalset.json"
    val_path = inputs_dir / "val.evalset.json"
    prompt_path = inputs_dir / "baseline_system_prompt.txt"
    optimizer_config_path = inputs_dir / "optimizer.json"
    shutil.copyfile(DEFAULT_TRAIN, train_path)
    shutil.copyfile(DEFAULT_VAL, val_path)
    shutil.copyfile(DEFAULT_PROMPT, prompt_path)
    optimizer_config_path.write_text(
        json.dumps(
            {
                "seed": 91,
                "optimizer": {},
                "metrics": {},
                "gate": {
                    "min_val_score_improvement": 0.01,
                    "allow_new_hard_fail": False,
                    "protected_case_ids": [],
                    "max_score_drop_per_case": 0.0,
                    "max_total_cost": 1.0,
                },
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    started = time.perf_counter()
    report = run_pipeline(
        train_path=train_path,
        val_path=val_path,
        optimizer_config_path=optimizer_config_path,
        prompt_path=prompt_path,
        output_dir=tmp_path / "out",
        mode="fake",
        trace=True,
        run_id="performance",
    )
    elapsed = time.perf_counter() - started

    assert elapsed < 180
    assert 0 < report.audit["duration_seconds"] <= elapsed


def _load_fixture(name: str) -> list[dict[str, Any]]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _gate_acceptance_thresholds_met(
    expected: list[bool],
    predictions: list[bool],
) -> bool:
    rates = _gate_classification_rates(expected, predictions)
    return all(rate >= 0.80 for rate in rates)


def _gate_classification_rates(
    expected: list[bool],
    predictions: list[bool],
) -> tuple[float, float, float]:
    paired = list(zip(predictions, expected, strict=True))
    positive_predictions = [prediction for prediction, label in paired if label]
    negative_predictions = [prediction for prediction, label in paired if not label]
    assert paired
    assert positive_predictions
    assert negative_predictions
    overall_accuracy = sum(prediction == label for prediction, label in paired) / len(paired)
    positive_recall = sum(positive_predictions) / len(positive_predictions)
    negative_specificity = (sum(not prediction for prediction in negative_predictions) / len(negative_predictions))
    return overall_accuracy, positive_recall, negative_specificity


def _eval(
    prompt_id: str,
    split: str,
    scores: list[float],
    *,
    cost: float = 0.0,
) -> EvalResult:
    cases = [
        CaseResult(
            case_id=f"{split[0]}{index}",
            split=split,
            score=float(score),
            passed=score >= 1.0,
            output=str(score),
            metrics={"holdout": float(score)},
            hard_failed=score == 0.0,
        ) for index, score in enumerate(scores)
    ]
    return EvalResult(
        prompt_id=prompt_id,
        split=split,
        score=sum(scores) / len(scores),
        passed=all(case.passed for case in cases),
        cost=cost,
        cases=cases,
    )


def _decision_for_scenario(scenario: dict[str, Any]) -> GateDecision:
    assert "expected" not in scenario
    baseline_train = _eval("baseline", "train", scenario["train"])
    baseline_validation = _eval("baseline", "validation", scenario["validation"])
    candidate_train = _eval("candidate", "train", scenario["candidate_train"])
    candidate_validation = _eval(
        "candidate",
        "validation",
        scenario["candidate_validation"],
    )
    deltas = compute_case_deltas(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_validation,
        candidate_train=candidate_train,
        candidate_validation=candidate_validation,
    )
    gate = AcceptanceGate({
        "min_val_score_improvement": 0.01,
        "allow_new_hard_fail": False,
        "protected_case_ids": scenario["protected"],
        "max_score_drop_per_case": scenario["max_drop"],
        "max_total_cost": scenario["budget"],
    })
    incurred_cost = float(scenario["cost"])
    return gate.decide(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_validation,
        candidate_train=candidate_train,
        candidate_validation=candidate_validation,
        deltas=deltas,
        cumulative_cost=incurred_cost,
        cost_summary=CostSummary(
            optimizer=incurred_cost,
            total=incurred_cost,
            complete=True,
        ),
    )


def _attribute_scenario(scenario: dict[str, Any]) -> tuple[str, str, str]:
    assert "expected" not in scenario
    return attribute_failure(scenario["error_code"], scenario["evidence"])
