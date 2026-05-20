# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for GepaReflectiveOptimizer and its GEPAResult->OptimizeResult helpers."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from typing import Optional

import pytest

from trpc_agent_sdk.evaluation._eval_case import EvalCase
from trpc_agent_sdk.evaluation._eval_case import Invocation
from trpc_agent_sdk.evaluation._eval_config import EvalConfig
from trpc_agent_sdk.evaluation._eval_set import EvalSet
from trpc_agent_sdk.evaluation._optimize_config import FrameworkStopConfig
from trpc_agent_sdk.evaluation._optimize_config import GepaReflectiveAlgo
from trpc_agent_sdk.evaluation._optimize_config import OptimizeConfig
from trpc_agent_sdk.evaluation._optimize_config import OptimizeConfigFile
from trpc_agent_sdk.evaluation._optimize_gepa_adapter import _AgentGEPAAdapter
from trpc_agent_sdk.evaluation._optimize_gepa_reflective import GepaReflectiveOptimizer
from trpc_agent_sdk.evaluation._optimize_gepa_reflective import (
    _RequiredMetricsAboveThresholdStopper,
)
from trpc_agent_sdk.evaluation._optimize_gepa_reflective import _LabeledStopper
from trpc_agent_sdk.evaluation._optimize_gepa_reflective import _build_failed_result
from trpc_agent_sdk.evaluation._optimize_gepa_reflective import _build_optimize_result
from trpc_agent_sdk.evaluation._optimize_gepa_reflective import _build_stop_callbacks
from trpc_agent_sdk.evaluation._optimize_gepa_reflective import _classify_stop_reason
from trpc_agent_sdk.evaluation._optimize_gepa_reflective import _load_evalset_cases
from trpc_agent_sdk.evaluation._optimize_model_options import OptimizeModelOptions
from trpc_agent_sdk.evaluation._target_prompt import TargetPrompt
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


# ---------------------------------------------------------------------------
# Shared fixtures and helpers
# ---------------------------------------------------------------------------


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


async def _stub_call_agent(query: str) -> str:
    return "stub"


def _new_target_prompt(write_recorder: Optional[dict[str, str]] = None) -> TargetPrompt:
    target = TargetPrompt()
    recorder = write_recorder if write_recorder is not None else {}

    async def read_cb() -> str:
        return recorder.get("instruction", "initial")

    async def write_cb(value: str) -> None:
        recorder["instruction"] = value

    target.add_callback("instruction", read=read_cb, write=write_cb)
    return target


class _FakeGEPAResult:
    """Minimal stand-in for gepa.core.result.GEPAResult used by mapping tests."""

    def __init__(
        self,
        *,
        candidates,
        val_aggregate_scores,
        parents=None,
        discovery_eval_counts=None,
        total_metric_calls=None,
        best_outputs_valset=None,
        per_objective_best_candidates=None,
    ):
        self.candidates = candidates
        self.val_aggregate_scores = val_aggregate_scores
        self.parents = parents or [[None]] + [[i - 1] for i in range(1, len(candidates))]
        self.discovery_eval_counts = discovery_eval_counts or [0] * len(candidates)
        self.total_metric_calls = total_metric_calls
        self.best_outputs_valset = best_outputs_valset
        # GEPA's actual GEPAResult field is dict[str, set[int]] | None
        self.per_objective_best_candidates = per_objective_best_candidates

    @property
    def best_idx(self) -> int:
        return max(range(len(self.val_aggregate_scores)), key=lambda i: self.val_aggregate_scores[i])

    @property
    def best_candidate(self):
        return self.candidates[self.best_idx]


def _make_config(*, max_metric_calls: int = 30, **algo_overrides) -> OptimizeConfigFile:
    return OptimizeConfigFile(
        evaluate=EvalConfig(
            metrics=[{"metric_name": "m1", "threshold": 0.7}],
            num_runs=1,
        ),
        optimize=OptimizeConfig(
            algorithm=GepaReflectiveAlgo(
                name="gepa_reflective",
                reflection_lm=OptimizeModelOptions(
                    provider_name="openai",
                    model_name="gpt-4o",
                    api_key="test-key",
                ),
                max_metric_calls=max_metric_calls,
                **algo_overrides,
            ),
        ),
    )


def _make_optimizer(target=None, train_path="/tmp/train.json", val_path="/tmp/val.json"):
    target = target or _new_target_prompt()
    return GepaReflectiveOptimizer(
        config=_make_config(),
        call_agent=_stub_call_agent,
        target_prompt=target,
        train_dataset_path=train_path,
        validation_dataset_path=val_path,
    )


# ---------------------------------------------------------------------------
# _load_evalset_cases
# ---------------------------------------------------------------------------


def test_load_evalset_cases_reads_from_evalset_json(tmp_path):
    evalset = EvalSet(
        eval_set_id="train",
        eval_cases=[_eval_case("c1"), _eval_case("c2")],
    )
    file_path = tmp_path / "train.evalset.json"
    file_path.write_text(evalset.model_dump_json(), encoding="utf-8")

    cases = _load_evalset_cases(str(file_path))
    assert len(cases) == 2
    assert {c.eval_id for c in cases} == {"c1", "c2"}


def test_load_evalset_cases_raises_for_missing_file():
    with pytest.raises(FileNotFoundError):
        _load_evalset_cases("/nonexistent/path.json")


# ---------------------------------------------------------------------------
# _build_stop_callbacks
# ---------------------------------------------------------------------------


def _disabled_stop_cfg() -> FrameworkStopConfig:
    return FrameworkStopConfig(required_metrics=None)


def test_build_stop_callbacks_includes_each_configured_stopper():
    """One stopper instance per configured stop field; unset fields stay off.

    Every gepa stopper is wrapped by ``_LabeledStopper`` so the optimizer can
    classify ``stop_reason`` after gepa returns; the inner gepa class is
    reached via ``stopper._inner`` and the label is exposed via
    ``stopper.label``.
    """
    pytest.importorskip("gepa")
    algo = GepaReflectiveAlgo(
        name="gepa_reflective",
        reflection_lm=OptimizeModelOptions(model_name="m", api_key="k"),
        max_metric_calls=10,
        max_iterations_without_improvement=3,
        timeout_seconds=60.0,
        score_threshold=0.95,
        max_candidate_proposals=20,
        max_tracked_candidates=12,
    )
    stoppers, framework_stopper = _build_stop_callbacks(
        algo, _disabled_stop_cfg(), {}
    )
    assert framework_stopper is None
    labeled_stoppers = [s for s in stoppers if isinstance(s, _LabeledStopper)]
    inner_class_names = {type(s._inner).__name__ for s in labeled_stoppers}
    assert "MaxMetricCallsStopper" in inner_class_names
    assert "NoImprovementStopper" in inner_class_names
    assert "TimeoutStopCondition" in inner_class_names
    assert "ScoreThresholdStopper" in inner_class_names
    assert "MaxCandidateProposalsStopper" in inner_class_names
    assert "MaxTrackedCandidatesStopper" in inner_class_names


def test_build_stop_callbacks_emits_only_configured_stoppers():
    pytest.importorskip("gepa")
    algo = GepaReflectiveAlgo(
        name="gepa_reflective",
        reflection_lm=OptimizeModelOptions(model_name="m", api_key="k"),
        timeout_seconds=30.0,
    )
    stoppers, framework_stopper = _build_stop_callbacks(
        algo, _disabled_stop_cfg(), {}
    )
    assert framework_stopper is None
    assert len(stoppers) == 1
    assert isinstance(stoppers[0], _LabeledStopper)
    assert type(stoppers[0]._inner).__name__ == "TimeoutStopCondition"
    assert stoppers[0].label == "timeout"


def test_build_stop_callbacks_adds_required_metrics_stopper_for_all():
    pytest.importorskip("gepa")
    algo = GepaReflectiveAlgo(
        name="gepa_reflective",
        reflection_lm=OptimizeModelOptions(model_name="m", api_key="k"),
        max_metric_calls=10,
    )
    stoppers, framework_stopper = _build_stop_callbacks(
        algo,
        FrameworkStopConfig(required_metrics="all"),
        {"m1": 0.5, "m2": 0.3},
    )
    assert isinstance(framework_stopper, _RequiredMetricsAboveThresholdStopper)
    assert framework_stopper in stoppers
    assert framework_stopper._thresholds == {"m1": 0.5, "m2": 0.3}


def test_build_stop_callbacks_adds_required_metrics_stopper_for_subset_list():
    pytest.importorskip("gepa")
    algo = GepaReflectiveAlgo(
        name="gepa_reflective",
        reflection_lm=OptimizeModelOptions(model_name="m", api_key="k"),
        max_metric_calls=10,
    )
    stoppers, framework_stopper = _build_stop_callbacks(
        algo,
        FrameworkStopConfig(required_metrics=["m1"]),
        {"m1": 0.5, "m2": 0.3},
    )
    assert isinstance(framework_stopper, _RequiredMetricsAboveThresholdStopper)
    assert framework_stopper._thresholds == {"m1": 0.5}


def test_build_stop_callbacks_skips_framework_stopper_when_disabled():
    pytest.importorskip("gepa")
    algo = GepaReflectiveAlgo(
        name="gepa_reflective",
        reflection_lm=OptimizeModelOptions(model_name="m", api_key="k"),
        max_metric_calls=10,
    )
    stoppers, framework_stopper = _build_stop_callbacks(
        algo,
        FrameworkStopConfig(required_metrics=None),
        {"m1": 0.5},
    )
    assert framework_stopper is None
    assert all(
        not isinstance(s, _RequiredMetricsAboveThresholdStopper) for s in stoppers
    )


def test_build_stop_callbacks_skips_framework_stopper_when_thresholds_empty():
    """Even with required_metrics='all', if metric_thresholds is empty the
    resolved subset is empty and the stopper would be a no-op; skip it."""
    pytest.importorskip("gepa")
    algo = GepaReflectiveAlgo(
        name="gepa_reflective",
        reflection_lm=OptimizeModelOptions(model_name="m", api_key="k"),
        max_metric_calls=10,
    )
    stoppers, framework_stopper = _build_stop_callbacks(
        algo, FrameworkStopConfig(required_metrics="all"), {}
    )
    assert framework_stopper is None


# ---------------------------------------------------------------------------
# _RequiredMetricsAboveThresholdStopper
# ---------------------------------------------------------------------------


def test_required_metrics_stopper_returns_false_before_first_update():
    stopper = _RequiredMetricsAboveThresholdStopper({"m1": 0.5})
    assert stopper(gepa_state=None) is False
    assert stopper.last_triggered is False


def test_required_metrics_stopper_triggers_when_all_pass():
    stopper = _RequiredMetricsAboveThresholdStopper({"m1": 0.5, "m2": 0.3})
    stopper.update({"m1": 0.6, "m2": 0.4})
    assert stopper(gepa_state=None) is True
    assert stopper.last_triggered is True


def test_required_metrics_stopper_does_not_trigger_when_one_below():
    stopper = _RequiredMetricsAboveThresholdStopper({"m1": 0.5, "m2": 0.3})
    stopper.update({"m1": 0.6, "m2": 0.2})
    assert stopper(gepa_state=None) is False
    assert stopper.last_triggered is False


def test_required_metrics_stopper_last_triggered_is_sticky():
    """Once triggered, last_triggered remains True even if subsequent updates
    fall back below thresholds (helps the run() stop_reason decision)."""
    stopper = _RequiredMetricsAboveThresholdStopper({"m1": 0.5})
    stopper.update({"m1": 0.7})
    stopper(gepa_state=None)
    assert stopper.last_triggered is True
    stopper.update({"m1": 0.1})
    stopper(gepa_state=None)
    assert stopper.last_triggered is True


def test_required_metrics_stopper_empty_thresholds_never_triggers():
    stopper = _RequiredMetricsAboveThresholdStopper({})
    stopper.update({"m1": 0.9})
    assert stopper(gepa_state=None) is False


# ---------------------------------------------------------------------------
# _build_optimize_result
# ---------------------------------------------------------------------------


def test_build_optimize_result_maps_best_and_baseline():
    baseline = {"instruction": "baseline text"}
    candidates = [
        {"instruction": "baseline text"},
        {"instruction": "candidate v1"},
        {"instruction": "candidate v2 (best)"},
    ]
    gepa_result = _FakeGEPAResult(
        candidates=candidates,
        val_aggregate_scores=[0.5, 0.6, 0.9],
        total_metric_calls=42,
    )

    started = datetime(2026, 5, 15, 10, 0, 0, tzinfo=timezone.utc)
    finished = datetime(2026, 5, 15, 10, 5, 0, tzinfo=timezone.utc)

    result = _build_optimize_result(
        gepa_result=gepa_result,
        baseline_prompts=baseline,
        best_candidate=candidates[2],
        reflection_lm_cost=1.23,
        started_at=started,
        finished_at=finished,
        algo_name="gepa_reflective",
    )

    assert result.status == "SUCCEEDED"
    assert result.finish_reason == "completed"
    assert result.baseline_pass_rate == pytest.approx(0.5)
    assert result.best_pass_rate == pytest.approx(0.9)
    assert result.pass_rate_improvement == pytest.approx(0.4)
    assert result.baseline_prompts == baseline
    assert result.best_prompts == candidates[2]
    assert result.total_rounds == 2
    assert result.total_llm_cost == pytest.approx(1.23)
    assert result.algorithm == "gepa_reflective"
    assert result.extras["total_metric_calls"] == 42


def test_build_optimize_result_produces_round_records():
    baseline = {"instruction": "v0"}
    candidates = [
        {"instruction": "v0"},
        {"instruction": "v1"},
        {"instruction": "v2"},
    ]
    gepa_result = _FakeGEPAResult(
        candidates=candidates,
        val_aggregate_scores=[0.3, 0.7, 0.5],
    )

    result = _build_optimize_result(
        gepa_result=gepa_result,
        baseline_prompts=baseline,
        best_candidate=candidates[1],
        reflection_lm_cost=0.0,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        algo_name="gepa_reflective",
    )

    assert len(result.rounds) == 2
    round1 = result.rounds[0]
    assert round1.round == 1
    assert round1.candidate_prompts == candidates[1]
    assert round1.validation_pass_rate == pytest.approx(0.7)
    assert round1.accepted is True

    round2 = result.rounds[1]
    assert round2.round == 2
    assert round2.candidate_prompts == candidates[2]
    assert round2.accepted is False


def test_build_optimize_result_forwards_metric_thresholds():
    """metric_thresholds gets copied through to OptimizeResult so reporters and
    summary.txt can show baseline / best alongside the per-metric PASS bar."""
    baseline = {"instruction": "v0"}
    gepa_result = _FakeGEPAResult(
        candidates=[baseline, {"instruction": "v1"}],
        val_aggregate_scores=[0.4, 0.9],
    )
    result = _build_optimize_result(
        gepa_result=gepa_result,
        baseline_prompts=baseline,
        best_candidate={"instruction": "v1"},
        reflection_lm_cost=0.0,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        algo_name="gepa_reflective",
        metric_thresholds={
            "final_response_avg_score": 0.5,
            "response_match_score": 0.3,
        },
    )
    assert result.metric_thresholds == {
        "final_response_avg_score": 0.5,
        "response_match_score": 0.3,
    }


def test_build_failed_result_carries_metric_thresholds():
    """Even on FAILED runs the user should still see the configured thresholds
    so summary.txt does not look like the metrics had no acceptance bar at all.
    """
    result = _build_failed_result(
        baseline_prompts={"instruction": "v0"},
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        error_message="boom",
        algo_name="gepa_reflective",
        metric_thresholds={"final_response_avg_score": 0.5},
    )
    assert result.status == "FAILED"
    assert result.metric_thresholds == {"final_response_avg_score": 0.5}


def test_build_optimize_result_forwards_baseline_and_best_breakdowns():
    """B1: baseline_metric_breakdown is passed through; best_metric_breakdown is
    pulled from the round whose candidate_prompts matches best_candidate."""
    from trpc_agent_sdk.evaluation._optimize_result import RoundRecord

    baseline = {"instruction": "v0"}
    candidates = [baseline, {"instruction": "v1"}, {"instruction": "v2"}]
    gepa_result = _FakeGEPAResult(
        candidates=candidates,
        val_aggregate_scores=[0.4, 0.6, 0.9],
    )
    callback_rounds = [
        RoundRecord(
            round=1,
            optimized_field_names=["instruction"],
            candidate_prompts=candidates[1],
            train_pass_rate=0.0,
            validation_pass_rate=0.6,
            metric_breakdown={"final_response_avg_score": 0.6},
            accepted=False,
            acceptance_reason="explored",
            started_at="2026-05-17T10:00:00Z",
            duration_seconds=1.0,
        ),
        RoundRecord(
            round=2,
            optimized_field_names=["instruction"],
            candidate_prompts=candidates[2],
            train_pass_rate=0.0,
            validation_pass_rate=0.9,
            metric_breakdown={"final_response_avg_score": 0.9},
            accepted=True,
            acceptance_reason="best",
            started_at="2026-05-17T10:00:02Z",
            duration_seconds=1.0,
        ),
    ]
    result = _build_optimize_result(
        gepa_result=gepa_result,
        baseline_prompts=baseline,
        best_candidate=candidates[2],
        reflection_lm_cost=0.5,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        algo_name="gepa_reflective",
        callback_rounds=callback_rounds,
        baseline_metric_breakdown={"final_response_avg_score": 0.4},
        total_reflection_lm_calls=5,
        total_judge_model_calls=12,
        total_judge_cost=0.25,
        total_token_usage={"prompt": 100, "completion": 50, "total": 150},
    )

    assert result.baseline_metric_breakdown == {"final_response_avg_score": 0.4}
    assert result.best_metric_breakdown == {"final_response_avg_score": 0.9}
    assert result.total_reflection_lm_calls == 5
    assert result.total_judge_model_calls == 12
    assert result.total_llm_cost == pytest.approx(0.75)  # 0.5 (reflection) + 0.25 (judge)
    assert result.total_token_usage == {"prompt": 100, "completion": 50, "total": 150}


def test_build_optimize_result_forwards_stop_reason():
    baseline = {"instruction": "v0"}
    gepa_result = _FakeGEPAResult(
        candidates=[baseline, {"instruction": "v1"}],
        val_aggregate_scores=[0.4, 0.9],
    )
    result = _build_optimize_result(
        gepa_result=gepa_result,
        baseline_prompts=baseline,
        best_candidate={"instruction": "v1"},
        reflection_lm_cost=0.0,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        algo_name="gepa_reflective",
        stop_reason="required_metrics_passing",
    )
    assert result.stop_reason == "required_metrics_passing"


def test_build_optimize_result_stop_reason_defaults_to_none():
    baseline = {"instruction": "v0"}
    gepa_result = _FakeGEPAResult(
        candidates=[baseline, {"instruction": "v1"}],
        val_aggregate_scores=[0.4, 0.9],
    )
    result = _build_optimize_result(
        gepa_result=gepa_result,
        baseline_prompts=baseline,
        best_candidate={"instruction": "v1"},
        reflection_lm_cost=0.0,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        algo_name="gepa_reflective",
    )
    assert result.stop_reason is None


def test_build_optimize_result_pass_rate_improvement_can_be_zero():
    baseline = {"instruction": "v"}
    gepa_result = _FakeGEPAResult(
        candidates=[baseline, dict(baseline)],
        val_aggregate_scores=[0.8, 0.8],
    )
    result = _build_optimize_result(
        gepa_result=gepa_result,
        baseline_prompts=baseline,
        best_candidate=baseline,
        reflection_lm_cost=0.0,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        algo_name="gepa_reflective",
    )
    assert result.pass_rate_improvement == pytest.approx(0.0)


def test_build_optimize_result_mirrors_baseline_breakdown_when_baseline_is_best():
    """R2: when ``best_idx == 0`` (gepa found no improvement), the
    iteration-0 baseline evaluation is recorded as
    ``baseline_metric_breakdown`` rather than as a RoundRecord, so the
    rounds list never contains a record matching the seed prompts.
    Without the fallback, ``best_metric_breakdown`` would stay empty and
    ``summary.txt`` would render the ``best`` column as ``nan``, looking
    like data loss instead of "no improvement".
    """
    baseline = {"instruction": "v0"}
    gepa_result = _FakeGEPAResult(
        candidates=[baseline],  # only the seed candidate
        val_aggregate_scores=[0.6667],
    )
    result = _build_optimize_result(
        gepa_result=gepa_result,
        baseline_prompts=baseline,
        best_candidate=baseline,  # baseline IS the best
        reflection_lm_cost=0.0,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        algo_name="gepa_reflective",
        baseline_metric_breakdown={
            "final_response_avg_score": 0.6667,
            "tool_trajectory_avg_score": 0.5,
        },
    )

    assert result.best_metric_breakdown == {
        "final_response_avg_score": 0.6667,
        "tool_trajectory_avg_score": 0.5,
    }
    # And it should match the baseline breakdown 1:1.
    assert result.best_metric_breakdown == result.baseline_metric_breakdown


def test_build_optimize_result_does_not_mirror_when_a_round_already_matches():
    """The mirror-from-baseline fallback must NOT overwrite a real round
    breakdown — if a RoundRecord matches ``best_candidate`` (e.g. the
    candidate happens to equal baseline as a string but a round still
    re-evaluated it on the valset), prefer the round's actual
    metric_breakdown.
    """
    baseline = {"instruction": "v0"}
    # callback_rounds carries a record matching baseline with REAL data.
    from trpc_agent_sdk.evaluation._optimize_result import RoundRecord

    callback_rounds = [
        RoundRecord(
            round=1,
            optimized_field_names=["instruction"],
            candidate_prompts=baseline,
            train_pass_rate=0.0,
            validation_pass_rate=0.6667,
            metric_breakdown={"final_response_avg_score": 0.7},
            accepted=False,
            acceptance_reason="explored",
            started_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=0.1,
        ),
    ]
    gepa_result = _FakeGEPAResult(
        candidates=[baseline, baseline],
        val_aggregate_scores=[0.6667, 0.6667],
    )
    result = _build_optimize_result(
        gepa_result=gepa_result,
        baseline_prompts=baseline,
        best_candidate=baseline,
        reflection_lm_cost=0.0,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        algo_name="gepa_reflective",
        callback_rounds=callback_rounds,
        baseline_metric_breakdown={"final_response_avg_score": 0.0},  # different!
    )

    # Round's real data wins; baseline_metric_breakdown is NOT used.
    assert result.best_metric_breakdown == {"final_response_avg_score": 0.7}


def test_build_optimize_result_no_mirror_when_baseline_breakdown_empty():
    """When both ``baseline_metric_breakdown`` and any matching round
    record are empty, ``best_metric_breakdown`` stays empty — there is
    simply no data to mirror.
    """
    baseline = {"instruction": "v0"}
    gepa_result = _FakeGEPAResult(
        candidates=[baseline],
        val_aggregate_scores=[0.0],
    )
    result = _build_optimize_result(
        gepa_result=gepa_result,
        baseline_prompts=baseline,
        best_candidate=baseline,
        reflection_lm_cost=0.0,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        algo_name="gepa_reflective",
        # baseline_metric_breakdown is omitted (None → empty dict)
    )

    assert result.best_metric_breakdown == {}


# ---------------------------------------------------------------------------
# _build_failed_result
# ---------------------------------------------------------------------------


def test_build_failed_result_marks_status_failed():
    baseline = {"instruction": "v0"}
    started = datetime(2026, 5, 15, 10, 0, 0, tzinfo=timezone.utc)
    finished = datetime(2026, 5, 15, 10, 0, 1, tzinfo=timezone.utc)

    result = _build_failed_result(
        baseline_prompts=baseline,
        started_at=started,
        finished_at=finished,
        error_message="boom",
        algo_name="gepa_reflective",
    )

    assert result.status == "FAILED"
    assert result.finish_reason == "error"
    assert result.error_message == "boom"
    assert result.baseline_prompts == baseline
    assert result.best_prompts == baseline
    assert result.baseline_pass_rate == 0.0
    assert result.best_pass_rate == 0.0
    assert result.total_rounds == 0
    assert result.algorithm == "gepa_reflective"


# ---------------------------------------------------------------------------
# GepaReflectiveOptimizer construction and run
# ---------------------------------------------------------------------------


def test_optimizer_constructor_stores_dataset_paths():
    optimizer = _make_optimizer(train_path="/tmp/t.json", val_path="/tmp/v.json")
    assert optimizer.train_dataset_path == "/tmp/t.json"
    assert optimizer.validation_dataset_path == "/tmp/v.json"


@pytest.mark.asyncio
async def test_optimizer_run_returns_best_without_writing_back(tmp_path, monkeypatch):
    train_evalset = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val_evalset = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train_evalset.model_dump_json(), encoding="utf-8")
    val_path.write_text(val_evalset.model_dump_json(), encoding="utf-8")

    recorder: dict[str, str] = {}
    target = _new_target_prompt(recorder)
    optimizer = GepaReflectiveOptimizer(
        config=_make_config(),
        call_agent=_stub_call_agent,
        target_prompt=target,
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
    )

    fake_gepa_result = _FakeGEPAResult(
        candidates=[{"instruction": "initial"}, {"instruction": "improved"}],
        val_aggregate_scores=[0.5, 0.9],
        total_metric_calls=20,
    )

    captured: dict = {}

    async def fake_call_gepa(self, **kwargs):
        captured["kwargs"] = kwargs
        return fake_gepa_result

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", fake_call_gepa)

    result = await optimizer.run()

    assert result.status == "SUCCEEDED"
    assert result.best_pass_rate == pytest.approx(0.9)
    assert result.best_prompts == {"instruction": "improved"}
    # BaseOptimizer.run() must not write back; the AgentOptimizer facade is the
    # sole owner of the write-back path (gated by ``update_source``).
    # The recorder may stay empty here because gepa.optimize is mocked and never
    # actually invokes adapter.evaluate(...); what matters is that ``result``
    # exposes the best prompts without persisting them.
    assert recorder.get("instruction") != "improved"

    kwargs = captured["kwargs"]
    assert kwargs["seed_candidate"] == {"instruction": "initial"}
    assert len(kwargs["trainset"]) == 1
    assert len(kwargs["valset"]) == 1
    assert kwargs["reflection_lm"] is not None
    assert isinstance(kwargs["adapter"], _AgentGEPAAdapter)
    assert kwargs["candidate_selection_strategy"] == "pareto"
    assert kwargs["module_selector"] == "round_robin"
    assert kwargs["seed"] == 42
    # The reflection prompt template must reach gepa.optimize and keep both
    # placeholders so GEPA's InstructionProposalSignature validation passes.
    template = kwargs.get("reflection_prompt_template", "")
    assert "<curr_param>" in template
    assert "<side_info>" in template


@pytest.mark.asyncio
async def test_optimizer_run_injects_metric_reference_doc_into_reflection_template(
    tmp_path, monkeypatch
):
    """For built-in criterion-based metrics, the metric reference doc must
    travel into gepa.optimize's reflection_prompt_template so the reflection
    LM understands what every per-case feedback row means."""
    train_evalset = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val_evalset = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train_evalset.model_dump_json(), encoding="utf-8")
    val_path.write_text(val_evalset.model_dump_json(), encoding="utf-8")

    # Use a real criterion-based built-in metric so the doc renders actual
    # content (not the empty-doc fallback path covered by the previous test).
    config = OptimizeConfigFile(
        evaluate=EvalConfig(
            metrics=[{
                "metric_name": "final_response_avg_score",
                "threshold": 1.0,
                "criterion": {"final_response": {"text": {"match": "contains"}}},
            }],
            num_runs=1,
        ),
        optimize=OptimizeConfig(
            algorithm=GepaReflectiveAlgo(
                name="gepa_reflective",
                reflection_lm=OptimizeModelOptions(
                    provider_name="openai",
                    model_name="gpt-4o",
                    api_key="test-key",
                ),
                max_metric_calls=30,
            ),
        ),
    )
    optimizer = GepaReflectiveOptimizer(
        config=config,
        call_agent=_stub_call_agent,
        target_prompt=_new_target_prompt(),
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
    )

    captured: dict = {}

    async def fake_call_gepa(self, **kwargs):
        captured["kwargs"] = kwargs
        return _FakeGEPAResult(
            candidates=[{"instruction": "initial"}],
            val_aggregate_scores=[1.0],
            total_metric_calls=10,
        )

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", fake_call_gepa)

    await optimizer.run()

    template = captured["kwargs"]["reflection_prompt_template"]
    # Required GEPA placeholders preserved
    assert "<curr_param>" in template
    assert "<side_info>" in template
    # The injected metric doc surfaces its metric name and config knobs
    assert "final_response_avg_score" in template
    assert "contains" in template
    # The metric doc sits between <curr_param> and <side_info>
    assert template.index("<curr_param>") < template.index("final_response_avg_score")
    assert template.index("final_response_avg_score") < template.index("<side_info>")


@pytest.mark.asyncio
async def test_optimizer_run_surfaces_per_metric_best_candidates(tmp_path, monkeypatch):
    """When GEPA reports per_objective_best_candidates, OptimizeResult must
    forward it (converting set -> sorted list) so users can see which
    candidate excels on which metric independent of the aggregated best."""
    train_evalset = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val_evalset = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train_evalset.model_dump_json(), encoding="utf-8")
    val_path.write_text(val_evalset.model_dump_json(), encoding="utf-8")

    optimizer = GepaReflectiveOptimizer(
        config=_make_config(),
        call_agent=_stub_call_agent,
        target_prompt=_new_target_prompt(),
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
    )

    fake_gepa_result = _FakeGEPAResult(
        candidates=[{"instruction": "initial"}, {"instruction": "improved"}],
        val_aggregate_scores=[0.4, 0.9],
        total_metric_calls=20,
        per_objective_best_candidates={
            "final_response_avg_score": {1},
            "llm_rubric_response": {0, 1},
        },
    )

    async def fake_call_gepa(self, **kwargs):
        return fake_gepa_result

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", fake_call_gepa)

    result = await optimizer.run()

    assert result.per_metric_best_candidates == {
        "final_response_avg_score": [1],
        "llm_rubric_response": [0, 1],
    }


@pytest.mark.asyncio
async def test_optimizer_run_per_metric_best_candidates_empty_when_gepa_omits_it(
    tmp_path, monkeypatch
):
    """Older GEPA builds or algorithms without per-objective tracking return
    ``per_objective_best_candidates=None``; OptimizeResult must keep an empty
    dict (not raise) so consumers can rely on the field always being a dict."""
    train_evalset = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val_evalset = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train_evalset.model_dump_json(), encoding="utf-8")
    val_path.write_text(val_evalset.model_dump_json(), encoding="utf-8")

    optimizer = GepaReflectiveOptimizer(
        config=_make_config(),
        call_agent=_stub_call_agent,
        target_prompt=_new_target_prompt(),
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
    )

    fake_gepa_result = _FakeGEPAResult(
        candidates=[{"instruction": "x"}],
        val_aggregate_scores=[0.5],
        total_metric_calls=5,
        per_objective_best_candidates=None,
    )

    async def fake_call_gepa(self, **kwargs):
        return fake_gepa_result

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", fake_call_gepa)

    result = await optimizer.run()
    assert result.per_metric_best_candidates == {}


@pytest.mark.asyncio
async def test_optimizer_run_returns_failed_when_baseline_evaluation_raises(tmp_path, monkeypatch):
    """If the explicit baseline evaluation throws, surface a FAILED result with
    the captured error message instead of propagating a raw exception."""
    train_evalset = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val_evalset = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train_evalset.model_dump_json(), encoding="utf-8")
    val_path.write_text(val_evalset.model_dump_json(), encoding="utf-8")

    recorder: dict[str, str] = {}
    target = _new_target_prompt(recorder)
    optimizer = GepaReflectiveOptimizer(
        config=_make_config(),
        call_agent=_stub_call_agent,
        target_prompt=target,
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
    )

    def explode(self, *args, **kwargs):
        raise RuntimeError("evaluator exploded during baseline")

    monkeypatch.setattr(_AgentGEPAAdapter, "evaluate", explode)

    result = await optimizer.run()
    assert result.status == "FAILED"
    assert result.finish_reason == "error"
    assert "evaluator exploded during baseline" in result.error_message
    assert result.best_prompts == result.baseline_prompts


@pytest.mark.asyncio
async def test_optimizer_run_stop_reason_required_metrics_passing(
    tmp_path, monkeypatch
):
    """When the framework stopper fires (its last_triggered flips True before
    gepa returns), run() must persist stop_reason='required_metrics_passing'."""
    train_evalset = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val_evalset = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train_evalset.model_dump_json(), encoding="utf-8")
    val_path.write_text(val_evalset.model_dump_json(), encoding="utf-8")

    optimizer = GepaReflectiveOptimizer(
        config=_make_config(),
        call_agent=_stub_call_agent,
        target_prompt=_new_target_prompt(),
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
    )

    fake_gepa_result = _FakeGEPAResult(
        candidates=[{"instruction": "initial"}, {"instruction": "improved"}],
        val_aggregate_scores=[0.5, 0.9],
        total_metric_calls=15,
    )

    async def fake_call_gepa(self, **kwargs):
        for s in kwargs["stop_callbacks"]:
            if isinstance(s, _RequiredMetricsAboveThresholdStopper):
                s.update({"m1": 0.9})
                s(gepa_state=None)
        return fake_gepa_result

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", fake_call_gepa)
    result = await optimizer.run()
    assert result.status == "SUCCEEDED"
    assert result.stop_reason == "required_metrics_passing"


@pytest.mark.asyncio
async def test_optimizer_run_stop_reason_completed_when_no_stopper_fires(
    tmp_path, monkeypatch
):
    """When gepa returns without firing any wrapped stopper (mock path),
    stop_reason must be 'completed' rather than the legacy 'budget_exhausted'
    catch-all so users can tell apart "loop drained naturally" from a real
    budget cap hit."""
    train_evalset = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val_evalset = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train_evalset.model_dump_json(), encoding="utf-8")
    val_path.write_text(val_evalset.model_dump_json(), encoding="utf-8")

    optimizer = GepaReflectiveOptimizer(
        config=_make_config(),
        call_agent=_stub_call_agent,
        target_prompt=_new_target_prompt(),
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
    )

    fake_gepa_result = _FakeGEPAResult(
        candidates=[{"instruction": "initial"}, {"instruction": "improved"}],
        val_aggregate_scores=[0.5, 0.6],
    )

    async def fake_call_gepa(self, **kwargs):
        return fake_gepa_result

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", fake_call_gepa)
    result = await optimizer.run()
    assert result.status == "SUCCEEDED"
    assert result.stop_reason == "completed"


@pytest.mark.asyncio
async def test_optimizer_run_stop_reason_no_improvement_when_that_stopper_fires(
    tmp_path, monkeypatch
):
    """When the wrapped NoImprovementStopper signals last_triggered (by gepa
    polling it past the configured patience), stop_reason must be
    'no_improvement' so reporters and summary.txt can attribute the stop
    correctly instead of falsely blaming the budget."""
    train_evalset = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val_evalset = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train_evalset.model_dump_json(), encoding="utf-8")
    val_path.write_text(val_evalset.model_dump_json(), encoding="utf-8")

    optimizer = GepaReflectiveOptimizer(
        config=_make_config(max_iterations_without_improvement=3),
        call_agent=_stub_call_agent,
        target_prompt=_new_target_prompt(),
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
    )

    fake_gepa_result = _FakeGEPAResult(
        candidates=[{"instruction": "initial"}, {"instruction": "improved"}],
        val_aggregate_scores=[0.5, 0.6],
    )

    async def fake_call_gepa(self, **kwargs):
        for stopper in kwargs["stop_callbacks"]:
            if isinstance(stopper, _LabeledStopper) and stopper.label == "no_improvement":
                stopper.last_triggered = True
                break
        return fake_gepa_result

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", fake_call_gepa)
    result = await optimizer.run()
    assert result.status == "SUCCEEDED"
    assert result.stop_reason == "no_improvement"


@pytest.mark.asyncio
async def test_optimizer_run_stop_reason_budget_exhausted_when_max_metric_calls_fires(
    tmp_path, monkeypatch
):
    """When MaxMetricCallsStopper is the only fired wrapper, stop_reason is
    'budget_exhausted'. This locks the label mapping for the legacy
    catch-all so a budget cap hit still carries the historical name users
    see in reports."""
    train_evalset = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val_evalset = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train_evalset.model_dump_json(), encoding="utf-8")
    val_path.write_text(val_evalset.model_dump_json(), encoding="utf-8")

    optimizer = GepaReflectiveOptimizer(
        config=_make_config(),
        call_agent=_stub_call_agent,
        target_prompt=_new_target_prompt(),
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
    )

    fake_gepa_result = _FakeGEPAResult(
        candidates=[{"instruction": "initial"}, {"instruction": "improved"}],
        val_aggregate_scores=[0.5, 0.6],
    )

    async def fake_call_gepa(self, **kwargs):
        for stopper in kwargs["stop_callbacks"]:
            if isinstance(stopper, _LabeledStopper) and stopper.label == "budget_exhausted":
                stopper.last_triggered = True
                break
        return fake_gepa_result

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", fake_call_gepa)
    result = await optimizer.run()
    assert result.status == "SUCCEEDED"
    assert result.stop_reason == "budget_exhausted"


def test_labeled_stopper_records_last_triggered_only_when_inner_returns_true():
    """``_LabeledStopper.__call__`` delegates the return value to the inner
    stopper and flips ``last_triggered`` sticky once the inner ever returns
    True; subsequent False results never clear the flag."""
    calls: list[bool] = []

    class _ScriptedInner:
        def __call__(self, *_args, **_kwargs):
            return calls.pop(0)

    wrapper = _LabeledStopper(_ScriptedInner(), "no_improvement")
    assert wrapper.label == "no_improvement"
    assert wrapper.last_triggered is False

    calls.extend([False, True, False])
    assert wrapper() is False
    assert wrapper.last_triggered is False
    assert wrapper() is True
    assert wrapper.last_triggered is True
    assert wrapper() is False
    assert wrapper.last_triggered is True


def test_build_stop_callbacks_wraps_each_gepa_stopper_with_a_labeled_stopper():
    """Every algorithm-side stop knob the user enables must end up wrapped in
    a ``_LabeledStopper`` carrying the matching label, so the optimizer can
    classify ``stop_reason`` precisely after gepa returns."""
    algo = GepaReflectiveAlgo(
        name="gepa_reflective",
        reflection_lm=OptimizeModelOptions(provider_name="openai", model_name="m"),
        max_metric_calls=10,
        max_iterations_without_improvement=3,
        timeout_seconds=60.0,
        score_threshold=0.95,
        max_candidate_proposals=5,
        max_tracked_candidates=4,
    )
    stop_callbacks, _framework = _build_stop_callbacks(
        algo=algo,
        stop_config=FrameworkStopConfig(required_metrics=None),
        metric_thresholds={"m1": 1.0},
    )
    labels = {
        s.label
        for s in stop_callbacks
        if isinstance(s, _LabeledStopper)
    }
    assert labels == {
        "budget_exhausted",
        "no_improvement",
        "timeout",
        "score_threshold",
        "max_candidate_proposals",
        "max_tracked_candidates",
    }


def test_classify_stop_reason_prefers_framework_stopper_over_labeled_ones():
    """When both the framework stopper and a labeled gepa stopper fired in
    the same run, ``required_metrics_passing`` wins because it represents
    the user's explicit opt-in stop policy."""
    framework = _RequiredMetricsAboveThresholdStopper({"m": 0.5})
    framework.last_triggered = True
    labeled = _LabeledStopper(lambda *_: False, "no_improvement")
    labeled.last_triggered = True
    assert (
        _classify_stop_reason(
            stop_callbacks=[labeled, framework],
            framework_stopper=framework,
        )
        == "required_metrics_passing"
    )


def test_classify_stop_reason_returns_completed_when_no_stopper_fires():
    """No stopper triggered ⇒ gepa loop ended naturally. The ``completed``
    label distinguishes this from any real stop cap so users can tell the
    difference in summary.txt and the terminal banner."""
    framework = _RequiredMetricsAboveThresholdStopper({"m": 0.5})
    labeled = _LabeledStopper(lambda *_: False, "timeout")
    assert (
        _classify_stop_reason(
            stop_callbacks=[labeled, framework],
            framework_stopper=framework,
        )
        == "completed"
    )


@pytest.mark.asyncio
async def test_optimizer_run_wires_stopper_update_into_callback(
    tmp_path, monkeypatch
):
    """The callback must receive the stopper's update as on_valset_breakdown so
    in a real gepa run the stopper's _latest tracks the most recent valset."""
    train_evalset = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val_evalset = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train_evalset.model_dump_json(), encoding="utf-8")
    val_path.write_text(val_evalset.model_dump_json(), encoding="utf-8")

    optimizer = GepaReflectiveOptimizer(
        config=_make_config(),
        call_agent=_stub_call_agent,
        target_prompt=_new_target_prompt(),
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
    )

    fake_gepa_result = _FakeGEPAResult(
        candidates=[{"instruction": "initial"}, {"instruction": "improved"}],
        val_aggregate_scores=[0.5, 0.9],
    )
    captured: dict = {}

    async def fake_call_gepa(self, **kwargs):
        captured["stop_callbacks"] = kwargs["stop_callbacks"]
        captured["gepa_callback"] = kwargs["callbacks"][0]
        return fake_gepa_result

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", fake_call_gepa)
    await optimizer.run()
    stopper = next(
        s
        for s in captured["stop_callbacks"]
        if isinstance(s, _RequiredMetricsAboveThresholdStopper)
    )
    gepa_callback = captured["gepa_callback"]
    assert gepa_callback._on_valset_breakdown == stopper.update


@pytest.mark.asyncio
async def test_optimizer_run_returns_failed_when_gepa_raises(tmp_path, monkeypatch):
    train_evalset = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val_evalset = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train_evalset.model_dump_json(), encoding="utf-8")
    val_path.write_text(val_evalset.model_dump_json(), encoding="utf-8")

    recorder: dict[str, str] = {"instruction": "initial"}
    target = _new_target_prompt(recorder)
    optimizer = GepaReflectiveOptimizer(
        config=_make_config(),
        call_agent=_stub_call_agent,
        target_prompt=target,
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
    )

    async def fake_call_gepa(self, **kwargs):
        raise RuntimeError("simulated gepa failure")

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", fake_call_gepa)

    result = await optimizer.run()

    assert result.status == "FAILED"
    assert result.finish_reason == "error"
    assert "simulated gepa failure" in result.error_message
    assert recorder["instruction"] == "initial"


def test_stop_reason_literal_includes_user_requested_stop() -> None:
    from typing import get_args

    from trpc_agent_sdk.evaluation._optimize_result import StopReason

    assert "user_requested_stop" in get_args(StopReason)


def test_optimizer_constructor_stores_output_dir(tmp_path) -> None:
    """BaseOptimizer surfaces output_dir so subclasses can wire FileStopper."""
    config = OptimizeConfigFile(
        evaluate=EvalConfig(
            metrics=[{"metric_name": "m", "threshold": 0.5}],
            num_runs=1,
        ),
        optimize=OptimizeConfig(
            stop=FrameworkStopConfig(required_metrics=None),
            algorithm=GepaReflectiveAlgo(
                name="gepa_reflective",
                reflection_lm=OptimizeModelOptions(),
                max_metric_calls=1,
            ),
        ),
    )

    async def _call_agent(_q: str) -> str:
        return ""

    target_prompt = TargetPrompt().add_path("p", str(tmp_path / "p.md"))
    (tmp_path / "p.md").write_text("seed", encoding="utf-8")

    opt = GepaReflectiveOptimizer(
        config=config,
        call_agent=_call_agent,
        target_prompt=target_prompt,
        train_dataset_path=str(tmp_path / "t.json"),
        validation_dataset_path=str(tmp_path / "v.json"),
        output_dir=str(tmp_path / "runs/x"),
    )

    assert opt.output_dir == str(tmp_path / "runs/x")


def test_build_stop_callbacks_installs_file_stopper_when_output_dir_set(tmp_path) -> None:
    """When output_dir is provided, FileStopper labels new stops as user_requested_stop."""
    algo = GepaReflectiveAlgo(
        name="gepa_reflective",
        reflection_lm=OptimizeModelOptions(),
        max_metric_calls=10,
    )

    callbacks, _ = _build_stop_callbacks(
        algo,
        FrameworkStopConfig(required_metrics=None),
        metric_thresholds={},
        output_dir=str(tmp_path),
    )

    labels = [cb.label for cb in callbacks if isinstance(cb, _LabeledStopper)]
    assert "user_requested_stop" in labels


def test_file_stopper_fires_after_optimize_stop_file_appears(tmp_path) -> None:
    algo = GepaReflectiveAlgo(
        name="gepa_reflective",
        reflection_lm=OptimizeModelOptions(),
        max_metric_calls=10,
    )

    callbacks, _ = _build_stop_callbacks(
        algo,
        FrameworkStopConfig(required_metrics=None),
        metric_thresholds={},
        output_dir=str(tmp_path),
    )
    stopper = next(
        cb for cb in callbacks
        if isinstance(cb, _LabeledStopper)
        and cb.label == "user_requested_stop"
    )

    assert stopper(gepa_state=None) is False
    (tmp_path / "optimize.stop").write_text("", encoding="utf-8")
    assert stopper(gepa_state=None) is True
    assert stopper.last_triggered is True


def test_build_stop_callbacks_skips_file_stopper_when_output_dir_none() -> None:
    algo = GepaReflectiveAlgo(
        name="gepa_reflective",
        reflection_lm=OptimizeModelOptions(),
        max_metric_calls=10,
    )

    callbacks, _ = _build_stop_callbacks(
        algo,
        FrameworkStopConfig(required_metrics=None),
        metric_thresholds={},
        output_dir=None,
    )

    labels = [cb.label for cb in callbacks if isinstance(cb, _LabeledStopper)]
    assert "user_requested_stop" not in labels


def test_run_forwards_reflection_history_top_k_into_adapter(tmp_path, monkeypatch):
    """algo.reflection_history_top_k must reach the adapter constructor as top_k_per_case."""
    import asyncio
    import json
    from types import SimpleNamespace

    from trpc_agent_sdk.evaluation._eval_config import EvalConfig
    from trpc_agent_sdk.evaluation._optimize_config import (
        FrameworkStopConfig,
        GepaReflectiveAlgo,
        OptimizeConfig,
        OptimizeConfigFile,
    )
    from trpc_agent_sdk.evaluation._optimize_gepa_reflective import (
        GepaReflectiveOptimizer,
    )
    from trpc_agent_sdk.evaluation._optimize_model_options import (
        OptimizeModelOptions,
    )
    from trpc_agent_sdk.evaluation._target_prompt import TargetPrompt

    async def _call_agent(_q: str) -> str:
        return ""

    (tmp_path / "p.md").write_text("seed", encoding="utf-8")
    train_path = tmp_path / "t.json"
    val_path = tmp_path / "v.json"
    train_path.write_text(
        json.dumps({"eval_set_id": "t", "eval_cases": []}), encoding="utf-8"
    )
    val_path.write_text(
        json.dumps({"eval_set_id": "v", "eval_cases": []}), encoding="utf-8"
    )

    captured_kwargs: dict = {}

    def fake_init(self, **kwargs):
        captured_kwargs.update(kwargs)
        self.target_prompt = kwargs["target_prompt"]
        self.eval_config = kwargs["eval_config"]
        self.call_agent = kwargs["call_agent"]
        self.callbacks = kwargs.get("callbacks")
        self.num_runs = kwargs.get("num_runs", 1)
        self.case_parallelism = kwargs.get("case_parallelism")
        self._top_k = int(kwargs.get("top_k_per_case", 0))
        self._best_history = {}
        self.last_outcome = None

    monkeypatch.setattr(
        "trpc_agent_sdk.evaluation._optimize_gepa_adapter._AgentGEPAAdapter.__init__",
        fake_init,
    )

    async def _fake_call(self, **kwargs):
        return SimpleNamespace(
            best_idx=0,
            candidates=[{"p": "seed"}],
            val_aggregate_scores=[0.5],
        )

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", _fake_call)

    config = OptimizeConfigFile(
        evaluate=EvalConfig(metrics=[{"metric_name": "m", "threshold": 0.5}]),
        optimize=OptimizeConfig(
            stop=FrameworkStopConfig(required_metrics=None),
            algorithm=GepaReflectiveAlgo(
                name="gepa_reflective",
                reflection_lm=OptimizeModelOptions(),
                max_metric_calls=1,
                reflection_history_top_k=3,
            ),
        ),
    )
    target_prompt = TargetPrompt().add_path("p", str(tmp_path / "p.md"))

    opt = GepaReflectiveOptimizer(
        config=config,
        call_agent=_call_agent,
        target_prompt=target_prompt,
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
        output_dir=None,
    )
    asyncio.run(opt.run())

    assert captured_kwargs["top_k_per_case"] == 3


def test_optimizer_constructor_stores_extra_callbacks(tmp_path) -> None:
    """BaseOptimizer.__init__ must accept and store extra_stop/gepa_callbacks."""
    import json

    from trpc_agent_sdk.evaluation._eval_config import EvalConfig
    from trpc_agent_sdk.evaluation._optimize_config import (
        FrameworkStopConfig,
        GepaReflectiveAlgo,
        OptimizeConfig,
        OptimizeConfigFile,
    )
    from trpc_agent_sdk.evaluation._optimize_model_options import (
        OptimizeModelOptions,
    )
    from trpc_agent_sdk.evaluation._target_prompt import TargetPrompt

    async def _call_agent(_q: str) -> str:
        return ""

    (tmp_path / "p.md").write_text("seed", encoding="utf-8")
    train_path = tmp_path / "t.json"
    val_path = tmp_path / "v.json"
    train_path.write_text(
        json.dumps({"eval_set_id": "t", "eval_cases": []}), encoding="utf-8"
    )
    val_path.write_text(
        json.dumps({"eval_set_id": "v", "eval_cases": []}), encoding="utf-8"
    )

    config = OptimizeConfigFile(
        evaluate=EvalConfig(metrics=[{"metric_name": "m", "threshold": 0.5}]),
        optimize=OptimizeConfig(
            stop=FrameworkStopConfig(required_metrics=None),
            algorithm=GepaReflectiveAlgo(
                name="gepa_reflective",
                reflection_lm=OptimizeModelOptions(),
                max_metric_calls=1,
            ),
        ),
    )
    target_prompt = TargetPrompt().add_path("p", str(tmp_path / "p.md"))

    def sentinel_stopper(gepa_state=None):
        return False

    sentinel_callback = object()

    opt = GepaReflectiveOptimizer(
        config=config,
        call_agent=_call_agent,
        target_prompt=target_prompt,
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
        output_dir=str(tmp_path / "runs/x"),
        extra_stop_callbacks=[sentinel_stopper],
        extra_gepa_callbacks=[sentinel_callback],
    )

    assert sentinel_stopper in opt.extra_stop_callbacks
    assert sentinel_callback in opt.extra_gepa_callbacks


def test_run_extends_stop_callbacks_with_user_supplied_extras(tmp_path, monkeypatch):
    """User-supplied extras must be appended to stop_callbacks and the callbacks list."""
    import asyncio
    import json
    from types import SimpleNamespace

    from trpc_agent_sdk.evaluation._eval_config import EvalConfig
    from trpc_agent_sdk.evaluation._optimize_config import (
        FrameworkStopConfig,
        GepaReflectiveAlgo,
        OptimizeConfig,
        OptimizeConfigFile,
    )
    from trpc_agent_sdk.evaluation._optimize_gepa_reflective import (
        GepaReflectiveOptimizer,
    )
    from trpc_agent_sdk.evaluation._optimize_model_options import (
        OptimizeModelOptions,
    )
    from trpc_agent_sdk.evaluation._target_prompt import TargetPrompt

    async def _call_agent(_q: str) -> str:
        return ""

    (tmp_path / "p.md").write_text("seed", encoding="utf-8")
    train_path = tmp_path / "t.json"
    val_path = tmp_path / "v.json"
    train_path.write_text(
        json.dumps({"eval_set_id": "t", "eval_cases": []}), encoding="utf-8"
    )
    val_path.write_text(
        json.dumps({"eval_set_id": "v", "eval_cases": []}), encoding="utf-8"
    )

    def sentinel_stopper_a(gepa_state=None):
        return False

    def sentinel_stopper_b(gepa_state=None):
        return False

    sentinel_callback = SimpleNamespace(tag="user-cb")

    captured: dict = {}

    async def _fake_call(self, **kwargs):
        captured["stop_callbacks"] = list(kwargs.get("stop_callbacks") or [])
        captured["callbacks"] = list(kwargs.get("callbacks") or [])
        return SimpleNamespace(
            best_idx=0,
            candidates=[{"p": "seed"}],
            val_aggregate_scores=[0.5],
        )

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", _fake_call)

    config = OptimizeConfigFile(
        evaluate=EvalConfig(metrics=[{"metric_name": "m", "threshold": 0.5}]),
        optimize=OptimizeConfig(
            stop=FrameworkStopConfig(required_metrics=None),
            algorithm=GepaReflectiveAlgo(
                name="gepa_reflective",
                reflection_lm=OptimizeModelOptions(),
                max_metric_calls=1,
            ),
        ),
    )
    target_prompt = TargetPrompt().add_path("p", str(tmp_path / "p.md"))

    opt = GepaReflectiveOptimizer(
        config=config,
        call_agent=_call_agent,
        target_prompt=target_prompt,
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
        output_dir=None,
        extra_stop_callbacks=[sentinel_stopper_a, sentinel_stopper_b],
        extra_gepa_callbacks=[sentinel_callback],
    )
    asyncio.run(opt.run())

    assert sentinel_stopper_a in captured["stop_callbacks"]
    assert sentinel_stopper_b in captured["stop_callbacks"]
    assert sentinel_callback in captured["callbacks"]
