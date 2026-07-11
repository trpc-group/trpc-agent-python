#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Public-interface tests for the evaluation/optimization regression loop example."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

_EXAMPLE_DIR = (Path(__file__).resolve().parents[2] / "examples" / "optimization" / "eval_optimize_loop")
_OPTIMIZATION_EXAMPLES_DIR = _EXAMPLE_DIR.parent


def _load_public_interface():
    if str(_OPTIMIZATION_EXAMPLES_DIR) not in sys.path:
        sys.path.insert(0, str(_OPTIMIZATION_EXAMPLES_DIR))
    from eval_optimize_loop.loop import EvalOptimizePipeline
    from eval_optimize_loop.loop import PipelineSpec

    return EvalOptimizePipeline, PipelineSpec


def test_example_deliverables_are_complete_and_sdk_configs_validate() -> None:
    expected = {
        "README.md",
        "DESIGN.md",
        "run_pipeline.py",
        "pipeline.json",
        "optimizer.json",
        "regression.metrics.json",
        "gate.json",
        "agent/prompts/system.md",
        "data/train.evalset.json",
        "data/val.evalset.json",
        "sample_output/optimization_report.json",
        "sample_output/optimization_report.md",
    }
    assert all((_EXAMPLE_DIR / path).is_file() for path in expected)

    from trpc_agent_sdk.evaluation import EvalConfig
    from trpc_agent_sdk.evaluation import EvalSet
    from trpc_agent_sdk.evaluation._optimize_config import load_optimize_config

    load_optimize_config(str(_EXAMPLE_DIR / "optimizer.json"))
    EvalConfig.model_validate_json((_EXAMPLE_DIR / "regression.metrics.json").read_text(encoding="utf-8"))
    train = EvalSet.model_validate_json((_EXAMPLE_DIR / "data/train.evalset.json").read_text(encoding="utf-8"))
    validation = EvalSet.model_validate_json((_EXAMPLE_DIR / "data/val.evalset.json").read_text(encoding="utf-8"))
    assert len(train.eval_cases) == 3
    assert len(validation.eval_cases) == 3

    design = (_EXAMPLE_DIR / "DESIGN.md").read_text(encoding="utf-8")
    chinese_chars = sum("\u4e00" <= char <= "\u9fff" for char in design)
    assert 300 <= chinese_chars <= 500

    _load_public_interface()
    from eval_optimize_loop.loop import OptimizationReport

    sample = OptimizationReport.model_validate_json(
        (_EXAMPLE_DIR / "sample_output/optimization_report.json").read_text(encoding="utf-8"))
    assert sample.selected_candidate_id == "robust"


def test_output_override_is_resolved_from_callers_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, PipelineSpec = _load_public_interface()
    monkeypatch.chdir(tmp_path)
    spec = PipelineSpec.from_file(
        _EXAMPLE_DIR / "pipeline.json",
        output_dir=Path("relative-run"),
    )
    assert spec.output_dir == tmp_path / "relative-run"


def test_artifact_labels_are_safe_and_candidate_ids_are_unique(tmp_path: Path, ) -> None:
    _, PipelineSpec = _load_public_interface()
    from eval_optimize_loop.loop.models import CandidateSource
    from eval_optimize_loop.loop.trace import TraceEvaluator

    for unsafe_id in ("../outside", "baseline", "NUL"):
        with pytest.raises(ValueError, match="candidate_id"):
            CandidateSource(candidate_id=unsafe_id, path=tmp_path / "prompt.md")
    with pytest.raises(ValueError, match="safe artifact label"):
        TraceEvaluator(tmp_path)._trace_directory("../outside")
    with pytest.raises(ValueError, match="reserved artifact label"):
        TraceEvaluator(tmp_path)._trace_directory("CON")

    spec = PipelineSpec.from_file(_EXAMPLE_DIR / "pipeline.json", output_dir=tmp_path / "run")
    payload = spec.model_dump()
    payload["candidate_sources"][1]["candidate_id"] = payload["candidate_sources"][0]["candidate_id"].upper()
    with pytest.raises(ValueError, match="candidate_ids must be case-insensitively unique"):
        PipelineSpec.model_validate(payload)

    payload = spec.model_dump()
    payload["target_prompts"]["SYSTEM"] = payload["target_prompts"]["system"]
    with pytest.raises(ValueError, match="target prompt names must be case-insensitively unique"):
        PipelineSpec.model_validate(payload)


def test_offline_model_rejects_ambiguous_duplicate_queries() -> None:
    _load_public_interface()
    from eval_optimize_loop.loop.offline import OfflineModel
    from trpc_agent_sdk.evaluation import EvalSet

    train_payload = json.loads((_EXAMPLE_DIR / "data/train.evalset.json").read_text(encoding="utf-8"))
    validation_payload = json.loads((_EXAMPLE_DIR / "data/val.evalset.json").read_text(encoding="utf-8"))
    validation_payload["eval_cases"][0]["conversation"][0]["user_content"] = (
        train_payload["eval_cases"][0]["conversation"][0]["user_content"])
    train = EvalSet.model_validate(train_payload)
    validation = EvalSet.model_validate(validation_payload)

    with pytest.raises(ValueError, match="offline replay queries must be unique"):
        OfflineModel.configure(
            eval_sets=[train, validation],
            candidate_prompts=["[variant: test]"],
        )


def test_query_identity_preserves_case_and_inner_whitespace() -> None:
    _load_public_interface()
    from eval_optimize_loop.loop.analysis import RegressionAnalyzer
    from eval_optimize_loop.loop.offline import OfflineModel
    from trpc_agent_sdk.evaluation import EvalSet

    train_payload = json.loads((_EXAMPLE_DIR / "data/train.evalset.json").read_text(encoding="utf-8"))
    validation_payload = json.loads((_EXAMPLE_DIR / "data/val.evalset.json").read_text(encoding="utf-8"))
    train_payload["eval_cases"][0]["conversation"][0]["user_content"]["parts"][0]["text"] = "SKU ab c"
    validation_payload["eval_cases"][0]["conversation"][0]["user_content"]["parts"][0]["text"] = "SKU a bc"
    train = EvalSet.model_validate(train_payload)
    validation = EvalSet.model_validate(validation_payload)
    analyzer = RegressionAnalyzer(seed=91, bootstrap_samples=100, confidence_level=0.95)

    audit = analyzer.validate_data_quality(
        train,
        validation,
        train_path=Path("train.evalset.json"),
        validation_path=Path("val.evalset.json"),
        prompt_text="",
    )
    OfflineModel.configure(
        eval_sets=[train, validation],
        candidate_prompts=["[variant: test]"],
    )

    assert audit.passed is True


@pytest.mark.asyncio
async def test_cross_split_duplicate_queries_fail_data_quality_before_replay(tmp_path: Path, ) -> None:
    EvalOptimizePipeline, PipelineSpec = _load_public_interface()
    validation_payload = json.loads((_EXAMPLE_DIR / "data/val.evalset.json").read_text(encoding="utf-8"))
    train_payload = json.loads((_EXAMPLE_DIR / "data/train.evalset.json").read_text(encoding="utf-8"))
    validation_payload["eval_cases"][0]["conversation"][0]["user_content"] = (
        train_payload["eval_cases"][0]["conversation"][0]["user_content"])
    validation_path = tmp_path / "duplicate-query.evalset.json"
    validation_path.write_text(json.dumps(validation_payload, ensure_ascii=False), encoding="utf-8")
    spec = PipelineSpec.from_file(
        _EXAMPLE_DIR / "pipeline.json",
        output_dir=tmp_path / "duplicate-query",
    ).model_copy(update={"validation_dataset": validation_path})

    with pytest.raises(ValueError, match="duplicate_queries"):
        await EvalOptimizePipeline(spec).run()


def test_non_finite_scores_and_resources_fail_closed() -> None:
    _load_public_interface()
    from eval_optimize_loop.loop.models import CaseEvaluation
    from eval_optimize_loop.loop.models import ResourceUsage

    with pytest.raises(ValueError, match="finite number"):
        CaseEvaluation(case_id="nan-score", passed=False, score=float("nan"))
    with pytest.raises(ValueError, match="finite number"):
        ResourceUsage(duration_seconds=float("inf"))


@pytest.mark.parametrize("status_code", [500, "500"])
def test_numeric_http_error_status_is_attributed_as_tool_error(status_code: int | str) -> None:
    _load_public_interface()
    from eval_optimize_loop.loop.trace import _tool_response_is_error

    assert _tool_response_is_error({"status_code": status_code, "message": "backend unavailable"}) is True


def test_key_case_pass_to_fail_is_rejected_even_when_score_is_unchanged() -> None:
    _load_public_interface()
    from eval_optimize_loop.loop.analysis import RegressionAnalyzer
    from eval_optimize_loop.loop.models import BaselineEvaluation
    from eval_optimize_loop.loop.models import CandidateDelta
    from eval_optimize_loop.loop.models import CaseEvaluation
    from eval_optimize_loop.loop.models import ResourceUsage
    from eval_optimize_loop.loop.models import SplitEvaluation

    analyzer = RegressionAnalyzer(seed=91, bootstrap_samples=100, confidence_level=0.95)
    baseline_train = SplitEvaluation(
        split="train",
        pass_rate=0.0,
        average_score=0.0,
        cases=[CaseEvaluation(case_id="train", passed=False, score=0.0)],
    )
    candidate_train = baseline_train.model_copy(deep=True)
    baseline_validation = SplitEvaluation(
        split="validation",
        pass_rate=0.5,
        average_score=0.25,
        cases=[
            CaseEvaluation(case_id="key", passed=True, score=0.5, key_case=True),
            CaseEvaluation(case_id="other", passed=False, score=0.0),
        ],
    )
    candidate_validation = SplitEvaluation(
        split="validation",
        pass_rate=0.5,
        average_score=0.75,
        cases=[
            CaseEvaluation(case_id="key", passed=False, score=0.5, key_case=True),
            CaseEvaluation(case_id="other", passed=True, score=1.0),
        ],
    )
    baseline = BaselineEvaluation(train=baseline_train, validation=baseline_validation)
    delta = CandidateDelta(
        train=analyzer.diff(baseline_train, candidate_train),
        validation=analyzer.diff(baseline_validation, candidate_validation),
    )

    decision = analyzer.gate(
        baseline=baseline,
        candidate_train=candidate_train,
        candidate_validation=candidate_validation,
        delta=delta,
        optimizer_status="SUCCEEDED",
        resources=ResourceUsage(),
        config={"min_validation_gain": 0.0},
    )

    key_check = next(check for check in decision.checks if check.name == "key_cases_no_regression")
    assert key_check.passed is False
    assert key_check.actual == ["key"]
    assert decision.accepted is False


@pytest.mark.asyncio
async def test_success_false_tool_response_is_attributed_as_tool_error(tmp_path: Path, ) -> None:
    EvalOptimizePipeline, PipelineSpec = _load_public_interface()
    train_payload = json.loads((_EXAMPLE_DIR / "data/train.evalset.json").read_text(encoding="utf-8"))
    tool_case = next(case for case in train_payload["eval_cases"] if case["eval_id"] == "train_tool_call_error")
    traces = tool_case["session_input"]["state"]["variant_traces"]
    for variant in ("baseline", "ineffective"):
        traces[variant]["intermediate_data"]["tool_responses"][0]["response"] = {
            "success": False,
            "message": "backend unavailable",
        }
    train_path = tmp_path / "success-false.evalset.json"
    train_path.write_text(json.dumps(train_payload, ensure_ascii=False), encoding="utf-8")
    spec = PipelineSpec.from_file(
        _EXAMPLE_DIR / "pipeline.json",
        output_dir=tmp_path / "success-false",
    ).model_copy(update={"train_dataset": train_path})

    report = await EvalOptimizePipeline(spec).run()

    result = next(case for case in report.baseline.train.cases if case.case_id == "train_tool_call_error")
    assert result.primary_failure == "tool_call_error"
    assert result.failure_reasons[0].evidence["tool_responses"][0]["response"]["success"] is False


@pytest.mark.asyncio
async def test_offline_pipeline_writes_machine_and_human_reports_without_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one public seam runs the real SDK loop and returns its report."""
    for name in (
            "TRPC_AGENT_API_KEY",
            "TRPC_AGENT_BASE_URL",
            "TRPC_AGENT_MODEL_NAME",
            "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    EvalOptimizePipeline, PipelineSpec = _load_public_interface()
    output_dir = tmp_path / "run"
    prompt_path = _EXAMPLE_DIR / "agent/prompts/system.md"
    original_prompt = prompt_path.read_bytes()
    spec = PipelineSpec.from_file(
        _EXAMPLE_DIR / "pipeline.json",
        output_dir=output_dir,
    )

    report = await EvalOptimizePipeline(spec).run()

    assert report.status == "accepted"
    assert report.optimizer.algorithm == "gepa_reflective"
    assert report.optimizer.used_agent_optimizer is True
    assert report.selected_candidate_id is not None
    assert prompt_path.read_bytes() == original_prompt
    assert (output_dir / "optimizer/prompt_workspace/system.md").is_file()
    assert (output_dir / "optimization_report.json").is_file()
    assert (output_dir / "optimization_report.md").is_file()

    payload = json.loads((output_dir / "optimization_report.json").read_text(encoding="utf-8"))
    assert payload["status"] == "accepted"
    assert payload["baseline"]["train"]["cases"]
    assert payload["baseline"]["validation"]["cases"]
    markdown = (output_dir / "optimization_report.md").read_text(encoding="utf-8")
    assert "Baseline" in markdown
    assert "Gate" in markdown


@pytest.mark.asyncio
async def test_concurrent_offline_runs_fail_closed_without_touching_source_prompt(tmp_path: Path, ) -> None:
    EvalOptimizePipeline, PipelineSpec = _load_public_interface()
    prompt_path = _EXAMPLE_DIR / "agent/prompts/system.md"
    original_prompt = prompt_path.read_bytes()
    specs = [
        PipelineSpec.from_file(
            _EXAMPLE_DIR / "pipeline.json",
            output_dir=tmp_path / f"concurrent-{index}",
        ) for index in range(2)
    ]

    results = await asyncio.gather(
        *(EvalOptimizePipeline(spec).run() for spec in specs),
        return_exceptions=True,
    )

    reports = [result for result in results if not isinstance(result, BaseException)]
    errors = [result for result in results if isinstance(result, BaseException)]
    assert len(reports) == 1
    assert reports[0].status == "accepted"
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert "already running" in str(errors[0])
    assert prompt_path.read_bytes() == original_prompt


@pytest.mark.asyncio
async def test_explicit_apply_writes_only_the_accepted_prompt_atomically(tmp_path: Path, ) -> None:
    EvalOptimizePipeline, PipelineSpec = _load_public_interface()
    source_prompt = tmp_path / "system.md"
    source_prompt.write_text(
        (_EXAMPLE_DIR / "agent/prompts/system.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    spec = PipelineSpec.from_file(
        _EXAMPLE_DIR / "pipeline.json",
        output_dir=tmp_path / "apply",
    ).model_copy(update={
        "target_prompts": {
            "system": source_prompt
        },
        "apply_if_accepted": True,
    })

    report = await EvalOptimizePipeline(spec).run()

    assert report.selected_candidate_id == "robust"
    assert report.candidate is not None
    assert source_prompt.read_text(encoding="utf-8") == report.candidate.prompts["system"]
    assert not source_prompt.with_name(source_prompt.name + ".tmp").exists()


@pytest.mark.asyncio
async def test_every_unique_proposal_is_independently_re_evaluated_and_gated(tmp_path: Path, ) -> None:
    EvalOptimizePipeline, PipelineSpec = _load_public_interface()
    spec = PipelineSpec.from_file(
        _EXAMPLE_DIR / "pipeline.json",
        output_dir=tmp_path / "all-candidates",
    )

    report = await EvalOptimizePipeline(spec).run()

    by_id = {candidate.candidate_id: candidate for candidate in report.candidates}
    assert set(by_id) == {"ineffective", "overfit", "robust"}

    assert by_id["ineffective"].gate.accepted is False
    assert by_id["ineffective"].delta.validation.pass_rate_delta == pytest.approx(0.0)

    assert by_id["overfit"].gate.accepted is False
    assert by_id["overfit"].gate.overfitting_detected is True
    assert by_id["overfit"].delta.train.pass_rate_delta > 0
    assert by_id["overfit"].delta.validation.pass_rate_delta < 0
    assert "val_format_key_case" in by_id["overfit"].delta.validation.newly_failed

    assert by_id["robust"].gate.accepted is True
    assert report.selected_candidate_id == "robust"
    assert report.candidate is not None
    assert report.candidate.candidate_id == "robust"


@pytest.mark.asyncio
async def test_trace_replay_attributes_all_required_failure_categories(tmp_path: Path, ) -> None:
    EvalOptimizePipeline, PipelineSpec = _load_public_interface()
    output_dir = tmp_path / "attribution"
    report = await EvalOptimizePipeline(PipelineSpec.from_file(
        _EXAMPLE_DIR / "pipeline.json",
        output_dir=output_dir,
    )).run()

    cases = list(report.baseline.train.cases) + list(report.baseline.validation.cases)
    for candidate in report.candidates:
        cases.extend(candidate.train.cases)
        cases.extend(candidate.validation.cases)

    observed = {case.primary_failure for case in cases if not case.passed and case.primary_failure is not None}
    assert {
        "final_response_mismatch",
        "tool_call_error",
        "parameter_error",
        "llm_rubric_failure",
        "knowledge_recall_insufficiency",
        "format_failure",
    }.issubset(observed)
    assert all(case.failure_reasons for case in cases if not case.passed)
    assert all(reason.explanation for case in cases for reason in case.failure_reasons)
    assert report.failure_attribution.coverage_rate == pytest.approx(1.0)
    assert {
        "final_response_mismatch",
        "tool_call_error",
        "parameter_error",
        "llm_rubric_failure",
        "knowledge_recall_insufficiency",
        "format_failure",
    }.issubset(report.failure_attribution.category_counts)

    baseline_by_id = {
        case.case_id: case.primary_failure
        for case in report.baseline.train.cases + report.baseline.validation.cases if not case.passed
    }
    assert baseline_by_id == {
        "train_final_response_mismatch": "final_response_mismatch",
        "train_tool_call_error": "tool_call_error",
        "train_parameter_error": "parameter_error",
        "val_llm_rubric_failure": "llm_rubric_failure",
        "val_knowledge_recall_insufficiency": "knowledge_recall_insufficiency",
    }
    overfit = next(item for item in report.candidates if item.candidate_id == "overfit")
    format_case = next(item for item in overfit.validation.cases if item.case_id == "val_format_key_case")
    assert format_case.primary_failure == "format_failure"

    implementation = "\n".join(path.read_text(encoding="utf-8") for path in (_EXAMPLE_DIR / "loop").glob("*.py"))
    for case_id in set(baseline_by_id) | {"val_format_key_case"}:
        assert case_id not in implementation

    trace_files = sorted((output_dir / "traces").glob("**/*.trace.evalset.json"))
    assert len(trace_files) == 8  # baseline + three proposals, train + validation
    knowledge_case = next(case for case in report.baseline.validation.cases
                          if case.case_id == "val_knowledge_recall_insufficiency")
    assert "llm_rubric_knowledge_recall" in {metric.metric_name for metric in knowledge_case.metrics}
    assert knowledge_case.key_trajectory


@pytest.mark.asyncio
async def test_report_includes_paired_bootstrap_uncertainty_and_pareto_selection(tmp_path: Path, ) -> None:
    EvalOptimizePipeline, PipelineSpec = _load_public_interface()
    report = await EvalOptimizePipeline(
        PipelineSpec.from_file(
            _EXAMPLE_DIR / "pipeline.json",
            output_dir=tmp_path / "statistics",
        )).run()

    by_id = {candidate.candidate_id: candidate for candidate in report.candidates}
    robust_ci = by_id["robust"].delta.validation.paired_pass_rate_ci
    assert robust_ci.point_estimate == pytest.approx(2 / 3)
    assert robust_ci.confidence_level == pytest.approx(0.95)
    assert robust_ci.bootstrap_samples == 2000
    assert robust_ci.lower <= robust_ci.point_estimate <= robust_ci.upper

    overfit_ci = by_id["overfit"].delta.validation.paired_pass_rate_ci
    assert overfit_ci.upper <= 0.0
    assert by_id["robust"].pareto_optimal is True
    assert by_id["ineffective"].pareto_optimal is False
    assert by_id["overfit"].pareto_optimal is False

    robust_checks = {check.name: check for check in by_id["robust"].gate.checks}
    assert robust_checks["validation_gain_ci_lower_bound"].passed is True


@pytest.mark.asyncio
async def test_data_quality_fails_closed_on_cross_split_near_duplicate(tmp_path: Path, ) -> None:
    EvalOptimizePipeline, PipelineSpec = _load_public_interface()
    train_payload = json.loads((_EXAMPLE_DIR / "data/train.evalset.json").read_text(encoding="utf-8"))
    validation_payload = json.loads((_EXAMPLE_DIR / "data/val.evalset.json").read_text(encoding="utf-8"))
    copied = json.loads(json.dumps(train_payload["eval_cases"][0]["conversation"]))
    copied[0]["user_content"]["parts"][0]["text"] += " 请尽快。"
    validation_payload["eval_cases"][0]["conversation"] = copied
    contaminated = tmp_path / "near-duplicate.evalset.json"
    contaminated.write_text(
        json.dumps(validation_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    spec = PipelineSpec.from_file(
        _EXAMPLE_DIR / "pipeline.json",
        output_dir=tmp_path / "rejected",
    ).model_copy(update={"validation_dataset": contaminated})

    with pytest.raises(ValueError, match="near_cross_split"):
        await EvalOptimizePipeline(spec).run()


@pytest.mark.asyncio
async def test_candidate_audit_uses_independent_evaluation_resources(tmp_path: Path, ) -> None:
    EvalOptimizePipeline, PipelineSpec = _load_public_interface()
    report = await EvalOptimizePipeline(
        PipelineSpec.from_file(
            _EXAMPLE_DIR / "pipeline.json",
            output_dir=tmp_path / "candidate-audit",
        )).run()

    for candidate in report.candidates:
        usage = candidate.audit.resources
        assert usage.metric_calls == 24  # 6 cases x 4 metrics x 1 run
        assert usage.judge_calls == 12  # 6 cases x 2 offline judge metrics
        assert usage.total_tokens > 0
        assert usage.duration_seconds > 0
        assert usage.cost_usd == pytest.approx(0.0)
        assert usage.cost_measurement == "measured_zero_offline"
        assert candidate.audit.seed == 91
        assert len(candidate.audit.prompt_sha256) == 64


@pytest.mark.asyncio
async def test_regression_protections_can_be_explicitly_disabled(tmp_path: Path, ) -> None:
    EvalOptimizePipeline, PipelineSpec = _load_public_interface()
    gate_payload = json.loads((_EXAMPLE_DIR / "gate.json").read_text(encoding="utf-8"))
    gate_payload.update({
        "min_validation_gain": -1.0,
        "min_validation_gain_ci_lower_bound": -1.0,
        "forbid_new_hard_failures": False,
        "key_cases_no_regression": False,
        "reject_overfitting": False,
    })
    permissive_gate = tmp_path / "permissive-gate.json"
    permissive_gate.write_text(
        json.dumps(gate_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    spec = PipelineSpec.from_file(
        _EXAMPLE_DIR / "pipeline.json",
        output_dir=tmp_path / "permissive",
    ).model_copy(update={"gate_config": permissive_gate})

    report = await EvalOptimizePipeline(spec).run()

    overfit = next(candidate for candidate in report.candidates if candidate.candidate_id == "overfit")
    assert overfit.gate.accepted is True
    optional_checks = {
        check.name: check
        for check in overfit.gate.checks if check.name in {
            "no_new_hard_failures",
            "key_cases_no_regression",
            "no_train_validation_overfit",
        }
    }
    assert all(check.required is False for check in optional_checks.values())


@pytest.mark.asyncio
async def test_score_only_train_improvement_and_validation_regression_is_overfit(tmp_path: Path, ) -> None:
    EvalOptimizePipeline, PipelineSpec = _load_public_interface()
    train_payload = json.loads((_EXAMPLE_DIR / "data/train.evalset.json").read_text(encoding="utf-8"))
    validation_payload = json.loads((_EXAMPLE_DIR / "data/val.evalset.json").read_text(encoding="utf-8"))

    train_case = next(case for case in train_payload["eval_cases"] if case["eval_id"] == "train_parameter_error")
    train_case["session_input"]["state"]["variant_traces"]["ineffective"]["intermediate_data"] = json.loads(
        json.dumps(train_case["conversation"][0]["intermediate_data"]))

    validation_case = next(case for case in validation_payload["eval_cases"]
                           if case["eval_id"] == "val_knowledge_recall_insufficiency")
    validation_case["session_input"]["state"]["variant_traces"]["ineffective"]["intermediate_data"] = {
        "tool_uses": [],
        "tool_responses": [],
    }

    train_path = tmp_path / "score-only-train.evalset.json"
    validation_path = tmp_path / "score-only-validation.evalset.json"
    train_path.write_text(json.dumps(train_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    validation_path.write_text(json.dumps(validation_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    gate_payload = json.loads((_EXAMPLE_DIR / "gate.json").read_text(encoding="utf-8"))
    gate_payload.update({
        "min_validation_gain": -1.0,
        "min_validation_gain_ci_lower_bound": -1.0,
        "forbid_new_hard_failures": False,
        "key_cases_no_regression": False,
        "reject_overfitting": True,
    })
    gate_path = tmp_path / "score-only-gate.json"
    gate_path.write_text(json.dumps(gate_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    spec = PipelineSpec.from_file(
        _EXAMPLE_DIR / "pipeline.json",
        output_dir=tmp_path / "score-only",
    ).model_copy(update={
        "train_dataset": train_path,
        "validation_dataset": validation_path,
        "gate_config": gate_path,
    })
    report = await EvalOptimizePipeline(spec).run()

    ineffective = next(candidate for candidate in report.candidates if candidate.candidate_id == "ineffective")
    assert ineffective.delta.train.pass_rate_delta == pytest.approx(0.0)
    assert ineffective.delta.train.average_score_delta > 0
    assert ineffective.delta.validation.pass_rate_delta == pytest.approx(0.0)
    assert ineffective.delta.validation.average_score_delta < 0
    assert ineffective.gate.overfitting_detected is True
    assert ineffective.gate.accepted is False


@pytest.mark.asyncio
async def test_replay_cost_is_measured_and_enforced_by_gate(tmp_path: Path, ) -> None:
    EvalOptimizePipeline, PipelineSpec = _load_public_interface()
    train_payload = json.loads((_EXAMPLE_DIR / "data/train.evalset.json").read_text(encoding="utf-8"))
    validation_payload = json.loads((_EXAMPLE_DIR / "data/val.evalset.json").read_text(encoding="utf-8"))
    for case in train_payload["eval_cases"] + validation_payload["eval_cases"]:
        case["session_input"]["state"]["variant_traces"]["robust"]["usage"]["cost"] = 1.0

    train_path = tmp_path / "cost-train.evalset.json"
    validation_path = tmp_path / "cost-validation.evalset.json"
    train_path.write_text(json.dumps(train_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    validation_path.write_text(json.dumps(validation_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    gate_payload = json.loads((_EXAMPLE_DIR / "gate.json").read_text(encoding="utf-8"))
    gate_payload["budget"]["max_cost_usd"] = 0.5
    gate_path = tmp_path / "cost-gate.json"
    gate_path.write_text(json.dumps(gate_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    metrics_payload = json.loads((_EXAMPLE_DIR / "regression.metrics.json").read_text(encoding="utf-8"))
    metrics_payload["num_runs"] = 2
    metrics_path = tmp_path / "two-runs.metrics.json"
    metrics_path.write_text(json.dumps(metrics_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    spec = PipelineSpec.from_file(
        _EXAMPLE_DIR / "pipeline.json",
        output_dir=tmp_path / "cost",
    ).model_copy(
        update={
            "train_dataset": train_path,
            "validation_dataset": validation_path,
            "gate_config": gate_path,
            "regression_metrics_config": metrics_path,
        })
    report = await EvalOptimizePipeline(spec).run()

    robust = next(candidate for candidate in report.candidates if candidate.candidate_id == "robust")
    cost_check = next(check for check in robust.gate.checks if check.name == "cost_budget")
    expected_single_run_tokens = sum(trace["usage"][key]
                                     for case in train_payload["eval_cases"] + validation_payload["eval_cases"]
                                     for trace in [case["session_input"]["state"]["variant_traces"]["robust"]]
                                     for key in ("input_tokens", "output_tokens"))
    assert robust.audit.resources.metric_calls == 48
    assert robust.audit.resources.judge_calls == 24
    assert robust.audit.resources.total_tokens == expected_single_run_tokens * 2
    assert robust.audit.resources.cost_usd == pytest.approx(12.0)
    assert robust.audit.resources.cost_measurement == "measured_from_replay"
    assert cost_check.passed is False
    assert robust.gate.accepted is False


@pytest.mark.asyncio
async def test_plain_text_format_violation_is_attributed_without_json_heuristic(tmp_path: Path, ) -> None:
    EvalOptimizePipeline, PipelineSpec = _load_public_interface()
    validation_payload = json.loads((_EXAMPLE_DIR / "data/val.evalset.json").read_text(encoding="utf-8"))
    format_case = next(case for case in validation_payload["eval_cases"] if case["eval_id"] == "val_format_key_case")
    format_case["conversation"][0]["user_content"]["parts"][0]["text"] = "查询订单 O-400，只返回一行纯文本，格式为“订单号|状态”。"
    expected = "O-400|已签收"
    format_case["conversation"][0]["final_response"]["parts"][0]["text"] = expected
    traces = format_case["session_input"]["state"]["variant_traces"]
    for variant in ("baseline", "ineffective", "robust"):
        traces[variant]["final_response"]["parts"][0]["text"] = expected
    for trace in traces.values():
        trace["signals"].pop("format_pass")
    traces["overfit"]["final_response"]["parts"][0]["text"] = "订单号：O-400\n状态：已签收"
    traces["overfit"]["signals"]["llm_rubric_pass"] = False

    validation_path = tmp_path / "plain-text-format.evalset.json"
    validation_path.write_text(
        json.dumps(validation_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    spec = PipelineSpec.from_file(
        _EXAMPLE_DIR / "pipeline.json",
        output_dir=tmp_path / "plain-text-format",
    ).model_copy(update={"validation_dataset": validation_path})

    report = await EvalOptimizePipeline(spec).run()

    overfit = next(candidate for candidate in report.candidates if candidate.candidate_id == "overfit")
    format_result = next(case for case in overfit.validation.cases if case.case_id == "val_format_key_case")
    assert format_result.primary_failure == "format_failure"
    evidence = format_result.failure_reasons[0].evidence
    assert evidence["detector"] == "requested_format"
    assert "not_single_line" in evidence["violations"]


@pytest.mark.asyncio
async def test_unknown_replay_cost_fails_closed_and_is_not_rendered_as_zero(tmp_path: Path, ) -> None:
    EvalOptimizePipeline, PipelineSpec = _load_public_interface()
    train_payload = json.loads((_EXAMPLE_DIR / "data/train.evalset.json").read_text(encoding="utf-8"))
    first_usage = train_payload["eval_cases"][0]["session_input"]["state"]["variant_traces"]["robust"]["usage"]
    first_usage.pop("cost")
    train_path = tmp_path / "unknown-cost.evalset.json"
    train_path.write_text(json.dumps(train_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    spec = PipelineSpec.from_file(
        _EXAMPLE_DIR / "pipeline.json",
        output_dir=tmp_path / "unknown-cost",
    ).model_copy(update={"train_dataset": train_path})
    report = await EvalOptimizePipeline(spec).run()

    robust = next(candidate for candidate in report.candidates if candidate.candidate_id == "robust")
    cost_check = next(check for check in robust.gate.checks if check.name == "cost_budget")
    assert robust.audit.resources.cost_usd is None
    assert robust.audit.resources.cost_measurement == "unavailable"
    assert cost_check.actual == "unavailable"
    assert cost_check.passed is False
    markdown = (spec.output_dir / "optimization_report.md").read_text(encoding="utf-8")
    robust_rows = [line for line in markdown.splitlines() if line.startswith("| `robust` |")]
    resource_row = next(line for line in robust_rows if "unavailable" in line)
    assert "$0.0000" not in resource_row


@pytest.mark.asyncio
async def test_json_shaped_reference_is_not_a_format_rule_without_a_json_request(tmp_path: Path, ) -> None:
    EvalOptimizePipeline, PipelineSpec = _load_public_interface()
    validation_payload = json.loads((_EXAMPLE_DIR / "data/val.evalset.json").read_text(encoding="utf-8"))
    quality_case = next(case for case in validation_payload["eval_cases"]
                        if case["eval_id"] == "val_llm_rubric_failure")
    quality_case["conversation"][0]["user_content"]["parts"][0]["text"] = "完整说明退款流程；无需采用特定输出格式。"
    quality_case["conversation"][0]["final_response"]["parts"][0]["text"] = '{"steps":["核验订单","告知时效"]}'
    for trace in quality_case["session_input"]["state"]["variant_traces"].values():
        trace["signals"].pop("format_pass")

    validation_path = tmp_path / "json-shaped-reference.evalset.json"
    validation_path.write_text(
        json.dumps(validation_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    spec = PipelineSpec.from_file(
        _EXAMPLE_DIR / "pipeline.json",
        output_dir=tmp_path / "json-shaped-reference",
    ).model_copy(update={"validation_dataset": validation_path})
    report = await EvalOptimizePipeline(spec).run()

    quality_result = next(case for case in report.baseline.validation.cases if case.case_id == "val_llm_rubric_failure")
    assert quality_result.primary_failure == "llm_rubric_failure"


def test_explicit_format_rubric_pass_is_authoritative_over_fallbacks() -> None:
    _load_public_interface()
    from eval_optimize_loop.loop.trace import TraceEvaluator
    from trpc_agent_sdk.evaluation import EvalCase
    from trpc_agent_sdk.evaluation import EvalCaseResult
    from trpc_agent_sdk.evaluation import EvalMetricResult
    from trpc_agent_sdk.evaluation import EvalMetricResultDetails
    from trpc_agent_sdk.evaluation import EvalMetricResultPerInvocation
    from trpc_agent_sdk.evaluation import EvalStatus
    from trpc_agent_sdk.evaluation import IntermediateData
    from trpc_agent_sdk.evaluation import Invocation
    from trpc_agent_sdk.evaluation import RubricScore
    from trpc_agent_sdk.types import Content
    from trpc_agent_sdk.types import Part

    def _content(text: str, role: str) -> Content:
        return Content(role=role, parts=[Part.from_text(text=text)])

    expected = Invocation(
        invocation_id="expected",
        user_content=_content("只返回一行纯文本。", "user"),
        final_response=_content("合格回答", "model"),
        intermediate_data=IntermediateData(),
    )
    actual = Invocation(
        invocation_id="actual",
        user_content=expected.user_content,
        final_response=_content("第一行\n第二行", "model"),
        intermediate_data=IntermediateData(),
    )
    rubric_metric = EvalMetricResult(
        metric_name="llm_rubric_response",
        threshold=1.0,
        score=0.5,
        eval_status=EvalStatus.FAILED,
        details=EvalMetricResultDetails(
            reason="quality failed; format passed",
            rubric_scores=[
                RubricScore(id="format", score=1.0, reason="single-line rule passed"),
                RubricScore(id="quality", score=0.0, reason="required content missing"),
            ],
        ),
    )
    case = EvalCase(
        eval_id="explicit-format-pass",
        eval_mode="trace",
        conversation=[expected],
        actual_conversation=[actual],
    )
    run = EvalCaseResult(
        eval_id=case.eval_id,
        final_eval_status=EvalStatus.FAILED,
        overall_eval_metric_results=[rubric_metric],
        eval_metric_result_per_invocation=[
            EvalMetricResultPerInvocation(
                actual_invocation=actual,
                expected_invocation=expected,
                eval_metric_results=[rubric_metric],
            )
        ],
        session_id="offline",
    )

    reasons = TraceEvaluator._attribute_failure(case, [run], {rubric_metric.metric_name: rubric_metric})

    assert reasons[0].category == "llm_rubric_failure"


@pytest.mark.parametrize(("request_text", "expected", "actual"), [
    ("请用自然语言回答，不要返回 JSON。", '{"answer":"参考"}', "自然语言回答"),
    ("请勿使用 JSON，请用自然语言回答。", '{"answer":"参考"}', "自然语言回答"),
    ("切勿采用 JSON 格式。", '{"answer":"参考"}', "自然语言回答"),
    ("不得返回 JSON。", '{"answer":"参考"}', "自然语言回答"),
    ("不能使用 JSON。", '{"answer":"参考"}', "自然语言回答"),
    ("不可输出 JSON。", '{"answer":"参考"}', "自然语言回答"),
    ("Never return JSON.", '{"answer":"reference"}', "natural language answer"),
    ("不要只返回一行，请分点说明。", "第一点\n第二点", "第一点\n第二点"),
    ("不要使用 Markdown，请使用纯文本。", "# 参考标题", "纯文本回答"),
])
def test_negative_format_directives_do_not_trigger_format_violations(
    request_text: str,
    expected: str,
    actual: str,
) -> None:
    _load_public_interface()
    from eval_optimize_loop.loop.trace import TraceEvaluator
    from trpc_agent_sdk.evaluation import EvalCase
    from trpc_agent_sdk.evaluation import IntermediateData
    from trpc_agent_sdk.evaluation import Invocation
    from trpc_agent_sdk.types import Content
    from trpc_agent_sdk.types import Part

    invocation = Invocation(
        invocation_id="negative-format",
        user_content=Content(role="user", parts=[Part.from_text(text=request_text)]),
        final_response=Content(role="model", parts=[Part.from_text(text=expected)]),
        intermediate_data=IntermediateData(),
    )
    case = EvalCase(eval_id="negative-format", conversation=[invocation])

    assert TraceEvaluator._format_violations(case, expected=expected, actual=actual) == []


@pytest.mark.parametrize(("request_text", "expected", "actual", "violation"), [
    ("不要添加解释，只返回 JSON。", '{"ok":true}', "不是 JSON", "invalid_json"),
    ("不要省略字段，请使用 JSON 返回。", '{"ok":true}', "不是 JSON", "invalid_json"),
    ("不要赘述，只返回一行。", "合格回答", "第一行\n第二行", "not_single_line"),
    ("不要赘述，请使用 Markdown 表格。", "| A |\n| --- |\n| B |", "纯文本", "missing_markdown_table"),
    ("返回非空 JSON 对象。", '{"ok":true}', "不是 JSON", "invalid_json"),
])
def test_content_negation_does_not_suppress_positive_format_directives(
    request_text: str,
    expected: str,
    actual: str,
    violation: str,
) -> None:
    _load_public_interface()
    from eval_optimize_loop.loop.trace import TraceEvaluator
    from trpc_agent_sdk.evaluation import EvalCase
    from trpc_agent_sdk.evaluation import IntermediateData
    from trpc_agent_sdk.evaluation import Invocation
    from trpc_agent_sdk.types import Content
    from trpc_agent_sdk.types import Part

    invocation = Invocation(
        invocation_id="positive-format",
        user_content=Content(role="user", parts=[Part.from_text(text=request_text)]),
        final_response=Content(role="model", parts=[Part.from_text(text=expected)]),
        intermediate_data=IntermediateData(),
    )
    case = EvalCase(eval_id="positive-format", conversation=[invocation])

    assert violation in TraceEvaluator._format_violations(case, expected=expected, actual=actual)


def test_unevaluated_judge_is_attributed_as_evaluation_error() -> None:
    _load_public_interface()
    from eval_optimize_loop.loop.trace import TraceEvaluator
    from trpc_agent_sdk.evaluation import EvalCase
    from trpc_agent_sdk.evaluation import EvalCaseResult
    from trpc_agent_sdk.evaluation import EvalMetricResult
    from trpc_agent_sdk.evaluation import EvalMetricResultDetails
    from trpc_agent_sdk.evaluation import EvalMetricResultPerInvocation
    from trpc_agent_sdk.evaluation import EvalStatus
    from trpc_agent_sdk.evaluation import IntermediateData
    from trpc_agent_sdk.evaluation import Invocation
    from trpc_agent_sdk.types import Content
    from trpc_agent_sdk.types import Part

    expected = Invocation(
        invocation_id="expected",
        user_content=Content(role="user", parts=[Part.from_text(text="只返回 JSON。")]),
        final_response=Content(role="model", parts=[Part.from_text(text='{"ok":true}')]),
        intermediate_data=IntermediateData(),
    )
    actual = Invocation(
        invocation_id="actual",
        user_content=expected.user_content,
        final_response=Content(role="model", parts=[Part.from_text(text="judge unavailable")]),
        intermediate_data=IntermediateData(),
    )
    unavailable_metric = EvalMetricResult(
        metric_name="llm_rubric_response",
        threshold=1.0,
        score=None,
        eval_status=EvalStatus.NOT_EVALUATED,
        details=EvalMetricResultDetails(reason="all judge models failed"),
    )
    case = EvalCase(
        eval_id="judge-unavailable",
        eval_mode="trace",
        conversation=[expected],
        actual_conversation=[actual],
    )
    run = EvalCaseResult(
        eval_id=case.eval_id,
        final_eval_status=EvalStatus.NOT_EVALUATED,
        overall_eval_metric_results=[unavailable_metric],
        eval_metric_result_per_invocation=[
            EvalMetricResultPerInvocation(
                actual_invocation=actual,
                expected_invocation=expected,
                eval_metric_results=[unavailable_metric],
            )
        ],
        session_id="offline",
    )

    reasons = TraceEvaluator._attribute_failure(case, [run], {unavailable_metric.metric_name: unavailable_metric})

    assert reasons[0].category == "evaluation_error"
    assert reasons[0].evidence["reason"] == "all judge models failed"
    assert not any(reason.category in {"format_failure", "llm_rubric_failure"} for reason in reasons)
