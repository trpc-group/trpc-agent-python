# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for OptimizeResult / RoundRecord / dump_to / from_file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trpc_agent_sdk.evaluation._optimize_result import OptimizeResult
from trpc_agent_sdk.evaluation._optimize_result import RoundRecord


def _round_record(round_idx: int = 1, accepted: bool = True) -> RoundRecord:
    return RoundRecord(
        round=round_idx,
        optimized_field_names=["system_prompt"],
        candidate_prompts={"system_prompt": f"v{round_idx}"},
        train_pass_rate=0.5 + 0.1 * round_idx,
        validation_pass_rate=0.4 + 0.1 * round_idx,
        metric_breakdown={"final_response_avg_score": 0.6},
        accepted=accepted,
        acceptance_reason=("validation_pass_rate gain 0.10 >= min_score_gain 0.0"
                           if accepted else "validation_pass_rate gain -0.02 < min_score_gain 0.0"),
        failed_case_ids=["c1", "c2"],
        failed_cases_truncated=0,
        per_field_diagnosis={"system_prompt": "model said: be more careful"},
        reflection_lm_calls=1,
        round_llm_cost=0.012,
        round_token_usage={"prompt": 100, "completion": 50, "total": 150},
        started_at="2026-05-14T19:30:00Z",
        duration_seconds=2.5,
    )


def _optimize_result(rounds: list[RoundRecord] | None = None) -> OptimizeResult:
    rounds = rounds or [_round_record(1, accepted=True)]
    return OptimizeResult(
        algorithm="gepa_reflective",
        status="SUCCEEDED",
        finish_reason="completed",
        baseline_pass_rate=0.4,
        best_pass_rate=0.6,
        pass_rate_improvement=0.2,
        baseline_metric_breakdown={"final_response_avg_score": 0.5},
        best_metric_breakdown={"final_response_avg_score": 0.7},
        baseline_prompts={"system_prompt": "v0"},
        best_prompts={"system_prompt": "v1"},
        total_rounds=len(rounds),
        rounds=rounds,
        total_reflection_lm_calls=1,
        total_judge_model_calls=8,
        total_llm_cost=0.05,
        total_token_usage={"prompt": 200, "completion": 100, "total": 300},
        duration_seconds=5.0,
        started_at="2026-05-14T19:30:00Z",
        finished_at="2026-05-14T19:30:05Z",
    )


def test_optimize_result_algorithm_field_required():
    """algorithm must be a top-level required field per spec §3.6 / acceptance #20."""
    import pydantic

    with pytest.raises(pydantic.ValidationError) as exc:
        OptimizeResult(
            status="SUCCEEDED",
            finish_reason="completed",
            baseline_pass_rate=0.0,
            best_pass_rate=0.0,
            pass_rate_improvement=0.0,
            total_rounds=0,
            total_reflection_lm_calls=0,
            total_judge_model_calls=0,
            duration_seconds=0.0,
            started_at="t0",
            finished_at="t1",
        )
    assert any("algorithm" in str(e["loc"]) for e in exc.value.errors())


def test_optimize_result_algorithm_field_round_trips(tmp_path: Path):
    result = _optimize_result()
    assert result.algorithm == "gepa_reflective"
    target = tmp_path / "r.json"
    result.dump_to(str(target))
    loaded = OptimizeResult.from_file(str(target))
    assert loaded.algorithm == "gepa_reflective"


def test_optimize_result_metric_thresholds_defaults_to_empty_dict():
    result = _optimize_result()
    assert result.metric_thresholds == {}


def test_optimize_result_metric_thresholds_round_trip(tmp_path: Path):
    result = _optimize_result().model_copy(
        update={
            "metric_thresholds": {
                "final_response_avg_score": 0.5,
                "response_match_score": 0.3,
            }
        }
    )
    path = tmp_path / "with_thresholds.json"
    result.dump_to(str(path))
    loaded = OptimizeResult.from_file(str(path))
    assert loaded.metric_thresholds == {
        "final_response_avg_score": 0.5,
        "response_match_score": 0.3,
    }


def test_optimize_result_format_summary_includes_thresholds_when_provided():
    result = _optimize_result().model_copy(
        update={
            "metric_thresholds": {"final_response_avg_score": 0.5},
            "baseline_metric_breakdown": {"final_response_avg_score": 0.4},
            "best_metric_breakdown": {"final_response_avg_score": 0.9},
        }
    )
    summary = result.format_summary(output_dir="/tmp/runs/x", update_source=False)
    assert "threshold | baseline -> best" in summary
    assert "threshold 0.5000" in summary
    assert "0.4000 -> 0.9000" in summary


def test_round_record_minimal_construction():
    record = _round_record()
    assert record.round == 1
    assert record.accepted is True
    assert record.round_llm_cost == 0.012
    assert record.round_token_usage == {"prompt": 100, "completion": 50, "total": 150}


def test_round_record_extras_defaults_to_empty_dict():
    record = _round_record()
    assert record.extras == {}


def test_round_record_extras_accepts_arbitrary_payload():
    record = RoundRecord(
        round=1,
        optimized_field_names=["a"],
        candidate_prompts={"a": "x"},
        train_pass_rate=0.5,
        validation_pass_rate=0.5,
        metric_breakdown={},
        accepted=False,
        acceptance_reason="",
        failed_case_ids=[],
        failed_cases_truncated=0,
        per_field_diagnosis={},
        reflection_lm_calls=0,
        round_llm_cost=0.0,
        round_token_usage={"prompt": 0, "completion": 0, "total": 0},
        started_at="2026-05-14T19:30:00Z",
        duration_seconds=1.0,
        extras={"judge_subscores": [0.5, 0.6], "wandb_step": 7},
    )
    assert record.extras["judge_subscores"] == [0.5, 0.6]
    assert record.extras["wandb_step"] == 7


def test_optimize_result_minimal_construction():
    result = _optimize_result()
    assert result.schema_version == "v1"
    assert result.status == "SUCCEEDED"
    assert result.finish_reason == "completed"
    assert result.baseline_pass_rate == 0.4
    assert result.best_pass_rate == 0.6
    assert result.pass_rate_improvement == 0.2
    assert result.total_rounds == 1
    assert len(result.rounds) == 1
    assert result.extras == {}


def test_optimize_result_default_token_usage_is_zero():
    result = OptimizeResult(
        algorithm="gepa_reflective",
        status="SUCCEEDED",
        finish_reason="completed",
        baseline_pass_rate=0.0,
        best_pass_rate=0.0,
        pass_rate_improvement=0.0,
        baseline_metric_breakdown={},
        best_metric_breakdown={},
        baseline_prompts={},
        best_prompts={},
        total_rounds=0,
        rounds=[],
        total_reflection_lm_calls=0,
        total_judge_model_calls=0,
        duration_seconds=0.0,
        started_at="2026-05-14T19:30:00Z",
        finished_at="2026-05-14T19:30:00Z",
    )
    assert result.total_llm_cost == 0.0
    assert result.total_token_usage == {"prompt": 0, "completion": 0, "total": 0}
    assert result.extras == {}
    assert result.error_message == ""


@pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "CANCELED"])
def test_optimize_result_run_status_accepts_all_legal_values(status):
    result = _optimize_result()
    new = result.model_copy(update={"status": status})
    assert new.status == status


def test_optimize_result_rejects_illegal_run_status():
    with pytest.raises(Exception):
        OptimizeResult.model_validate({**_optimize_result().model_dump(), "status": "unknown"})


@pytest.mark.parametrize("reason", [
    "completed",
    "perfect_pass_rate",
    "no_improvement",
    "error",
])
def test_optimize_result_finish_reason_accepts_all_legal_values(reason):
    result = _optimize_result()
    new = result.model_copy(update={"finish_reason": reason})
    assert new.finish_reason == reason


def test_optimize_result_rejects_illegal_finish_reason():
    with pytest.raises(Exception):
        OptimizeResult.model_validate({**_optimize_result().model_dump(), "finish_reason": "weird"})


def test_optimize_result_rejects_removed_cancelled_finish_reason():
    """DOC-4: 'cancelled' was removed from FinishReason because no SDK code path
    ever produces it; user cancellation surfaces as stop_reason='user_requested_stop'
    + status='CANCELED'. Schema must reject it to keep the literal set honest."""
    with pytest.raises(Exception):
        OptimizeResult.model_validate(
            {**_optimize_result().model_dump(), "finish_reason": "cancelled"}
        )


def test_optimize_result_model_dump_json_round_trip():
    original = _optimize_result()
    payload = original.model_dump_json()
    restored = OptimizeResult.model_validate_json(payload)
    assert restored == original


def test_optimize_result_dump_to_creates_indented_json_file(tmp_path: Path):
    path = tmp_path / "result.json"
    result = _optimize_result()
    result.dump_to(str(path))
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert payload["status"] == "SUCCEEDED"
    assert payload["finishReason"] == "completed"
    assert "\n" in text


def test_optimize_result_from_file_round_trip(tmp_path: Path):
    path = tmp_path / "result.json"
    original = _optimize_result()
    original.dump_to(str(path))
    restored = OptimizeResult.from_file(str(path))
    assert restored == original


def test_round_record_new_reporter_fields_default_to_none_or_zero():
    """New fields reporter and artifact persistence consume must default
    safely so existing callers keep working unchanged."""
    record = _round_record()
    assert record.kind == "reflective"
    assert record.train_minibatch_size == 0
    assert record.train_subsample_parent_score is None
    assert record.train_subsample_candidate_score is None
    assert record.skip_reason is None
    assert record.error_message is None
    assert record.budget_used is None
    assert record.budget_total is None


def test_round_record_new_reporter_fields_round_trip():
    record = RoundRecord(
        round=2,
        optimized_field_names=[],
        candidate_prompts={"a": "x"},
        train_pass_rate=0.5,
        validation_pass_rate=0.0,
        metric_breakdown={},
        accepted=False,
        acceptance_reason="",
        failed_case_ids=[],
        failed_cases_truncated=0,
        per_field_diagnosis={},
        reflection_lm_calls=0,
        round_llm_cost=0.0,
        round_token_usage={"prompt": 0, "completion": 0, "total": 0},
        started_at="2026-05-17T16:30:00Z",
        duration_seconds=2.1,
        kind="merge",
        train_minibatch_size=2,
        train_subsample_parent_score=0.6,
        train_subsample_candidate_score=0.4,
        skip_reason=None,
        error_message=None,
        budget_used=42,
        budget_total=200,
    )
    payload = record.model_dump_json()
    restored = RoundRecord.model_validate_json(payload)
    assert restored == record
    assert restored.kind == "merge"
    assert restored.train_minibatch_size == 2
    assert restored.budget_used == 42
    assert restored.budget_total == 200


def test_optimize_result_format_summary_succeeded_contains_key_fields():
    """format_summary renders the human-readable summary.txt artifact and
    must surface algorithm, status, baseline/best pass rates, delta,
    rounds and best_prompts inventory."""
    result = _optimize_result()
    summary = result.format_summary(
        output_dir="/tmp/runs/2026-05-17T16-30-00",
        update_source=False,
    )
    assert "gepa_reflective" in summary
    assert "SUCCEEDED" in summary
    assert "0.4000" in summary and "0.6000" in summary
    assert "+0.2000" in summary or "+0.20" in summary
    assert "improved" in summary
    assert "system_prompt" in summary
    assert "/tmp/runs/2026-05-17T16-30-00" in summary


def test_optimize_result_format_summary_failed_includes_error_message():
    result = _optimize_result().model_copy(update={
        "status": "FAILED",
        "finish_reason": "error",
        "error_message": "dataset load failed: missing file",
    })
    summary = result.format_summary(
        output_dir="/tmp/runs/x", update_source=True,
    )
    assert "FAILED" in summary
    assert "dataset load failed" in summary


def test_optimize_result_from_file_missing_path_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        OptimizeResult.from_file(str(tmp_path / "nope.json"))


def test_optimize_result_camel_alias_export():
    result = _optimize_result()
    dumped = result.model_dump(by_alias=True)
    assert "schemaVersion" in dumped
    assert "finishReason" in dumped
    assert "baselinePassRate" in dumped
    assert "totalTokenUsage" in dumped


def test_optimize_result_camel_case_input_accepted():
    payload = _optimize_result().model_dump(by_alias=True)
    restored = OptimizeResult.model_validate(payload)
    assert restored == _optimize_result()


def test_optimize_result_extras_round_trip_through_file(tmp_path: Path):
    result = _optimize_result().model_copy(
        update={"extras": {"wandb_run_id": "abc-123", "git_sha": "deadbeef"}}
    )
    path = tmp_path / "result.json"
    result.dump_to(str(path))
    restored = OptimizeResult.from_file(str(path))
    assert restored.extras == {"wandb_run_id": "abc-123", "git_sha": "deadbeef"}


def test_optimize_result_dump_to_overwrites_existing_file(tmp_path: Path):
    path = tmp_path / "result.json"
    path.write_text("stale content", encoding="utf-8")
    result = _optimize_result()
    result.dump_to(str(path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "SUCCEEDED"


def test_optimize_result_with_multiple_rounds():
    rounds = [
        _round_record(round_idx=1, accepted=True),
        _round_record(round_idx=2, accepted=False),
        _round_record(round_idx=3, accepted=True),
    ]
    result = _optimize_result(rounds=rounds)
    assert result.total_rounds == 3
    assert result.rounds[1].accepted is False
    payload = result.model_dump_json()
    restored = OptimizeResult.model_validate_json(payload)
    assert [r.accepted for r in restored.rounds] == [True, False, True]


# ---------------------------------------------------------------------------
# stop_reason
# ---------------------------------------------------------------------------


def test_optimize_result_stop_reason_defaults_to_none():
    result = _optimize_result()
    assert result.stop_reason is None


@pytest.mark.parametrize(
    "reason", ["required_metrics_passing", "budget_exhausted"],
)
def test_optimize_result_stop_reason_accepts_legal_values(reason):
    result = _optimize_result().model_copy(update={"stop_reason": reason})
    assert result.stop_reason == reason


def test_optimize_result_stop_reason_rejects_illegal_value():
    with pytest.raises(Exception):
        OptimizeResult.model_validate(
            {**_optimize_result().model_dump(), "stop_reason": "weird"}
        )


def test_optimize_result_stop_reason_round_trip(tmp_path: Path):
    result = _optimize_result().model_copy(
        update={"stop_reason": "required_metrics_passing"}
    )
    target = tmp_path / "r.json"
    result.dump_to(str(target))
    loaded = OptimizeResult.from_file(str(target))
    assert loaded.stop_reason == "required_metrics_passing"


def test_optimize_result_format_summary_includes_stop_reason_when_set():
    result = _optimize_result().model_copy(
        update={"stop_reason": "required_metrics_passing"}
    )
    summary = result.format_summary(output_dir="/tmp/x", update_source=False)
    assert "stop_reason" in summary
    assert "required_metrics_passing" in summary


def test_optimize_result_format_summary_omits_stop_reason_when_none():
    result = _optimize_result()
    summary = result.format_summary(output_dir="/tmp/x", update_source=False)
    assert "stop_reason" not in summary
