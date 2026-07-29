#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2025 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Evaluator evidence extraction and labeled holdout tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import attribution
import gates
import report
import runner

pytestmark = pytest.mark.asyncio


def _content(text: str) -> dict:
    return {"role": "model", "parts": [{"text": text}]}


def _invocation(
    invocation_id: str,
    *,
    query: str,
    answer: str,
    tools: list[dict],
) -> dict:
    return {
        "invocation_id": invocation_id,
        "user_content": {"role": "user", "parts": [{"text": query}]},
        "final_response": _content(answer),
        "intermediate_data": {"tool_uses": tools, "tool_responses": []},
    }


async def test_tool_and_parameter_categories_come_from_real_evaluator_trace(
    tmp_path: Path,
) -> None:
    expected_tool = {"id": "t1", "name": "search", "args": {"query": "pricing"}}
    trace = {
        "eval_set_id": "attribution_trace",
        "eval_cases": [
            {
                "eval_id": "tool_missing",
                "eval_mode": "trace",
                "actual_conversation": [
                    _invocation(
                        "actual-1",
                        query="find pricing",
                        answer="pricing found",
                        tools=[],
                    )
                ],
                "conversation": [
                    _invocation(
                        "expected-1",
                        query="find pricing",
                        answer="pricing found",
                        tools=[expected_tool],
                    )
                ],
            },
            {
                "eval_id": "param_wrong",
                "eval_mode": "trace",
                "actual_conversation": [
                    _invocation(
                        "actual-2",
                        query="find pricing",
                        answer="pricing found",
                        tools=[
                            {
                                "id": "a2",
                                "name": "search",
                                "args": {"query": "weather"},
                            }
                        ],
                    )
                ],
                "conversation": [
                    _invocation(
                        "expected-2",
                        query="find pricing",
                        answer="pricing found",
                        tools=[expected_tool],
                    )
                ],
            },
        ],
    }
    config = {
        "metrics": [
            {
                "metric_name": "tool_trajectory_avg_score",
                "threshold": 1.0,
                "criterion": {
                    "tool_trajectory": {
                        "default": {
                            "name": {"match": "exact", "case_insensitive": False},
                            "arguments": {"match": "exact"},
                        },
                        "order_sensitive": False,
                        "subset_matching": False,
                    }
                },
            }
        ]
    }
    evalset_path = tmp_path / "tool_trace.evalset.json"
    config_path = tmp_path / "test_config.json"
    evalset_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    result = await runner._run_evaluator(
        evalset_path,
        metric_config_path=config_path,
    )
    flattened = runner._flatten_results(result)

    categories: dict[str, str] = {}
    for case in trace["eval_cases"]:
        record = runner._case_record(
            case=case,
            split="holdout",
            champion_runs=flattened[case["eval_id"]],
            challenger_runs=flattened[case["eval_id"]],
        )
        assert record.actual_text == "pricing found"
        assert record.expected_text == "pricing found"
        assert record.metric_results
        assert record.trace_ref
        categories[case["eval_id"]] = attribution.from_case_record(record).category

    assert categories == {
        "tool_missing": "tool_call_error",
        "param_wrong": "param_error",
    }


async def test_labeled_evidence_holdout_reports_actual_accuracy(
    loop_root: Path,
    tmp_path: Path,
) -> None:
    """The checked-in public holdout is measured, never called a hidden set."""

    payload = json.loads((loop_root / "data" / "attribution_holdout.json").read_text(encoding="utf-8"))
    correct = 0
    predictions: dict[str, str] = {}
    records: list[runner.CaseRecord] = []
    for case in payload["cases"]:
        result = attribution.classify_with_reason(
            attribution.AttributionInput(
                status=case["status"],
                metric_names_failed=tuple(case.get("failed_metrics", [])),
                metric_reasons=tuple(case.get("metric_reasons", [])),
                expected_text=case.get("expected_text"),
                actual_text=case.get("actual_text"),
                expected_tool_uses=tuple(case.get("expected_tool_uses", [])),
                actual_tool_uses=tuple(case.get("actual_tool_uses", [])),
                expected_tool_responses=tuple(case.get("expected_tool_responses", [])),
                actual_tool_responses=tuple(case.get("actual_tool_responses", [])),
                error_message=case.get("error_message"),
            )
        )
        predictions[case["eval_id"]] = result.category
        correct += result.category == case["expected_category"]
        records.append(
            runner.CaseRecord(
                eval_id=case["eval_id"],
                split="val",
                slice_name="holdout",
                risk_level="low",
                protected=False,
                scenario_tag="deliberately_ignored",
                champion_status="FAILED",
                challenger_status=case["status"],
                champion_score=0.0,
                challenger_score=0.0,
                expected_text=case.get("expected_text"),
                actual_text=case.get("actual_text"),
                metric_results=[
                    {
                        "metric_name": metric_name,
                        "score": 0.0,
                        "threshold": 1.0,
                        "eval_status": "FAILED",
                        "reason": "; ".join(case.get("metric_reasons", [])),
                        "rubric_scores": (
                            [{"id": "r1", "score": 0.0, "reason": "failed"}] if "rubric" in metric_name else []
                        ),
                    }
                    for metric_name in case.get("failed_metrics", [])
                ],
                expected_tool_uses=case.get("expected_tool_uses", []),
                actual_tool_uses=case.get("actual_tool_uses", []),
                expected_tool_responses=case.get("expected_tool_responses", []),
                actual_tool_responses=case.get("actual_tool_responses", []),
                error_message=case.get("error_message"),
                failure_reasons=case.get("metric_reasons", []),
                trace_ref=f"attribution_holdout.json#eval_id={case['eval_id']}",
            )
        )

    assert len(predictions) == 8
    assert correct / len(predictions) == 1.0

    frozen = runner.FrozenInputs(
        run_id="holdout-report",
        champion_sha256="champion",
        challenger_sha256="challenger",
        train_sha256="train",
        val_sha256="val",
        metric_config_sha256="metric",
        run_config_sha256="run",
        optimizer_config_sha256=None,
        seed=42,
        started_at="2026-07-28T00:00:00.000Z",
        mode="fixture",
        candidate_source="labeled_holdout",
    )
    split = runner.SplitResult(champion_avg=0.0, challenger_avg=0.0)
    artifact = runner.RunArtifact(
        frozen=frozen,
        train=split,
        val=split,
        cases=records,
        champion_train_avg=0.0,
        champion_val_avg=0.0,
        artifact_dir=tmp_path,
        cost_status="measured",
        total_tokens=0,
        total_cost=0.0,
        duration_seconds=0.0,
        champion_prompt_text="",
        challenger_prompt_text="",
    )
    report_payload = report.build_report_dict(
        artifact,
        gates.Decision(accepted=False, violated=["G1"], reasons=["fixture"]),
        applied=False,
        before_apply_sha256="champion",
        after_apply_sha256=None,
        repro_cmd="pytest test_evaluator_evidence.py",
    )
    report.write_report(report_payload, out_dir=tmp_path)
    persisted = json.loads((tmp_path / "optimization_report.json").read_text(encoding="utf-8"))
    per_case = {case["eval_id"]: case for case in persisted["per_case"]}
    assert per_case["holdout_tool"]["evidence"]["expected_tool_uses"]
    assert per_case["holdout_param"]["evidence"]["attribution"]["parameter_differences"]
    assert per_case["holdout_rubric"]["evidence"]["metric_results"][0]["rubric_scores"]
    assert per_case["holdout_infra"]["failure_kind"] == "infrastructure_failure"
    assert per_case["holdout_reply"]["failure_kind"] == "agent_quality_failure"
