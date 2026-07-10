from __future__ import annotations

import json
from pathlib import Path

import pytest

from examples.optimization.eval_optimize_loop.eval_loop import schemas
from examples.optimization.eval_optimize_loop.eval_loop.backends import FakeBackend
from examples.optimization.eval_optimize_loop.eval_loop.config import parse_optimizer_config
from examples.optimization.eval_optimize_loop.eval_loop.config import validate_inputs
from examples.optimization.eval_optimize_loop.eval_loop.loader import load_eval_cases
from examples.optimization.eval_optimize_loop.eval_loop.loader import load_optimizer_config
from examples.optimization.eval_optimize_loop.eval_loop.loader import read_json
from examples.optimization.eval_optimize_loop.run_pipeline import _load_sdk_gate_config
from examples.optimization.eval_optimize_loop.run_pipeline import _parse_target_prompt_paths
from examples.optimization.eval_optimize_loop.run_pipeline import build_pipeline_request_and_backend
from examples.optimization.eval_optimize_loop.run_pipeline import validate_run_id


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


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        (field_name, field_value)
        for field_name in (
            "min_val_score_improvement",
            "max_score_drop_per_case",
            "max_total_cost",
        )
        for field_value in (float("nan"), float("inf"), float("-inf"), True)
    ],
)
def test_optimizer_config_rejects_non_finite_or_boolean_gate_numbers(
    tmp_path: Path,
    field_name: str,
    field_value: float | bool,
):
    path = tmp_path / "optimizer.json"

    with pytest.raises(ValueError, match="finite number"):
        parse_optimizer_config({"gate": {field_name: field_value}}, path=path)


def test_optimizer_config_allows_disabling_cost_gate(tmp_path: Path):
    config = parse_optimizer_config(
        {"gate": {"max_total_cost": None}},
        path=tmp_path / "optimizer.json",
    )

    assert config.gate.max_total_cost is None


def test_sdk_gate_config_allows_disabling_cost_gate(tmp_path: Path):
    path = tmp_path / "gate.json"
    path.write_text('{"gate": {"max_total_cost": null}}', encoding="utf-8")

    gate = _load_sdk_gate_config(path)

    assert gate["max_total_cost"] is None


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_sdk_gate_config_rejects_non_standard_constants(tmp_path: Path, constant: str):
    path = tmp_path / "gate.json"
    path.write_text(f'{{"gate": {{"max_total_cost": {constant}}}}}', encoding="utf-8")

    with pytest.raises(ValueError, match="non-standard JSON constant"):
        _load_sdk_gate_config(path)


def test_factory_rejects_same_resolved_train_and_validation_path(tmp_path: Path):
    eval_path = tmp_path / "same.evalset.json"
    eval_path.write_text('{"eval_cases": []}', encoding="utf-8")
    optimizer_path = tmp_path / "optimizer.json"
    optimizer_path.write_text('{"seed": 91}', encoding="utf-8")
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("baseline", encoding="utf-8")

    with pytest.raises(ValueError, match="train and validation.*different"):
        build_pipeline_request_and_backend(
            train_path=eval_path,
            val_path=eval_path,
            optimizer_config_path=optimizer_path,
            prompt_path=prompt_path,
            output_dir=tmp_path / "out",
            mode="fake",
        )


@pytest.mark.parametrize("run_id", ["CON", "NUL.txt", "trailing.", "x" * 129])
def test_run_id_rejects_windows_unsafe_or_oversized_components(run_id: str):
    with pytest.raises(ValueError, match="run-id.*invalid"):
        validate_run_id(run_id)


@pytest.mark.parametrize(
    "targets",
    [
        ["CON=one.txt"],
        ["Foo=one.txt", "foo=two.txt"],
        [f"{'x' * 129}=one.txt"],
    ],
)
def test_target_prompt_fields_are_windows_safe_and_casefold_unique(
    tmp_path: Path,
    targets: list[str],
):
    with pytest.raises(ValueError, match="target-prompt.*invalid|case-insensitively unique"):
        _parse_target_prompt_paths(targets, default_prompt_path=tmp_path / "prompt.txt")


def test_nested_optimizer_seed_drives_backend_and_audit_request(tmp_path: Path):
    train_path = tmp_path / "train.evalset.json"
    val_path = tmp_path / "val.evalset.json"
    for path in (train_path, val_path):
        path.write_text('{"eval_cases": []}', encoding="utf-8")
    optimizer_path = tmp_path / "optimizer.json"
    optimizer_path.write_text('{"optimize": {"algorithm": {"seed": 7}}}', encoding="utf-8")
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("baseline", encoding="utf-8")

    request, backend = build_pipeline_request_and_backend(
        train_path=train_path,
        val_path=val_path,
        optimizer_config_path=optimizer_path,
        prompt_path=prompt_path,
        output_dir=tmp_path / "out",
        mode="fake",
    )

    assert backend.seed == 7
    assert request.effective_seed == 7


def test_conflicting_top_level_and_nested_optimizer_seeds_are_rejected(tmp_path: Path):
    train_path = tmp_path / "train.evalset.json"
    val_path = tmp_path / "val.evalset.json"
    for path in (train_path, val_path):
        path.write_text('{"eval_cases": []}', encoding="utf-8")
    optimizer_path = tmp_path / "optimizer.json"
    optimizer_path.write_text(
        '{"seed": 91, "optimize": {"algorithm": {"seed": 7}}}',
        encoding="utf-8",
    )
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("baseline", encoding="utf-8")

    with pytest.raises(ValueError, match="conflicting.*seed"):
        build_pipeline_request_and_backend(
            train_path=train_path,
            val_path=val_path,
            optimizer_config_path=optimizer_path,
            prompt_path=prompt_path,
            output_dir=tmp_path / "out",
            mode="fake",
        )


def test_official_nested_seed_ignores_unrelated_string_top_level_seed(tmp_path: Path):
    train_path = tmp_path / "train.evalset.json"
    val_path = tmp_path / "val.evalset.json"
    for path in (train_path, val_path):
        path.write_text('{"eval_cases": []}', encoding="utf-8")
    optimizer_path = tmp_path / "optimizer.json"
    optimizer_path.write_text(
        '{"seed": "sdk-owned-label", "optimize": {"algorithm": {"seed": 7}}}',
        encoding="utf-8",
    )
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("baseline", encoding="utf-8")

    request, backend = build_pipeline_request_and_backend(
        train_path=train_path,
        val_path=val_path,
        optimizer_config_path=optimizer_path,
        prompt_path=prompt_path,
        output_dir=tmp_path / "out",
        mode="fake",
    )

    assert request.effective_seed == backend.seed == 7


def test_factory_rejects_injected_fake_backend_seed_mismatch(tmp_path: Path):
    train_path = tmp_path / "train.evalset.json"
    val_path = tmp_path / "val.evalset.json"
    for path in (train_path, val_path):
        path.write_text('{"eval_cases": []}', encoding="utf-8")
    optimizer_path = tmp_path / "optimizer.json"
    optimizer_path.write_text('{"seed": 91}', encoding="utf-8")
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("baseline", encoding="utf-8")

    with pytest.raises(ValueError, match="backend seed.*effective seed"):
        build_pipeline_request_and_backend(
            train_path=train_path,
            val_path=val_path,
            optimizer_config_path=optimizer_path,
            prompt_path=prompt_path,
            output_dir=tmp_path / "out",
            mode="fake",
            backend=FakeBackend(seed=7),
        )


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_read_json_rejects_non_standard_constants(tmp_path: Path, constant: str):
    path = tmp_path / "invalid.json"
    path.write_text(f'{{"value": {constant}}}', encoding="utf-8")

    with pytest.raises(ValueError, match="non-standard JSON constant") as exc_info:
        read_json(path)

    assert str(path) in str(exc_info.value)


def test_read_json_wraps_decode_errors_with_path(tmp_path: Path):
    path = tmp_path / "invalid.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        read_json(path)

    assert str(path) in str(exc_info.value)


def test_eval_case_rejects_explicit_split_mismatch():
    payload = {
        "id": "case_1",
        "split": "validation",
        "input": "hello",
        "expectation": {"type": "exact", "expected": "hello"},
    }

    with pytest.raises(ValueError, match="split mismatch"):
        schemas.EvalCase.from_dict(payload, split="train")


def test_case_result_defaults_metrics_and_trace_availability():
    result = schemas.CaseResult(
        case_id="case_1",
        split="train",
        score=1.0,
        passed=True,
        output="ok",
    )

    assert result.metrics == {}
    assert result.trace == {}
    assert result.trace_available is False


def test_case_result_preserves_legacy_positional_arguments_with_new_keyword_fields():
    trace = {"events": ["model_call"]}

    result = schemas.CaseResult(
        "case_1",
        "validation",
        0.25,
        False,
        "bad output",
        trace,
        "format_violation",
        "invalid JSON",
        "parser rejected output",
        0.125,
        True,
        "format_violation",
        metrics={"response_match": 0.25},
        trace_available=True,
    )

    assert result.trace == trace
    assert result.failure_category == "format_violation"
    assert result.failure_reason == "invalid JSON"
    assert result.evidence == "parser rejected output"
    assert result.cost == 0.125
    assert result.hard_failed is True
    assert result.expected_failure_category == "format_violation"
    assert result.metrics == {"response_match": 0.25}
    assert result.trace_available is True


def test_candidate_prompt_bundle_defaults_to_system_prompt():
    candidate = schemas.CandidatePrompt(
        candidate_id="candidate_1",
        prompt="system instructions",
        rationale="baseline",
        prompt_diff="",
    )

    assert candidate.bundle() == {"system_prompt": "system instructions"}


def test_candidate_prompt_bundle_returns_prompt_fields_copy():
    prompt_fields = {
        "system_prompt": "system instructions",
        "router_prompt": "router instructions",
    }
    candidate = schemas.CandidatePrompt(
        candidate_id="candidate_1",
        prompt="combined prompt",
        rationale="multi-prompt candidate",
        prompt_diff="",
        prompt_fields=prompt_fields,
    )

    bundle = candidate.bundle()
    bundle["system_prompt"] = "changed"

    assert bundle is not prompt_fields
    assert candidate.prompt_fields["system_prompt"] == "system instructions"


def test_optimization_contract_defaults():
    cost = schemas.CostSummary()
    round_result = schemas.OptimizationRound(
        round_id=1,
        candidate_id="candidate_1",
        prompts={"system_prompt": "optimized"},
        rationale="improve format adherence",
        metrics={"validation_score": 1.0},
        cost=cost,
        duration_seconds=0.25,
    )
    result = schemas.OptimizationResult(
        candidates=[],
        rounds=[round_result],
        cost=cost,
    )

    assert cost.optimizer == 0.0
    assert cost.evaluator == 0.0
    assert cost.agent == 0.0
    assert cost.total == 0.0
    assert cost.complete is True
    assert result.raw_summary == {}
    assert set(schemas.WritebackStatus.__args__) == {
        "rejected",
        "not_requested",
        "applied",
        "rolled_back",
        "rollback_failed",
    }


def test_optimization_report_preserves_legacy_construction_with_new_defaults():
    empty_eval = schemas.EvalResult(
        prompt_id="baseline",
        split="train",
        score=0.0,
        passed=False,
        cost=0.0,
        cases=[],
    )
    report = schemas.OptimizationReport(
        schema_version="1",
        run={},
        baseline={},
        baseline_train=empty_eval,
        baseline_validation=empty_eval,
        candidates=[],
        delta={},
        per_case_deltas=[],
        failure_attribution_summary={},
        gate_decisions=[],
        selected_candidate=None,
        audit={},
    )

    assert report.rounds == []
    assert report.cost_summary == schemas.CostSummary()
    assert report.writeback == schemas.WritebackResult(status="not_requested")


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
