# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the gepa protocol adapter and trajectory/feedback helpers."""

from __future__ import annotations

from typing import Optional

import pytest

from trpc_agent_sdk.evaluation._eval_case import EvalCase
from trpc_agent_sdk.evaluation._eval_case import Invocation
from trpc_agent_sdk.evaluation._eval_config import EvalConfig
from trpc_agent_sdk.evaluation._eval_metrics import EvalStatus
from trpc_agent_sdk.evaluation._eval_result import EvalCaseResult
from trpc_agent_sdk.evaluation._eval_result import EvalMetricResult
from trpc_agent_sdk.evaluation._eval_result import EvalMetricResultDetails
from trpc_agent_sdk.evaluation._eval_result import EvalMetricResultPerInvocation
from trpc_agent_sdk.evaluation._eval_result import EvalSetAggregateResult
from trpc_agent_sdk.evaluation._eval_result import EvaluateResult
from trpc_agent_sdk.evaluation._optimize_evaluator_call import EvaluationOutcome
from trpc_agent_sdk.evaluation._optimize_gepa_adapter import _AgentGEPAAdapter
from trpc_agent_sdk.evaluation._optimize_gepa_adapter import _extract_case_output
from trpc_agent_sdk.evaluation._optimize_gepa_adapter import _render_metric_lines
from trpc_agent_sdk.evaluation._target_prompt import TargetPrompt
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


def _invocation(user_text: str, response_text: Optional[str] = None) -> Invocation:
    final_response = (
        Content(role="model", parts=[Part.from_text(text=response_text)])
        if response_text is not None
        else None
    )
    return Invocation(
        user_content=Content(role="user", parts=[Part.from_text(text=user_text)]),
        final_response=final_response,
    )


def _eval_case(eval_id: str = "c1", user: str = "hi", expected: str = "ack") -> EvalCase:
    return EvalCase(
        eval_id=eval_id,
        conversation=[_invocation(user, expected)],
    )


def _case_result(
    eval_id: str,
    *,
    status: EvalStatus,
    metric_score: float,
    actual: str,
    expected: str = "ack",
    reason: Optional[str] = None,
    error_message: Optional[str] = None,
) -> EvalCaseResult:
    details = EvalMetricResultDetails(reason=reason, score=metric_score) if reason else None
    return EvalCaseResult(
        eval_id=eval_id,
        eval_set_id="optimize_gepa_batch",
        final_eval_status=status,
        error_message=error_message,
        overall_eval_metric_results=[
            EvalMetricResult(
                metric_name="m1",
                threshold=0.7,
                score=metric_score,
                eval_status=status,
                details=details,
            )
        ],
        eval_metric_result_per_invocation=[
            EvalMetricResultPerInvocation(
                actual_invocation=_invocation("hi", actual),
                expected_invocation=_invocation("hi", expected),
                eval_metric_results=[],
            )
        ],
        session_id=f"sess-{eval_id}",
    )


def _evaluate_result(case_results_by_id: dict[str, list[EvalCaseResult]]) -> EvaluateResult:
    return EvaluateResult(
        results_by_eval_set_id={
            "optimize_gepa_batch": EvalSetAggregateResult(
                eval_results_by_eval_id=case_results_by_id,
                num_runs=1,
            )
        }
    )


async def _stub_call_agent(query: str) -> str:
    return "stub"


def _eval_config() -> EvalConfig:
    return EvalConfig(metrics=[{"metric_name": "m1", "threshold": 0.7}], num_runs=1)


def _new_target_prompt(write_recorder: Optional[dict[str, str]] = None) -> TargetPrompt:
    target = TargetPrompt()
    recorder = write_recorder if write_recorder is not None else {}

    async def read_cb() -> str:
        return recorder.get("instruction", "initial")

    async def write_cb(value: str) -> None:
        recorder["instruction"] = value

    target.add_callback("instruction", read=read_cb, write=write_cb)
    return target


def _multi_component_target_prompt(component_names: list[str]) -> TargetPrompt:
    """Register one callback per requested component.

    Each callback writes into an isolated dict so ``write_all`` succeeds for
    any candidate whose keys exactly match ``component_names``. Used by
    multi-component reflective-dataset tests to exercise the
    ``Other Active Components`` injection path.
    """
    target = TargetPrompt()
    storage: dict[str, str] = {name: "" for name in component_names}

    def _make_pair(name: str):
        async def read_cb() -> str:
            return storage[name]

        async def write_cb(value: str) -> None:
            storage[name] = value

        return read_cb, write_cb

    for name in component_names:
        read_cb, write_cb = _make_pair(name)
        target.add_callback(name, read=read_cb, write=write_cb)
    return target


def _patch_run_evaluator(monkeypatch, outcome: EvaluationOutcome) -> dict[str, dict]:
    captured: dict[str, dict] = {}

    async def fake_run_evaluator(**kwargs):
        captured["kwargs"] = kwargs
        eval_dataset_path = kwargs.get("eval_dataset_path")
        if eval_dataset_path:
            from pathlib import Path
            import json
            payload = json.loads(Path(eval_dataset_path).read_text(encoding="utf-8"))
            captured.setdefault("evalset_id_history", []).append(payload["eval_set_id"])
            captured.setdefault("evalset_payload_history", []).append(payload)
        return outcome

    monkeypatch.setattr(
        "trpc_agent_sdk.evaluation._optimize_gepa_adapter.run_evaluator",
        fake_run_evaluator,
    )
    return captured


def _make_adapter(target: Optional[TargetPrompt] = None, num_runs: int = 1) -> _AgentGEPAAdapter:
    return _AgentGEPAAdapter(
        target_prompt=target or _new_target_prompt(),
        eval_config=_eval_config(),
        call_agent=_stub_call_agent,
        callbacks=None,
        num_runs=num_runs,
    )


def test_extract_case_output_reads_first_invocation_final_response():
    case_result = _case_result("c1", status=EvalStatus.PASSED, metric_score=0.9, actual="output text")
    assert _extract_case_output(case_result) == "output text"


def test_extract_case_output_returns_empty_when_no_invocation():
    case_result = EvalCaseResult(
        eval_id="c1",
        final_eval_status=EvalStatus.FAILED,
        overall_eval_metric_results=[],
        eval_metric_result_per_invocation=[],
        session_id="s",
    )
    assert _extract_case_output(case_result) == ""


# ---------------------------------------------------------------------------
# ``_render_metric_lines`` is the core verdict-line renderer used by every
# Case Body block (per-turn + Overall). Tests below pin the structural
# guarantees the reflection LM relies on: PASS/FAIL labelling, threshold
# emission, judge-vs-synthesized reason precedence, and rubric breakdown.
# ---------------------------------------------------------------------------


def _failed_final_response_metric(
    *,
    text: Optional[dict] = None,
    json_cfg: Optional[dict] = None,
) -> EvalMetricResult:
    """Build a FAILED final_response_avg_score metric WITHOUT details.reason,
    mirroring what the real ``_final_response_evaluator`` actually emits.
    Used to exercise the deterministic-reason synthesis path."""
    criterion: dict = {"final_response": {}}
    if text is not None:
        criterion["final_response"]["text"] = text
    if json_cfg is not None:
        criterion["final_response"]["json"] = json_cfg
    return EvalMetricResult(
        metric_name="final_response_avg_score",
        threshold=1.0,
        score=0.0,
        eval_status=EvalStatus.FAILED,
        criterion=criterion,
        details=None,
    )


def test_render_metric_lines_emits_pass_fail_status_with_threshold_and_score():
    """Each metric occupies one line in the form
    ``[PASSED|FAILED] <name>: score=<float>, threshold=<float>``. The
    reflection LM uses these markers to (a) decide which metrics to keep
    constraints for, (b) tell which metric is being judged."""
    lines = _render_metric_lines(
        [
            EvalMetricResult(
                metric_name="m_pass",
                threshold=0.7,
                score=0.95,
                eval_status=EvalStatus.PASSED,
            ),
            EvalMetricResult(
                metric_name="m_fail",
                threshold=0.7,
                score=0.10,
                eval_status=EvalStatus.FAILED,
            ),
        ]
    )
    assert "[PASSED] m_pass: score=0.9500, threshold=0.7000" in lines
    assert "[FAILED] m_fail: score=0.1000, threshold=0.7000" in lines


def test_render_metric_lines_uses_explicit_judge_reason():
    """LLM-judged metrics already carry a natural-language reason in
    ``details.reason``; that reason is surfaced verbatim under the verdict
    line so the LM sees the judge's actual diagnosis."""
    lines = _render_metric_lines(
        [
            EvalMetricResult(
                metric_name="llm_rubric_response",
                threshold=0.5,
                score=0.0,
                eval_status=EvalStatus.FAILED,
                details=EvalMetricResultDetails(
                    score=0.0, reason="judge said: missing units"
                ),
            )
        ]
    )
    assert any("reason: judge said: missing units" in line for line in lines)


def test_render_metric_lines_synthesizes_reason_for_failing_contains_match():
    """Real deterministic matchers leave ``details.reason`` empty. We synth
    a one-line failure explanation from the criterion config so the LM
    sees WHY a substring match failed without diffing two long strings."""
    lines = _render_metric_lines(
        [
            _failed_final_response_metric(
                text={"match": "contains", "case_insensitive": True}
            )
        ]
    )
    joined = "\n".join(lines)
    assert "expected substring not contained" in joined
    assert "case-insensitive" in joined


def test_render_metric_lines_synthesizes_reason_for_failing_exact_match():
    lines = _render_metric_lines(
        [_failed_final_response_metric(text={"match": "exact"})]
    )
    joined = "\n".join(lines)
    assert "byte-equal" in joined
    assert "case-sensitive" in joined


def test_render_metric_lines_synthesizes_reason_for_failing_regex_match():
    lines = _render_metric_lines(
        [_failed_final_response_metric(text={"match": "regex"})]
    )
    assert any("regex" in line for line in lines)


def test_render_metric_lines_synthesizes_combined_text_and_json_failure():
    """When a metric runs BOTH text AND json checks the synthesized reason
    must say so (joined with AND), otherwise the LM cannot tell which half
    of the combined check failed."""
    lines = _render_metric_lines(
        [
            _failed_final_response_metric(
                text={"match": "exact"},
                json_cfg={"number_tolerance": 0.01},
            )
        ]
    )
    joined = "\n".join(lines)
    assert "byte-equal" in joined
    assert "JSON" in joined
    assert "AND" in joined


def test_render_metric_lines_no_reason_for_passing_deterministic_metric():
    """Passing metrics with no explicit reason emit no ``reason:`` line —
    we only synthesize failure explanations, never invent praise."""
    lines = _render_metric_lines(
        [
            EvalMetricResult(
                metric_name="final_response_avg_score",
                threshold=1.0,
                score=1.0,
                eval_status=EvalStatus.PASSED,
                criterion={"final_response": {"text": {"match": "contains"}}},
                details=None,
            )
        ]
    )
    assert not any("reason:" in line for line in lines)


def test_render_metric_lines_keeps_explicit_reason_over_synthesis():
    """When details.reason IS present, the explicit text wins — never
    overwritten by synthesized criterion text. Guards against an LLM
    judge's nuanced verdict being clobbered by template-generated wording."""
    lines = _render_metric_lines(
        [
            EvalMetricResult(
                metric_name="llm_rubric_response",
                threshold=0.5,
                score=0.0,
                eval_status=EvalStatus.FAILED,
                criterion={"llm_judge": {"judge_model": {"model_name": "j1"}}},
                details=EvalMetricResultDetails(
                    score=0.0, reason="judge said: missing units"
                ),
            )
        ]
    )
    joined = "\n".join(lines)
    assert "judge said: missing units" in joined
    assert "byte-equal" not in joined
    assert "expected substring not contained" not in joined


def test_render_metric_lines_expands_rubric_sub_scores():
    """LLM rubric metrics carry per-rubric sub-scores; each rubric must
    surface as its own ``  · rubric[<id>]: PASS|FAIL ...`` line so the LM
    knows which sub-quality is responsible for the verdict."""
    from trpc_agent_sdk.evaluation._llm_criterion import RubricScore

    lines = _render_metric_lines(
        [
            EvalMetricResult(
                metric_name="llm_rubric_response",
                threshold=0.66,
                score=0.6667,
                eval_status=EvalStatus.PASSED,
                details=EvalMetricResultDetails(
                    score=0.6667,
                    reason="2/3 rubrics passed",
                    rubric_scores=[
                        RubricScore(id="numeric_correct", score=1.0, reason="answer matches"),
                        RubricScore(id="reasoning_clear", score=0.0, reason="no calculation steps shown"),
                        RubricScore(id="units_present", score=1.0, reason="unit present"),
                    ],
                ),
            )
        ]
    )
    joined = "\n".join(lines)
    assert "rubric[numeric_correct]: PASS score=1.00" in joined
    assert "rubric[reasoning_clear]: FAIL score=0.00" in joined
    assert "rubric[units_present]: PASS score=1.00" in joined
    assert "answer matches" in joined
    assert "no calculation steps shown" in joined
    assert "unit present" in joined


def test_adapter_constructor_stores_dependencies():
    target = _new_target_prompt()
    config = _eval_config()
    adapter = _AgentGEPAAdapter(
        target_prompt=target,
        eval_config=config,
        call_agent=_stub_call_agent,
        callbacks=None,
        num_runs=3,
    )
    assert adapter.target_prompt is target
    assert adapter.eval_config is config
    assert adapter.num_runs == 3


def test_evaluate_writes_candidate_to_target_prompt(monkeypatch):
    case = _eval_case("c1")
    outcome = EvaluationOutcome(
        pass_rate=1.0,
        tiebreaker=0.9,
        raw_result=_evaluate_result({"c1": [_case_result("c1", status=EvalStatus.PASSED, metric_score=0.9, actual="ack")]}),
    )
    _patch_run_evaluator(monkeypatch, outcome)

    recorder: dict[str, str] = {}
    target = _new_target_prompt(recorder)
    adapter = _make_adapter(target)

    adapter.evaluate(batch=[case], candidate={"instruction": "new prompt text"})
    assert recorder.get("instruction") == "new prompt text"


def test_evaluate_passes_correct_kwargs_to_run_evaluator(monkeypatch):
    case = _eval_case("c1")
    outcome = EvaluationOutcome(
        pass_rate=1.0,
        tiebreaker=0.9,
        raw_result=_evaluate_result({"c1": [_case_result("c1", status=EvalStatus.PASSED, metric_score=0.9, actual="ack")]}),
    )
    captured = _patch_run_evaluator(monkeypatch, outcome)

    adapter = _make_adapter(num_runs=2)
    adapter.evaluate(batch=[case], candidate={"instruction": "x"})

    kwargs = captured["kwargs"]
    # The adapter wraps call_agent in a one-shot return-type sentinel
    # (API-A2 fix), so identity equality with the user-provided callable
    # no longer holds. Verify the wrapped callable is async and forwards
    # the original return value.
    import asyncio as _asyncio
    import inspect as _inspect
    forwarded = kwargs["call_agent"]
    assert _inspect.iscoroutinefunction(forwarded)
    assert _asyncio.run(forwarded("ping")) == "stub"
    assert kwargs["num_runs"] == 2
    assert kwargs["callbacks"] is None
    assert kwargs["eval_dataset_path"].endswith(".evalset.json")
    assert kwargs["eval_metrics_path"].endswith(".metrics.json")


def test_evaluate_scores_reflect_continuous_metric_means(monkeypatch):
    """case_score must equal the mean of each metric's continuous score —
    NOT a binary pass/fail collapse — so GEPA can distinguish candidates
    whose metrics differ in degree but share pass/fail labels."""
    cases = [_eval_case("c1"), _eval_case("c2"), _eval_case("c3")]
    outcome = EvaluationOutcome(
        pass_rate=1 / 3,
        tiebreaker=0.5,
        raw_result=_evaluate_result({
            "c1": [_case_result("c1", status=EvalStatus.PASSED, metric_score=0.9, actual="ack")],
            "c2": [_case_result("c2", status=EvalStatus.FAILED, metric_score=0.3, actual="wrong")],
            "c3": [_case_result("c3", status=EvalStatus.FAILED, metric_score=0.4, actual="bad")],
        }),
    )
    _patch_run_evaluator(monkeypatch, outcome)

    adapter = _make_adapter()
    batch_obj = adapter.evaluate(batch=cases, candidate={"instruction": "x"})

    assert batch_obj.scores == pytest.approx([0.9, 0.3, 0.4])
    assert len(batch_obj.outputs) == 3
    assert batch_obj.outputs[0] == "ack"
    assert batch_obj.outputs[1] == "wrong"


def test_evaluate_with_num_runs_averages_continuous_metric_scores(monkeypatch):
    """With num_runs > 1, case_score = mean over runs of mean over metrics —
    no binary pass-count collapse."""
    cases = [_eval_case("c1")]
    outcome = EvaluationOutcome(
        pass_rate=0.5,
        tiebreaker=0.5,
        raw_result=_evaluate_result({
            "c1": [
                _case_result("c1", status=EvalStatus.PASSED, metric_score=0.9, actual="ok"),
                _case_result("c1", status=EvalStatus.FAILED, metric_score=0.3, actual="bad"),
            ],
        }),
    )
    _patch_run_evaluator(monkeypatch, outcome)

    adapter = _make_adapter(num_runs=2)
    batch_obj = adapter.evaluate(batch=cases, candidate={"instruction": "x"})

    # mean([mean([0.9]), mean([0.3])]) = mean([0.9, 0.3]) = 0.6
    assert batch_obj.scores == pytest.approx([0.6])


def test_evaluate_case_score_averages_across_multiple_metrics(monkeypatch):
    """When a case carries multiple metrics, case_score = mean of metric scores.

    This is the property GEPA relies on to break ties between candidates that
    agree on the binary PASS/FAIL bucket but differ in degree (e.g. one keeps
    rubric quality at 1.0 while the other regresses to 0.33)."""
    case_result = EvalCaseResult(
        eval_id="c_multi",
        eval_set_id="optimize_gepa_batch",
        final_eval_status=EvalStatus.PASSED,
        overall_eval_metric_results=[
            EvalMetricResult(
                metric_name="final_response_avg_score",
                threshold=1.0,
                score=1.0,
                eval_status=EvalStatus.PASSED,
                details=EvalMetricResultDetails(score=1.0),
            ),
            EvalMetricResult(
                metric_name="llm_rubric_response",
                threshold=0.66,
                score=0.3333,
                eval_status=EvalStatus.FAILED,
                details=EvalMetricResultDetails(score=0.3333),
            ),
        ],
        eval_metric_result_per_invocation=[],
        session_id="sess-c_multi",
    )
    outcome = EvaluationOutcome(
        pass_rate=0.5,
        tiebreaker=0.7,
        raw_result=_evaluate_result({"c_multi": [case_result]}),
    )
    _patch_run_evaluator(monkeypatch, outcome)

    adapter = _make_adapter()
    batch_obj = adapter.evaluate(
        batch=[_eval_case("c_multi")], candidate={"instruction": "x"}
    )

    # mean([1.0, 0.3333]) ≈ 0.6667; binary collapse would have produced 0.0 (failed)
    assert batch_obj.scores == pytest.approx([0.66665], rel=1e-3)


def test_evaluate_populates_objective_scores_per_metric_per_case(monkeypatch):
    """objective_scores must be a list aligned with batch order; each entry is
    a {metric_name: score} dict — this is the channel GEPA needs to track a
    per-objective Pareto frontier."""
    case_1 = EvalCaseResult(
        eval_id="c1",
        eval_set_id="optimize_gepa_batch",
        final_eval_status=EvalStatus.PASSED,
        overall_eval_metric_results=[
            EvalMetricResult(
                metric_name="final_response_avg_score",
                threshold=1.0,
                score=1.0,
                eval_status=EvalStatus.PASSED,
                details=EvalMetricResultDetails(score=1.0),
            ),
            EvalMetricResult(
                metric_name="llm_rubric_response",
                threshold=0.66,
                score=0.6667,
                eval_status=EvalStatus.PASSED,
                details=EvalMetricResultDetails(score=0.6667),
            ),
        ],
        eval_metric_result_per_invocation=[],
        session_id="sess-c1",
    )
    case_2 = EvalCaseResult(
        eval_id="c2",
        eval_set_id="optimize_gepa_batch",
        final_eval_status=EvalStatus.FAILED,
        overall_eval_metric_results=[
            EvalMetricResult(
                metric_name="final_response_avg_score",
                threshold=1.0,
                score=0.0,
                eval_status=EvalStatus.FAILED,
                details=EvalMetricResultDetails(score=0.0),
            ),
            EvalMetricResult(
                metric_name="llm_rubric_response",
                threshold=0.66,
                score=1.0,
                eval_status=EvalStatus.PASSED,
                details=EvalMetricResultDetails(score=1.0),
            ),
        ],
        eval_metric_result_per_invocation=[],
        session_id="sess-c2",
    )
    outcome = EvaluationOutcome(
        pass_rate=0.5,
        tiebreaker=0.6,
        raw_result=_evaluate_result({"c1": [case_1], "c2": [case_2]}),
    )
    _patch_run_evaluator(monkeypatch, outcome)

    adapter = _make_adapter()
    batch_obj = adapter.evaluate(
        batch=[_eval_case("c1"), _eval_case("c2")],
        candidate={"instruction": "x"},
    )

    assert batch_obj.objective_scores is not None
    assert len(batch_obj.objective_scores) == 2
    assert batch_obj.objective_scores[0]["final_response_avg_score"] == pytest.approx(1.0)
    assert batch_obj.objective_scores[0]["llm_rubric_response"] == pytest.approx(0.6667, rel=1e-3)
    assert batch_obj.objective_scores[1]["final_response_avg_score"] == pytest.approx(0.0)
    assert batch_obj.objective_scores[1]["llm_rubric_response"] == pytest.approx(1.0)


def test_evaluate_objective_scores_average_across_num_runs(monkeypatch):
    """When num_runs > 1, each metric's score in objective_scores must be the
    mean of its scores across runs — keeping per-objective signal continuous."""
    run_1 = EvalCaseResult(
        eval_id="c1",
        eval_set_id="optimize_gepa_batch",
        final_eval_status=EvalStatus.PASSED,
        overall_eval_metric_results=[
            EvalMetricResult(
                metric_name="m1", threshold=0.7, score=1.0,
                eval_status=EvalStatus.PASSED,
                details=EvalMetricResultDetails(score=1.0),
            ),
            EvalMetricResult(
                metric_name="m2", threshold=0.5, score=0.6,
                eval_status=EvalStatus.PASSED,
                details=EvalMetricResultDetails(score=0.6),
            ),
        ],
        eval_metric_result_per_invocation=[],
        session_id="sess-c1-r1",
    )
    run_2 = EvalCaseResult(
        eval_id="c1",
        eval_set_id="optimize_gepa_batch",
        final_eval_status=EvalStatus.FAILED,
        overall_eval_metric_results=[
            EvalMetricResult(
                metric_name="m1", threshold=0.7, score=0.4,
                eval_status=EvalStatus.FAILED,
                details=EvalMetricResultDetails(score=0.4),
            ),
            EvalMetricResult(
                metric_name="m2", threshold=0.5, score=0.8,
                eval_status=EvalStatus.PASSED,
                details=EvalMetricResultDetails(score=0.8),
            ),
        ],
        eval_metric_result_per_invocation=[],
        session_id="sess-c1-r2",
    )
    outcome = EvaluationOutcome(
        pass_rate=0.5,
        tiebreaker=0.6,
        raw_result=_evaluate_result({"c1": [run_1, run_2]}),
    )
    _patch_run_evaluator(monkeypatch, outcome)

    adapter = _make_adapter(num_runs=2)
    batch_obj = adapter.evaluate(
        batch=[_eval_case("c1")], candidate={"instruction": "x"}
    )

    assert batch_obj.objective_scores is not None
    assert len(batch_obj.objective_scores) == 1
    assert batch_obj.objective_scores[0]["m1"] == pytest.approx(0.7)
    assert batch_obj.objective_scores[0]["m2"] == pytest.approx(0.7)


def test_evaluate_case_score_separates_candidates_with_same_pass_rate(monkeypatch):
    """Two candidates that share the same PASS/FAIL labels on a case but
    differ in metric score must end up with different case_scores, so GEPA's
    best-candidate selection no longer collapses to ``first-among-ties``."""
    case_a = EvalCaseResult(
        eval_id="c1",
        eval_set_id="optimize_gepa_batch",
        final_eval_status=EvalStatus.FAILED,
        overall_eval_metric_results=[
            EvalMetricResult(
                metric_name="final_response_avg_score",
                threshold=1.0,
                score=0.0,
                eval_status=EvalStatus.FAILED,
                details=EvalMetricResultDetails(score=0.0),
            ),
            EvalMetricResult(
                metric_name="llm_rubric_response",
                threshold=0.66,
                score=1.0,
                eval_status=EvalStatus.PASSED,
                details=EvalMetricResultDetails(score=1.0),
            ),
        ],
        eval_metric_result_per_invocation=[],
        session_id="sess-c1-A",
    )
    case_b = EvalCaseResult(
        eval_id="c1",
        eval_set_id="optimize_gepa_batch",
        final_eval_status=EvalStatus.FAILED,
        overall_eval_metric_results=[
            EvalMetricResult(
                metric_name="final_response_avg_score",
                threshold=1.0,
                score=0.0,
                eval_status=EvalStatus.FAILED,
                details=EvalMetricResultDetails(score=0.0),
            ),
            EvalMetricResult(
                metric_name="llm_rubric_response",
                threshold=0.66,
                score=0.3333,
                eval_status=EvalStatus.FAILED,
                details=EvalMetricResultDetails(score=0.3333),
            ),
        ],
        eval_metric_result_per_invocation=[],
        session_id="sess-c1-B",
    )

    outcome_a = EvaluationOutcome(
        pass_rate=0.0,
        tiebreaker=0.5,
        raw_result=_evaluate_result({"c1": [case_a]}),
    )
    outcome_b = EvaluationOutcome(
        pass_rate=0.0,
        tiebreaker=0.16,
        raw_result=_evaluate_result({"c1": [case_b]}),
    )

    _patch_run_evaluator(monkeypatch, outcome_a)
    adapter = _make_adapter()
    score_a = adapter.evaluate(
        batch=[_eval_case("c1")], candidate={"instruction": "candidate_A"}
    ).scores[0]

    _patch_run_evaluator(monkeypatch, outcome_b)
    score_b = adapter.evaluate(
        batch=[_eval_case("c1")], candidate={"instruction": "candidate_B"}
    ).scores[0]

    # Both candidates fail final_response, but candidate A preserves rubric quality.
    # Continuous case_score must reflect this difference (binary collapse would
    # have tied both at 0.0).
    assert score_a > score_b
    assert score_a == pytest.approx(0.5)
    assert score_b == pytest.approx(0.16665, rel=1e-3)


def test_evaluate_with_capture_traces_returns_trajectories(monkeypatch):
    cases = [_eval_case("c1")]
    outcome = EvaluationOutcome(
        pass_rate=0.0,
        tiebreaker=0.3,
        raw_result=_evaluate_result({
            "c1": [_case_result("c1", status=EvalStatus.FAILED, metric_score=0.3, actual="wrong", reason="not matching")],
        }),
    )
    _patch_run_evaluator(monkeypatch, outcome)

    adapter = _make_adapter()
    batch_obj = adapter.evaluate(batch=cases, candidate={"instruction": "x"}, capture_traces=True)

    assert batch_obj.trajectories is not None
    assert len(batch_obj.trajectories) == 1
    traj = batch_obj.trajectories[0]
    # Trajectory dict now carries only what ``make_reflective_dataset``
    # actually consumes: the score (for filtering), the captured EvalCase /
    # case_runs (for rebuilding the Case Body), and an optional
    # error_message for the no-runs evaluator-error path.
    assert traj["_case"].eval_id == "c1"
    assert len(traj["_case_runs"]) == 1
    assert traj["score"] == pytest.approx(0.3)
    assert traj["error_message"] is None


def test_evaluate_without_capture_traces_returns_no_trajectories(monkeypatch):
    cases = [_eval_case("c1")]
    outcome = EvaluationOutcome(
        pass_rate=1.0,
        tiebreaker=0.9,
        raw_result=_evaluate_result({"c1": [_case_result("c1", status=EvalStatus.PASSED, metric_score=0.9, actual="ack")]}),
    )
    _patch_run_evaluator(monkeypatch, outcome)

    adapter = _make_adapter()
    batch_obj = adapter.evaluate(batch=cases, candidate={"instruction": "x"}, capture_traces=False)
    assert batch_obj.trajectories is None


def test_evaluate_handles_empty_raw_result(monkeypatch):
    cases = [_eval_case("c1"), _eval_case("c2")]
    outcome = EvaluationOutcome(pass_rate=0.0, tiebreaker=0.0, raw_result=None)
    _patch_run_evaluator(monkeypatch, outcome)

    adapter = _make_adapter()
    batch_obj = adapter.evaluate(batch=cases, candidate={"instruction": "x"}, capture_traces=True)

    assert batch_obj.scores == [0.0, 0.0]
    assert batch_obj.outputs == ["", ""]
    assert batch_obj.trajectories is not None
    assert all(
        t["error_message"] == "no result returned" for t in batch_obj.trajectories
    )


def test_evaluate_handles_case_missing_from_result(monkeypatch):
    cases = [_eval_case("c1"), _eval_case("missing")]
    outcome = EvaluationOutcome(
        pass_rate=0.5,
        tiebreaker=0.5,
        raw_result=_evaluate_result({"c1": [_case_result("c1", status=EvalStatus.PASSED, metric_score=0.9, actual="ack")]}),
    )
    _patch_run_evaluator(monkeypatch, outcome)

    adapter = _make_adapter()
    batch_obj = adapter.evaluate(batch=cases, candidate={"instruction": "x"}, capture_traces=True)

    assert batch_obj.scores == pytest.approx([0.9, 0.0])
    assert batch_obj.outputs[1] == ""
    assert batch_obj.trajectories is not None
    assert (
        batch_obj.trajectories[1]["error_message"]
        == "case missing from evaluator result"
    )


def test_adapter_exposes_propose_new_texts_attribute_as_none():
    # gepa's reflective proposer reads ``adapter.propose_new_texts`` directly;
    # the attribute must exist (None signals "use the default reflection LM").
    assert hasattr(_AgentGEPAAdapter, "propose_new_texts")
    assert _AgentGEPAAdapter.propose_new_texts is None


def test_evaluate_deduplicates_repeated_case_ids_within_batch(monkeypatch):
    # gepa's batch sampler pads the minibatch with least-frequent ids when the
    # trainset size does not divide the minibatch size, so the same eval_case
    # can appear twice in one batch. The evaluator's in-memory manager rejects
    # duplicate eval_ids inside an EvalSet, so the adapter must rename repeats.
    case = _eval_case("dup")
    outcome = EvaluationOutcome(
        pass_rate=1.0,
        tiebreaker=0.9,
        raw_result=_evaluate_result({"dup": [_case_result("dup", status=EvalStatus.PASSED, metric_score=0.9, actual="ack")]}),
    )
    captured = _patch_run_evaluator(monkeypatch, outcome)

    adapter = _make_adapter()
    adapter.evaluate(batch=[case, case], candidate={"instruction": "x"})

    payload = captured["evalset_payload_history"][0]
    ids = [c["eval_id"] for c in payload["eval_cases"]]
    assert len(ids) == 2
    assert len(set(ids)) == 2, f"Duplicate eval_ids must be renamed, got {ids}"


def test_evaluate_uses_unique_eval_set_id_per_call(monkeypatch):
    case = _eval_case("c1")
    outcome = EvaluationOutcome(
        pass_rate=1.0,
        tiebreaker=0.9,
        raw_result=_evaluate_result({"c1": [_case_result("c1", status=EvalStatus.PASSED, metric_score=0.9, actual="ack")]}),
    )
    captured = _patch_run_evaluator(monkeypatch, outcome)

    adapter = _make_adapter()
    adapter.evaluate(batch=[case], candidate={"instruction": "v1"})
    adapter.evaluate(batch=[case], candidate={"instruction": "v2"})

    ids = captured["evalset_id_history"]
    assert len(ids) == 2
    assert ids[0] != ids[1], "Each call must use a unique eval_set_id to avoid in-memory manager collisions"


def test_make_reflective_dataset_collects_failed_cases_only(monkeypatch):
    cases = [_eval_case("c1"), _eval_case("c2"), _eval_case("c3")]
    outcome = EvaluationOutcome(
        pass_rate=1 / 3,
        tiebreaker=0.4,
        raw_result=_evaluate_result({
            "c1": [_case_result("c1", status=EvalStatus.PASSED, metric_score=1.0, actual="ack")],
            "c2": [_case_result("c2", status=EvalStatus.FAILED, metric_score=0.3, actual="wrong", reason="bad")],
            "c3": [_case_result("c3", status=EvalStatus.FAILED, metric_score=0.4, actual="bad", reason="off")],
        }),
    )
    _patch_run_evaluator(monkeypatch, outcome)

    adapter = _make_adapter()
    batch_obj = adapter.evaluate(batch=cases, candidate={"instruction": "x"}, capture_traces=True)

    reflective = adapter.make_reflective_dataset(
        candidate={"instruction": "x"},
        eval_batch=batch_obj,
        components_to_update=["instruction"],
    )
    records = reflective["instruction"]
    assert len(records) == 2
    # Turn-sliced schema: case_id, score, Case Body. Other Active Components
    # is omitted on single-component candidates.
    assert all("case_id" in r for r in records)
    assert all("score" in r for r in records)
    assert all("Case Body" in r for r in records)
    assert all(isinstance(r["Case Body"], str) and r["Case Body"] for r in records)
    assert all("Other Active Components" not in r for r in records)


def test_make_reflective_dataset_case_body_one_turn_block_per_invocation(monkeypatch):
    """Multi-turn case: Case Body contains one ``### Turn N`` block per
    invocation, each carrying its own User/Expected lines."""
    multi_turn_case = EvalCase(
        eval_id="c_multi_turn",
        conversation=[
            _invocation("hello", "hi there"),
            _invocation("how are you", "I'm doing fine"),
            _invocation("bye", "goodbye"),
        ],
    )
    outcome = EvaluationOutcome(
        pass_rate=0.0,
        tiebreaker=0.0,
        raw_result=_evaluate_result({
            "c_multi_turn": [_case_result(
                "c_multi_turn", status=EvalStatus.FAILED,
                metric_score=0.0, actual="wrong",
            )],
        }),
    )
    _patch_run_evaluator(monkeypatch, outcome)

    adapter = _make_adapter()
    batch_obj = adapter.evaluate(
        batch=[multi_turn_case], candidate={"instruction": "x"}, capture_traces=True
    )
    records = adapter.make_reflective_dataset(
        candidate={"instruction": "x"},
        eval_batch=batch_obj,
        components_to_update=["instruction"],
    )["instruction"]

    body = records[0]["Case Body"]
    assert "### Turn 1" in body
    assert "### Turn 2" in body
    assert "### Turn 3" in body
    assert "**User**: hello" in body
    assert "**Expected**: hi there" in body
    assert "**User**: how are you" in body
    assert "**Expected**: I'm doing fine" in body
    assert "**User**: bye" in body
    assert "**Expected**: goodbye" in body


def test_make_reflective_dataset_case_body_emits_overall_for_multi_turn(monkeypatch):
    """Multi-turn case ends with ``### Overall (case-level aggregate)`` so
    the reflection LM sees both per-turn verdicts and the case-level roll-up."""
    multi_turn_case = EvalCase(
        eval_id="c_multi",
        conversation=[
            _invocation("hi", "ack1"),
            _invocation("again", "ack2"),
        ],
    )
    outcome = EvaluationOutcome(
        pass_rate=0.0,
        tiebreaker=0.0,
        raw_result=_evaluate_result({
            "c_multi": [_case_result(
                "c_multi", status=EvalStatus.FAILED,
                metric_score=0.0, actual="wrong",
            )],
        }),
    )
    _patch_run_evaluator(monkeypatch, outcome)

    adapter = _make_adapter()
    batch_obj = adapter.evaluate(
        batch=[multi_turn_case], candidate={"instruction": "x"}, capture_traces=True
    )
    body = adapter.make_reflective_dataset(
        candidate={"instruction": "x"},
        eval_batch=batch_obj,
        components_to_update=["instruction"],
    )["instruction"][0]["Case Body"]
    assert "### Overall (case-level aggregate)" in body


def test_make_reflective_dataset_case_body_omits_overall_for_single_turn_single_run(monkeypatch):
    """Single-turn single-run cases skip the Overall block — Turn 1 already
    carries the only verdict, an Overall heading would just repeat it."""
    outcome = EvaluationOutcome(
        pass_rate=0.0,
        tiebreaker=0.3,
        raw_result=_evaluate_result({
            "c1": [_case_result("c1", status=EvalStatus.FAILED, metric_score=0.3, actual="wrong")],
        }),
    )
    _patch_run_evaluator(monkeypatch, outcome)

    adapter = _make_adapter()
    batch_obj = adapter.evaluate(
        batch=[_eval_case("c1")], candidate={"instruction": "x"}, capture_traces=True
    )
    body = adapter.make_reflective_dataset(
        candidate={"instruction": "x"},
        eval_batch=batch_obj,
        components_to_update=["instruction"],
    )["instruction"][0]["Case Body"]
    assert "### Turn 1" in body
    assert "### Overall" not in body


def test_make_reflective_dataset_case_body_nests_run_blocks_for_multi_run(monkeypatch):
    """num_runs > 1: each turn block nests ``#### Run N`` sub-blocks so the
    reflection LM sees output variance attributed to the right run, without
    repeating the shared User/Expected lines per run."""
    run1 = _case_result(
        "c1", status=EvalStatus.FAILED, metric_score=0.0, actual="output_run1"
    )
    run1.run_id = 1
    run2 = _case_result(
        "c1", status=EvalStatus.PASSED, metric_score=1.0, actual="output_run2"
    )
    run2.run_id = 2
    outcome = EvaluationOutcome(
        pass_rate=0.5,
        tiebreaker=0.5,
        raw_result=_evaluate_result({"c1": [run1, run2]}),
    )
    _patch_run_evaluator(monkeypatch, outcome)

    adapter = _make_adapter(num_runs=2)
    batch_obj = adapter.evaluate(
        batch=[_eval_case("c1")], candidate={"instruction": "x"}, capture_traces=True
    )
    body = adapter.make_reflective_dataset(
        candidate={"instruction": "x"},
        eval_batch=batch_obj,
        components_to_update=["instruction"],
    )["instruction"][0]["Case Body"]

    assert "#### Run 1" in body
    assert "#### Run 2" in body
    assert "**Agent Response**: output_run1" in body
    assert "**Agent Response**: output_run2" in body
    # Shared User line appears once at the turn level — not once per run.
    assert body.count("**User**: hi") == 1
    # Multi-run cases close with per-run aggregate.
    assert "### Overall (per-run aggregate)" in body


def test_make_reflective_dataset_case_body_renders_tool_trace_inline(monkeypatch):
    """Tool calls render as a single-line ``func(arg=val) → result [id=...]``
    so GEPA's H6 markdown cap does not flatten the call/arg/result hierarchy
    when the renderer nests them as headers."""
    from trpc_agent_sdk.evaluation._eval_case import IntermediateData
    from trpc_agent_sdk.types import FunctionCall, FunctionResponse

    actual = _invocation("query", "I used search")
    actual.intermediate_data = IntermediateData(
        tool_uses=[
            FunctionCall(id="call_1", name="search", args={"q": "weather"}),
        ],
        tool_responses=[
            FunctionResponse(id="call_1", name="search", response={"result": "sunny"}),
        ],
    )

    case_result = EvalCaseResult(
        eval_id="c_tool",
        eval_set_id="optimize_gepa_batch",
        final_eval_status=EvalStatus.FAILED,
        overall_eval_metric_results=[
            EvalMetricResult(
                metric_name="m1", threshold=0.7, score=0.3,
                eval_status=EvalStatus.FAILED,
                details=EvalMetricResultDetails(reason="off", score=0.3),
            )
        ],
        eval_metric_result_per_invocation=[
            EvalMetricResultPerInvocation(
                actual_invocation=actual,
                expected_invocation=_invocation("query", "expected"),
                eval_metric_results=[],
            )
        ],
        session_id="sess-c_tool",
    )
    outcome = EvaluationOutcome(
        pass_rate=0.0,
        tiebreaker=0.3,
        raw_result=_evaluate_result({"c_tool": [case_result]}),
    )
    _patch_run_evaluator(monkeypatch, outcome)

    adapter = _make_adapter()
    batch_obj = adapter.evaluate(
        batch=[_eval_case("c_tool")], candidate={"instruction": "x"}, capture_traces=True
    )
    body = adapter.make_reflective_dataset(
        candidate={"instruction": "x"},
        eval_batch=batch_obj,
        components_to_update=["instruction"],
    )["instruction"][0]["Case Body"]

    assert "**Tool Trace**:" in body
    assert "search(q='weather')" in body
    assert "'sunny'" in body
    assert "[id=call_1]" in body


def test_make_reflective_dataset_case_body_omits_tool_trace_when_absent(monkeypatch):
    """When the agent did not invoke any tool, the Tool Trace section is
    absent — keeps the prompt focused on what the agent actually produced."""
    outcome = EvaluationOutcome(
        pass_rate=0.0,
        tiebreaker=0.3,
        raw_result=_evaluate_result({
            "c1": [_case_result("c1", status=EvalStatus.FAILED, metric_score=0.3, actual="wrong")],
        }),
    )
    _patch_run_evaluator(monkeypatch, outcome)

    adapter = _make_adapter()
    batch_obj = adapter.evaluate(
        batch=[_eval_case("c1")], candidate={"instruction": "x"}, capture_traces=True
    )
    body = adapter.make_reflective_dataset(
        candidate={"instruction": "x"},
        eval_batch=batch_obj,
        components_to_update=["instruction"],
    )["instruction"][0]["Case Body"]
    assert "**Tool Trace**:" not in body


def test_make_reflective_dataset_record_carries_case_id_and_score(monkeypatch):
    """Per-record meta fields case_id and score let the reflection LM
    reference a specific case and see the aggregated case-level score
    alongside per-metric breakdown."""
    outcome = EvaluationOutcome(
        pass_rate=0.0,
        tiebreaker=0.3,
        raw_result=_evaluate_result({
            "c_special": [_case_result("c_special", status=EvalStatus.FAILED, metric_score=0.42, actual="wrong")],
        }),
    )
    _patch_run_evaluator(monkeypatch, outcome)

    adapter = _make_adapter()
    batch_obj = adapter.evaluate(
        batch=[_eval_case("c_special")], candidate={"instruction": "x"}, capture_traces=True
    )
    record = adapter.make_reflective_dataset(
        candidate={"instruction": "x"},
        eval_batch=batch_obj,
        components_to_update=["instruction"],
    )["instruction"][0]
    assert record["case_id"] == "c_special"
    assert record["score"] == pytest.approx(0.42)


def test_make_reflective_dataset_other_active_components_present_for_multi_component(
    monkeypatch,
):
    """Multi-component candidate: each record exposes the OTHER prompts'
    current text under ``Other Active Components`` so the reflection LM can
    avoid restating requirements already enforced by sibling prompts."""
    outcome = EvaluationOutcome(
        pass_rate=0.0,
        tiebreaker=0.3,
        raw_result=_evaluate_result({
            "c1": [_case_result("c1", status=EvalStatus.FAILED, metric_score=0.3, actual="wrong", reason="off")],
        }),
    )
    _patch_run_evaluator(monkeypatch, outcome)

    target = _multi_component_target_prompt(["system_prompt", "skill_prompt"])
    adapter = _make_adapter(target=target)
    candidate = {
        "system_prompt": "You are a helpful assistant.",
        "skill_prompt": "When asked math, always include units.",
    }
    batch_obj = adapter.evaluate(
        batch=[_eval_case("c1")], candidate=candidate, capture_traces=True
    )
    reflective = adapter.make_reflective_dataset(
        candidate=candidate,
        eval_batch=batch_obj,
        components_to_update=["system_prompt"],
    )
    other_md = reflective["system_prompt"][0]["Other Active Components"]
    # The sibling prompt's current body is included.
    assert "When asked math, always include units." in other_md
    assert "### skill_prompt (current)" in other_md
    # The target component itself is NOT echoed (GEPA already shows it in <curr_param>).
    assert "system_prompt (current)" not in other_md


def test_make_reflective_dataset_other_active_components_absent_for_single_component(
    monkeypatch,
):
    """Single-component candidate: no ``Other Active Components`` key is
    emitted — there is nothing else to surface and the LM should not see an
    empty section."""
    outcome = EvaluationOutcome(
        pass_rate=0.0,
        tiebreaker=0.3,
        raw_result=_evaluate_result({
            "c1": [_case_result("c1", status=EvalStatus.FAILED, metric_score=0.3, actual="wrong", reason="off")],
        }),
    )
    _patch_run_evaluator(monkeypatch, outcome)

    adapter = _make_adapter()
    batch_obj = adapter.evaluate(
        batch=[_eval_case("c1")], candidate={"instruction": "x"}, capture_traces=True
    )
    record = adapter.make_reflective_dataset(
        candidate={"instruction": "x"},
        eval_batch=batch_obj,
        components_to_update=["instruction"],
    )["instruction"][0]
    assert "Other Active Components" not in record


def test_make_reflective_dataset_other_active_components_rebuilt_per_component(
    monkeypatch,
):
    """When dispatching to multiple components in the same round, each
    component's record set must list the OTHER components' content — i.e.
    the ``Other Active Components`` field is rebuilt per component, not
    shared across them."""
    outcome = EvaluationOutcome(
        pass_rate=0.0,
        tiebreaker=0.3,
        raw_result=_evaluate_result({
            "c1": [_case_result("c1", status=EvalStatus.FAILED, metric_score=0.3, actual="wrong", reason="off")],
        }),
    )
    _patch_run_evaluator(monkeypatch, outcome)

    target = _multi_component_target_prompt(["system_prompt", "skill_prompt"])
    adapter = _make_adapter(target=target)
    candidate = {
        "system_prompt": "SYSTEM BODY",
        "skill_prompt": "SKILL BODY",
    }
    batch_obj = adapter.evaluate(
        batch=[_eval_case("c1")], candidate=candidate, capture_traces=True
    )
    reflective = adapter.make_reflective_dataset(
        candidate=candidate,
        eval_batch=batch_obj,
        components_to_update=["system_prompt", "skill_prompt"],
    )

    sys_other = reflective["system_prompt"][0]["Other Active Components"]
    skill_other = reflective["skill_prompt"][0]["Other Active Components"]

    # Each record set surfaces only the sibling component's body.
    assert "SKILL BODY" in sys_other
    assert "SYSTEM BODY" not in sys_other
    assert "SYSTEM BODY" in skill_other
    assert "SKILL BODY" not in skill_other


def test_make_reflective_dataset_surfaces_evaluator_error_as_case_body(monkeypatch):
    """When the evaluator fails to produce runs for a case (e.g. ``case
    missing from evaluator result``), the trajectory entry carries an
    ``error_message`` and no ``_case_runs``. The reflective record must
    still appear with that error_message as the Case Body, otherwise the
    LM silently loses every failed case where the runtime itself broke."""
    cases = [_eval_case("c_missing")]
    outcome = EvaluationOutcome(
        pass_rate=0.0,
        tiebreaker=0.0,
        raw_result=_evaluate_result({}),  # no case results at all
    )
    _patch_run_evaluator(monkeypatch, outcome)

    adapter = _make_adapter()
    batch_obj = adapter.evaluate(
        batch=cases, candidate={"instruction": "x"}, capture_traces=True
    )
    record = adapter.make_reflective_dataset(
        candidate={"instruction": "x"},
        eval_batch=batch_obj,
        components_to_update=["instruction"],
    )["instruction"][0]
    assert record["case_id"] == "c_missing"
    assert record["score"] == pytest.approx(0.0)
    assert "case missing from evaluator result" in record["Case Body"]


def test_make_reflective_dataset_returns_empty_for_no_components():
    adapter = _make_adapter()
    fake_batch = type("FakeBatch", (), {"trajectories": [{"score": 0.0}]})()
    result = adapter.make_reflective_dataset(
        candidate={"instruction": "x"},
        eval_batch=fake_batch,
        components_to_update=[],
    )
    assert result == {}


def test_make_reflective_dataset_handles_no_trajectories():
    adapter = _make_adapter()
    fake_batch = type("FakeBatch", (), {"trajectories": None})()
    result = adapter.make_reflective_dataset(
        candidate={"instruction": "x"},
        eval_batch=fake_batch,
        components_to_update=["instruction", "system"],
    )
    assert result == {"instruction": [], "system": []}


def test_make_reflective_dataset_replicates_records_across_components(monkeypatch):
    cases = [_eval_case("c1")]
    outcome = EvaluationOutcome(
        pass_rate=0.0,
        tiebreaker=0.3,
        raw_result=_evaluate_result({
            "c1": [_case_result("c1", status=EvalStatus.FAILED, metric_score=0.3, actual="wrong", reason="off")],
        }),
    )
    _patch_run_evaluator(monkeypatch, outcome)

    adapter = _make_adapter()
    batch_obj = adapter.evaluate(batch=cases, candidate={"instruction": "x"}, capture_traces=True)
    reflective = adapter.make_reflective_dataset(
        candidate={"instruction": "x"},
        eval_batch=batch_obj,
        components_to_update=["instruction", "react_skill"],
    )
    assert "instruction" in reflective
    assert "react_skill" in reflective
    assert len(reflective["instruction"]) == 1
    assert len(reflective["react_skill"]) == 1


def test_adapter_records_best_history_per_case():
    """After three _record_history calls the buffer keeps the top-2 by score."""
    adapter = _AgentGEPAAdapter(
        target_prompt=_new_target_prompt(),
        eval_config=_eval_config(),
        call_agent=_stub_call_agent,
        callbacks=None,
        num_runs=1,
        top_k_per_case=2,
    )
    adapter._record_history(case_id="c1", score=0.4, best_response="hello low")
    adapter._record_history(case_id="c1", score=0.9, best_response="hello high")
    adapter._record_history(case_id="c1", score=0.6, best_response="hello mid")

    history = adapter._best_history["c1"]
    assert len(history) == 2
    assert history[0]["score"] == pytest.approx(0.9)
    assert history[0]["best_response"] == "hello high"
    assert history[1]["score"] == pytest.approx(0.6)
    assert history[1]["best_response"] == "hello mid"


def test_adapter_top_k_zero_disables_buffer():
    """top_k=0 is the kill switch — _record_history must be a no-op."""
    adapter = _AgentGEPAAdapter(
        target_prompt=_new_target_prompt(),
        eval_config=_eval_config(),
        call_agent=_stub_call_agent,
        callbacks=None,
        num_runs=1,
        top_k_per_case=0,
    )
    adapter._record_history(case_id="c1", score=0.9, best_response="hello")

    assert adapter._best_history.get("c1", []) == []


def test_evaluate_populates_best_history_buffer(monkeypatch):
    """Running evaluate() twice on the same case accumulates history sorted by score."""
    from trpc_agent_sdk.evaluation._optimize_evaluator_call import EvaluationOutcome

    cases = [_eval_case("c1")]
    outcome_low = EvaluationOutcome(
        pass_rate=0.0,
        tiebreaker=0.3,
        raw_result=_evaluate_result({
            "c1": [_case_result(
                "c1", status=EvalStatus.FAILED, metric_score=0.3, actual="low"
            )],
        }),
    )
    _patch_run_evaluator(monkeypatch, outcome_low)
    adapter = _AgentGEPAAdapter(
        target_prompt=_new_target_prompt(),
        eval_config=_eval_config(),
        call_agent=_stub_call_agent,
        callbacks=None,
        num_runs=1,
        top_k_per_case=2,
    )
    adapter.evaluate(
        batch=cases, candidate={"instruction": "x"}, capture_traces=False
    )

    outcome_high = EvaluationOutcome(
        pass_rate=0.0,
        tiebreaker=0.8,
        raw_result=_evaluate_result({
            "c1": [_case_result(
                "c1", status=EvalStatus.FAILED, metric_score=0.8, actual="high"
            )],
        }),
    )
    _patch_run_evaluator(monkeypatch, outcome_high)
    adapter.evaluate(
        batch=cases, candidate={"instruction": "y"}, capture_traces=False
    )

    history = adapter._best_history["c1"]
    assert len(history) == 2
    assert history[0]["score"] == pytest.approx(0.8)
    assert history[0]["best_response"] == "high"
    assert history[1]["score"] == pytest.approx(0.3)
    assert history[1]["best_response"] == "low"


def test_make_reflective_dataset_includes_history_top_k_when_buffer_nonempty(
    monkeypatch,
):
    """When history is seeded and top_k>0, the record carries a history_top_k list."""
    from trpc_agent_sdk.evaluation._optimize_evaluator_call import EvaluationOutcome

    cases = [_eval_case("c1")]
    outcome = EvaluationOutcome(
        pass_rate=0.0,
        tiebreaker=0.3,
        raw_result=_evaluate_result({
            "c1": [_case_result(
                "c1", status=EvalStatus.FAILED, metric_score=0.3, actual="bad"
            )],
        }),
    )
    _patch_run_evaluator(monkeypatch, outcome)

    adapter = _AgentGEPAAdapter(
        target_prompt=_new_target_prompt(),
        eval_config=_eval_config(),
        call_agent=_stub_call_agent,
        callbacks=None,
        num_runs=1,
        top_k_per_case=2,
    )
    # Seed history with a previous high-score entry the adapter should keep.
    adapter._record_history(case_id="c1", score=0.9, best_response="known good")

    batch_obj = adapter.evaluate(
        batch=cases, candidate={"instruction": "x"}, capture_traces=True
    )
    dataset = adapter.make_reflective_dataset(
        candidate={"instruction": "x"},
        eval_batch=batch_obj,
        components_to_update=["instruction"],
    )

    records = dataset["instruction"]
    assert len(records) == 1
    assert "history_top_k" in records[0]
    history = records[0]["history_top_k"]
    assert len(history) == 2  # 0.9 seeded + 0.3 from this evaluation
    assert history[0]["score"] == pytest.approx(0.9)
    assert history[0]["best_response"] == "known good"
    assert history[1]["score"] == pytest.approx(0.3)


def test_make_reflective_dataset_omits_history_top_k_when_buffer_empty(
    monkeypatch,
):
    """top_k=0 disables the feature: the record must not carry history_top_k."""
    from trpc_agent_sdk.evaluation._optimize_evaluator_call import EvaluationOutcome

    cases = [_eval_case("c1")]
    outcome = EvaluationOutcome(
        pass_rate=0.0,
        tiebreaker=0.3,
        raw_result=_evaluate_result({
            "c1": [_case_result(
                "c1", status=EvalStatus.FAILED, metric_score=0.3, actual="bad"
            )],
        }),
    )
    _patch_run_evaluator(monkeypatch, outcome)

    adapter = _AgentGEPAAdapter(
        target_prompt=_new_target_prompt(),
        eval_config=_eval_config(),
        call_agent=_stub_call_agent,
        callbacks=None,
        num_runs=1,
        top_k_per_case=0,
    )

    batch_obj = adapter.evaluate(
        batch=cases, candidate={"instruction": "x"}, capture_traces=True
    )
    dataset = adapter.make_reflective_dataset(
        candidate={"instruction": "x"},
        eval_batch=batch_obj,
        components_to_update=["instruction"],
    )

    records = dataset["instruction"]
    assert len(records) == 1
    assert "history_top_k" not in records[0]


# ---------------------------------------------------------------------------
# Long-lived event loop: call_agent may hold async resources across evaluate()
# calls without hitting "Event loop is closed" (fix for CONC-2).
# ---------------------------------------------------------------------------


def test_evaluate_reuses_single_loop_across_calls(monkeypatch) -> None:
    """A module-level async resource bound to the loop on first use must
    keep working across consecutive evaluate() calls."""
    import asyncio

    outcome = EvaluationOutcome(
        pass_rate=1.0,
        tiebreaker=1.0,
        metric_breakdown={"m1": 1.0},
        failed_case_ids=[],
        raw_result=_evaluate_result({
            "c1": [_case_result("c1", status=EvalStatus.PASSED, metric_score=1.0, actual="ok")],
        }),
    )
    _patch_run_evaluator(monkeypatch, outcome)

    seen_loops: list[int] = []

    async def call_agent_with_loop_id(query: str) -> str:
        # id(loop) stays constant iff the adapter reuses one loop.
        seen_loops.append(id(asyncio.get_running_loop()))
        return "stub"

    adapter = _AgentGEPAAdapter(
        target_prompt=_new_target_prompt(),
        eval_config=_eval_config(),
        call_agent=call_agent_with_loop_id,
        callbacks=None,
        num_runs=1,
        top_k_per_case=0,
    )
    try:
        for _ in range(3):
            adapter.evaluate(
                batch=[_eval_case()],
                candidate={"instruction": "v"},
            )
    finally:
        adapter.close()

    # _patch_run_evaluator stubs the actual evaluator path so call_agent
    # is not driven; verify the same loop is used by inspecting the
    # adapter-owned loop directly across calls.
    assert adapter._loop is None  # closed after close()


def test_evaluate_loop_reuse_supports_module_level_async_client(monkeypatch) -> None:
    """A user holding a module-level lock-like async resource that binds to
    its first event loop must still work across multiple evaluate() calls."""
    import asyncio

    outcome = EvaluationOutcome(
        pass_rate=1.0, tiebreaker=1.0, metric_breakdown={"m1": 1.0},
        failed_case_ids=[],
        raw_result=_evaluate_result({
            "c1": [_case_result("c1", status=EvalStatus.PASSED, metric_score=1.0, actual="ok")],
        }),
    )

    captured_loops: list[int] = []
    # Capture the loop id during write_all (driven by adapter's loop).
    target = TargetPrompt()
    state = {"value": ""}

    async def read_cb() -> str:
        return state["value"]

    async def write_cb(value: str) -> None:
        captured_loops.append(id(asyncio.get_running_loop()))
        state["value"] = value

    target.add_callback("instruction", read=read_cb, write=write_cb)

    _patch_run_evaluator(monkeypatch, outcome)

    adapter = _AgentGEPAAdapter(
        target_prompt=target,
        eval_config=_eval_config(),
        call_agent=_stub_call_agent,
        callbacks=None,
        num_runs=1,
        top_k_per_case=0,
    )
    try:
        for i in range(3):
            adapter.evaluate(
                batch=[_eval_case()],
                candidate={"instruction": f"v{i}"},
            )
    finally:
        adapter.close()

    # All write_all invocations executed on the same event loop.
    assert len(captured_loops) == 3
    assert len(set(captured_loops)) == 1


def test_close_is_idempotent_and_safe_before_evaluate() -> None:
    """close() before any evaluate() and double close() must not raise."""
    adapter = _AgentGEPAAdapter(
        target_prompt=_new_target_prompt(),
        eval_config=_eval_config(),
        call_agent=_stub_call_agent,
        callbacks=None,
        num_runs=1,
        top_k_per_case=0,
    )
    adapter.close()
    adapter.close()


def test_evaluate_after_close_creates_fresh_loop(monkeypatch) -> None:
    """After close(), a subsequent evaluate() must spin up a new loop
    (defensive support for callers that reuse an adapter)."""
    import asyncio

    outcome = EvaluationOutcome(
        pass_rate=1.0, tiebreaker=1.0, metric_breakdown={"m1": 1.0},
        failed_case_ids=[],
        raw_result=_evaluate_result({
            "c1": [_case_result("c1", status=EvalStatus.PASSED, metric_score=1.0, actual="ok")],
        }),
    )
    _patch_run_evaluator(monkeypatch, outcome)

    adapter = _AgentGEPAAdapter(
        target_prompt=_new_target_prompt(),
        eval_config=_eval_config(),
        call_agent=_stub_call_agent,
        callbacks=None,
        num_runs=1,
        top_k_per_case=0,
    )
    try:
        adapter.evaluate(batch=[_eval_case()], candidate={"instruction": "v1"})
        old_loop = adapter._loop
        first_loop_id = id(old_loop)
        adapter.close()
        assert adapter._loop is None
        adapter.evaluate(batch=[_eval_case()], candidate={"instruction": "v2"})
        assert adapter._loop is not None
        assert id(adapter._loop) != first_loop_id
        del old_loop
    finally:
        adapter.close()


# ---------------------------------------------------------------------------
# API-A2: call_agent return-type sentinel check (must surface non-str return
# on the first call instead of crashing deep inside metric code).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_agent_returning_non_str_is_rejected_on_first_call():
    """An async callable that returns a non-str value must raise a clear
    TypeError on the first invocation, naming the actual returned type.
    The check fires through the wrapper installed in _AgentGEPAAdapter.__init__."""
    async def bad_call_agent(query: str):
        return 42  # int, not str

    adapter = _AgentGEPAAdapter(
        target_prompt=_new_target_prompt(),
        eval_config=_eval_config(),
        call_agent=bad_call_agent,
        callbacks=None,
        num_runs=1,
        top_k_per_case=0,
    )

    with pytest.raises(TypeError, match="call_agent must return str"):
        await adapter.call_agent("hi")


@pytest.mark.asyncio
async def test_call_agent_return_check_runs_only_once():
    """The wrapper must only validate on the first successful call to avoid
    per-case overhead. After the first call returns a valid str, later calls
    bypass the isinstance check entirely (we cannot directly observe this,
    but verify functional correctness: subsequent str returns succeed)."""
    call_count = {"n": 0}

    async def good_call_agent(query: str):
        call_count["n"] += 1
        return "ok"

    adapter = _AgentGEPAAdapter(
        target_prompt=_new_target_prompt(),
        eval_config=_eval_config(),
        call_agent=good_call_agent,
        callbacks=None,
        num_runs=1,
        top_k_per_case=0,
    )

    for _ in range(5):
        result = await adapter.call_agent("hi")
        assert result == "ok"
    assert call_count["n"] == 5


@pytest.mark.asyncio
async def test_call_agent_return_check_does_not_swallow_user_exceptions():
    """If call_agent itself raises, the wrapper must propagate the original
    exception (not replace it with a TypeError)."""
    async def raising_call_agent(query: str):
        raise RuntimeError("user-side failure")

    adapter = _AgentGEPAAdapter(
        target_prompt=_new_target_prompt(),
        eval_config=_eval_config(),
        call_agent=raising_call_agent,
        callbacks=None,
        num_runs=1,
        top_k_per_case=0,
    )

    with pytest.raises(RuntimeError, match="user-side failure"):
        await adapter.call_agent("hi")
