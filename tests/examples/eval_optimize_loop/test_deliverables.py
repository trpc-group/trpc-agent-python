"""Issue-level deliverable contract tests for the public example."""

from __future__ import annotations

import json
import re
from pathlib import Path

from examples.optimization.eval_optimize_loop.pipeline.models import (
    Decision,
    OptimizationReport,
    Transition,
)
from examples.optimization.eval_optimize_loop.pipeline.reporting import render_markdown

EXAMPLE_ROOT = (Path(__file__).resolve().parents[3] / "examples" / "optimization" / "eval_optimize_loop")


def _dataset(name: str) -> dict:
    return json.loads((EXAMPLE_ROOT / name).read_text(encoding="utf-8"))


def test_public_datasets_cover_required_case_matrix() -> None:
    train = _dataset("train.evalset.json")["evalCases"]
    validation = _dataset("val.evalset.json")["evalCases"]
    assert len(train) == 3
    assert len(validation) == 3
    assert {case["evalId"] for case in train}.isdisjoint(case["evalId"] for case in validation)
    queries = [case["conversation"][0]["userContent"]["parts"][0]["text"] for case in (*train, *validation)]
    assert all(
        any(f"BEHAVIOR:{behavior}" in query for query in queries) for behavior in ("improve", "stable", "regress"))


def test_solution_is_300_to_500_chinese_characters_and_covers_required_topics() -> None:
    solution = (EXAMPLE_ROOT / "SOLUTION.md").read_text(encoding="utf-8")
    chinese_characters = re.findall(r"[\u4e00-\u9fff]", solution)
    assert 300 <= len(chinese_characters) <= 500
    for topic in ("失败归因", "接受门禁", "过拟合", "审计"):
        assert topic in solution


def test_committed_sample_report_is_complete_and_rendered_from_the_model() -> None:
    sample_root = EXAMPLE_ROOT / "sample_output"
    payload = json.loads((sample_root / "optimization_report.json").read_text(encoding="utf-8"))
    assert {
        "baseline",
        "candidate",
        "delta",
        "gateDecision",
        "failureAttribution",
    } <= payload.keys()

    report = OptimizationReport.model_validate(payload)
    assert report.status == Decision.REJECT
    assert report.stage == "complete"
    assert report.duration_seconds < 180
    assert report.reproducibility.reproducible is True
    assert report.reproducibility.git_dirty is False
    assert report.baseline and report.baseline.train and report.baseline.validation
    assert report.candidate and report.candidate.train and report.candidate.validation
    assert report.delta and report.delta.train and report.delta.validation
    assert report.gate_decision and report.gate_decision.decision == Decision.REJECT
    assert "OVERFIT_TRAIN_UP_VALIDATION_DOWN" in report.gate_decision.reasons
    transitions = {case.transition for case in report.delta.validation.cases}
    assert transitions == {
        Transition.NEW_PASS,
        Transition.NEW_FAIL,
        Transition.UNCHANGED,
    }
    assert report.failure_attribution
    for snapshot in (
            report.failure_attribution.train,
            report.failure_attribution.validation,
    ):
        assert snapshot
        assert snapshot.statistics.total_failures == len(snapshot.failures)
        assert all(failure.reasons and failure.evidence for failure in snapshot.failures)

    markdown = (sample_root / "optimization_report.md").read_text(encoding="utf-8")
    assert markdown == render_markdown(report)
