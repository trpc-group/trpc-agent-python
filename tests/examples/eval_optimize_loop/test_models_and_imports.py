"""Strict model and architecture boundary tests for the loop example."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from examples.optimization.eval_optimize_loop.pipeline.configuration import (
    GateConfig,
    PipelineSettings,
)
from examples.optimization.eval_optimize_loop.pipeline.models import (
    CandidateProposal,
    CandidateRound,
    CaseComparison,
    CostSource,
    CostSummary,
    MetricDelta,
    MetricRun,
    OptimizationReport,
    RubricOutcome,
    Transition,
)
from examples.optimization.eval_optimize_loop.pipeline.schema import (
    add_exception_note,
    parse_strict_json,
    sanitized_text,
    validate_safe_component,
)

PIPELINE_DIR = Path(__file__).resolve().parents[3] / "examples" / "optimization" / "eval_optimize_loop" / "pipeline"


def test_models_forbid_unknown_and_non_finite_values() -> None:
    with pytest.raises(ValidationError):
        PipelineSettings.model_validate({"unexpected": True})
    with pytest.raises(ValidationError):
        GateConfig(epsilon=float("nan"))
    with pytest.raises(ValidationError):
        PipelineSettings(metric_weights={"metric": 0})
    with pytest.raises(ValidationError, match="v2"):
        OptimizationReport.model_validate({"schemaVersion": "v1"})


def test_pipeline_policy_scalars_reject_string_coercion() -> None:
    with pytest.raises(ValidationError):
        PipelineSettings.model_validate({"applyCandidate": "yes"})
    with pytest.raises(ValidationError):
        PipelineSettings.model_validate({"seed": "42"})
    with pytest.raises(ValidationError):
        GateConfig.model_validate({"maxNewHardFailures": "2"})
    with pytest.raises(ValidationError):
        GateConfig.model_validate({"overfitGuard": "false"})


def test_strict_json_and_path_components_reject_ambiguous_inputs() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        parse_strict_json('{"key": 1, "key": 2}')
    with pytest.raises(ValueError, match="non-finite"):
        parse_strict_json('{"value": NaN}')
    with pytest.raises(ValueError, match="non-finite"):
        parse_strict_json('{"value": 1e999}')
    for value in ("../escape", "a..b", "CON", "name.", "C:drive"):
        with pytest.raises(ValueError):
            validate_safe_component(value)


def test_candidate_requires_contiguous_accepted_history() -> None:
    with pytest.raises(ValidationError):
        CandidateProposal(
            algorithm="fake",
            baseline_prompts={"system": "old"},
            prompts={"system": "new"},
            changed=True,
            rounds=(CandidateRound(
                round=2,
                candidate_prompts={"system": "new"},
                accepted=True,
                score=1,
            ), ),
        )


def test_candidate_accounting_defaults_to_unknown() -> None:
    proposal = CandidateProposal(
        algorithm="custom",
        baseline_prompts={"system": "same"},
        prompts={"system": "same"},
        changed=False,
    )
    assert proposal.cost_sources == (CostSource(name="unreported", cost_usd=None), )
    with pytest.raises(ValidationError):
        CandidateRound(
            round=1,
            candidate_prompts={"system": "candidate"},
            accepted=True,
            score=1,
            cost_usd=-1,
        )


def test_candidate_round_distinguishes_skipped_rounds_and_rejects_invalid_accounting() -> None:
    skipped = CandidateProposal(
        algorithm="gepa_reflective",
        baseline_prompts={"system": "same"},
        prompts={"system": "same"},
        changed=False,
        rounds=(CandidateRound(
            round=1,
            candidate_prompts={},
            accepted=False,
            skip_reason="reflect-LM produced no usable new prompt",
        ), ),
    )
    assert skipped.rounds[0].score is None
    with pytest.raises(ValidationError, match="skip or error"):
        CandidateRound(round=1, candidate_prompts={}, accepted=False)
    with pytest.raises(ValidationError, match="round score"):
        CandidateRound(
            round=1,
            candidate_prompts={"system": "candidate"},
            accepted=False,
            score=-0.1,
        )
    with pytest.raises(ValidationError, match="token usage"):
        CandidateRound(
            round=1,
            candidate_prompts={"system": "candidate"},
            accepted=False,
            score=0,
            token_usage={"total": -1},
        )
    negative_metric = CandidateRound(
        round=1,
        candidate_prompts={"system": "candidate"},
        accepted=False,
        score=0,
        metric_scores={"log_likelihood": -2.5},
    )
    assert negative_metric.metric_scores == {"log_likelihood": -2.5}


def test_sanitized_text_redacts_structured_values_and_exception_messages() -> None:
    assert "tool-secret" not in sanitized_text(
        {"actual": {
            "apiKey": "tool-secret"
        }},
        max_text_chars=1000,
    )
    assert "error-secret" not in sanitized_text(
        RuntimeError('{"clientSecret":"error-secret"}'),
        max_text_chars=1000,
    )


def test_exception_notes_work_without_python_311_add_note() -> None:

    class LegacyError(RuntimeError):
        add_note = None

    error = LegacyError("primary")
    add_exception_note(error, "secondary diagnostic")
    assert error.__notes__ == ["secondary diagnostic"]


def test_rubric_and_cost_contracts_reject_inconsistent_aggregates() -> None:
    scaled = MetricRun(
        metric_name="quality_5_point",
        score=4,
        threshold=3,
        passed=True,
        rubrics=(RubricOutcome(id="quality", score=4, passed=True), ),
    )
    assert scaled.rubrics[0].score == 4
    with pytest.raises(ValidationError, match="finite"):
        RubricOutcome(id="quality", score=float("inf"), passed=True)
    with pytest.raises(ValidationError, match="metric threshold"):
        MetricRun(
            metric_name="quality",
            score=0,
            threshold=0.5,
            passed=False,
            rubrics=(RubricOutcome(id="quality", score=1, passed=False), ),
        )
    with pytest.raises(ValidationError, match="must be unknown"):
        CostSummary(
            sources=(CostSource(name="judge", cost_usd=None), ),
            total_cost_usd=0,
        )
    with pytest.raises(ValidationError, match="does not equal"):
        CostSummary(
            sources=(CostSource(name="judge", cost_usd=1), ),
            total_cost_usd=2,
        )


def test_comparison_contract_rejects_contradictory_delta_and_failure_flags() -> None:
    with pytest.raises(ValidationError, match="metric delta"):
        MetricDelta(metric_name="quality", baseline=0, candidate=1, delta=0)
    with pytest.raises(ValidationError, match="hard failure"):
        CaseComparison(
            case_id="critical",
            baseline_passed=True,
            candidate_passed=False,
            baseline_score=1,
            candidate_score=0,
            delta=-1,
            metrics=(MetricDelta(metric_name="quality", baseline=1, candidate=0, delta=-1), ),
            transition=Transition.NEW_FAIL,
            hard=True,
            new_hard_failure=False,
        )


def _pipeline_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            imports.add(node.module.split(".")[0])
    return imports


def test_pipeline_import_boundaries() -> None:
    allowed = {
        "artifacts": {"models", "schema"},
        "attribution": {"configuration", "models", "schema"},
        "backends": {"contracts", "models", "offline_evaluation", "schema"},
        "candidate_runtime": {
            "artifacts",
            "attribution",
            "contracts",
            "evaluation",
            "models",
            "prompt_workspace",
            "schema",
        },
        "configuration": {"models", "schema"},
        "contracts": {"models"},
        "costing": {"models"},
        "evaluation": {"models"},
        "evaluation_runtime": {
            "artifacts",
            "configuration",
            "contracts",
            "costing",
            "evaluation",
            "models",
        },
        "gate": {"configuration", "models"},
        "models": {"schema"},
        "offline_evaluation": set(),
        "orchestrator": {
            "artifacts",
            "attribution",
            "backends",
            "candidate_runtime",
            "contracts",
            "costing",
            "evaluation",
            "evaluation_runtime",
            "gate",
            "models",
            "preflight",
            "prompt_workspace",
            "reporting",
            "schema",
        },
        "preflight": {"artifacts", "configuration", "evaluation", "prompt_workspace", "schema"},
        "prompt_workspace": {"schema"},
        "reporting": {"artifacts", "configuration", "models"},
        "schema": set(),
    }
    graph: dict[str, set[str]] = {}
    for path in PIPELINE_DIR.glob("*.py"):
        if path.name == "__init__.py":
            continue
        module = path.stem
        graph[module] = _pipeline_imports(path)
    assert set(graph) == set(allowed)
    for module, dependencies in graph.items():
        unexpected = dependencies - allowed[module]
        assert not unexpected, f"{module} imports disallowed pipeline modules: {sorted(unexpected)}"

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str) -> None:
        if module in visiting:
            raise AssertionError(f"pipeline import cycle involving {module}")
        if module in visited:
            return
        visiting.add(module)
        for dependency in graph.get(module, set()):
            visit(dependency)
        visiting.remove(module)
        visited.add(module)

    for module in graph:
        visit(module)


def test_only_normalizer_unfolds_raw_evaluate_result() -> None:
    raw_result_attributes = {
        "results_by_eval_set_id",
        "eval_results_by_eval_id",
        "overall_eval_metric_results",
        "eval_metric_result_per_invocation",
    }
    for path in PIPELINE_DIR.glob("*.py"):
        if path.stem in {"__init__", "evaluation"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        used = {
            node.attr
            for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr in raw_result_attributes
        }
        assert not used, f"{path.name} unfolds raw EvaluateResult fields: {sorted(used)}"
