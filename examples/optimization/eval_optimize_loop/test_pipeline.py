"""Acceptance tests for the offline evaluation and optimization loop."""

from __future__ import annotations

import json
import time
from pathlib import Path

from pipeline import EvalOptimizePipeline


HERE = Path(__file__).resolve().parent


def test_all_six_cases_run_and_report_is_complete(tmp_path):
    report = EvalOptimizePipeline(HERE).run(tmp_path)
    baseline_cases = report["baseline"]["train_cases"] + report["baseline"]["validation_cases"]
    candidate_cases = report["candidate"]["train_cases"] + report["candidate"]["validation_cases"]
    assert len(baseline_cases) == len(candidate_cases) == 6
    assert (tmp_path / "optimization_report.json").is_file()
    assert (tmp_path / "optimization_report.md").is_file()
    assert {"baseline", "candidate", "delta", "gate", "failure_attribution"} <= report.keys()
    assert all(
        "trace" in item and "score" in item and "metric_scores" in item and "passed" in item
        for item in baseline_cases
    )


def test_overfit_candidate_is_rejected():
    report = EvalOptimizePipeline(HERE).run()
    assert report["delta"]["train_score"] > 0
    assert report["delta"]["validation_score"] < 0
    assert report["gate"]["decision"] == "reject"
    assert report["gate"]["observed"]["new_hard_fails"] == 1
    assert any("critical" in reason for reason in report["gate"]["reasons"])


def test_failure_attribution_matches_expected_categories():
    pipeline = EvalOptimizePipeline(HERE)
    train = pipeline._load_json("train.evalset.json")["eval_cases"]
    results = pipeline.evaluate(train, "baseline")
    assert [item.attribution for item in results] == [
        "format_noncompliance",
        "knowledge_recall_insufficient",
        "parameter_error",
    ]
    assert all(item.reason for item in results if not item.passed)


def test_gate_accepts_clean_validation_improvement():
    pipeline = EvalOptimizePipeline(HERE)
    cases = pipeline._load_json("val.evalset.json")["eval_cases"][:1]
    baseline = pipeline.evaluate(cases, "baseline")
    candidate = pipeline.evaluate(cases, "candidate")
    comparison = pipeline.compare(baseline, candidate)
    gate = pipeline.apply_gate(baseline, candidate, comparison)
    assert gate["decision"] == "accept"


def test_prompt_source_is_not_modified(tmp_path):
    before = (HERE / "prompt.md").read_text(encoding="utf-8")
    report = EvalOptimizePipeline(HERE).run(tmp_path)
    assert (HERE / "prompt.md").read_text(encoding="utf-8") == before
    assert report["candidate"]["prompt"] != before


def test_trace_pipeline_finishes_well_below_three_minutes(tmp_path):
    started = time.perf_counter()
    EvalOptimizePipeline(HERE).run(tmp_path)
    assert time.perf_counter() - started < 180


def test_example_report_is_valid_json():
    report = json.loads((HERE / "optimization_report.json").read_text(encoding="utf-8"))
    assert report["gate"]["decision"] == "reject"
    assert len(report["delta"]["cases"]) == 3
