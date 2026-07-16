from __future__ import annotations

import json
import os
import tempfile

from ..reporting import write_reports
from ..models import (
    PipelineResult,
    SplitResult,
    SplitDelta,
    PerCaseDelta,
    PerCaseResult,
    FailureAttribution,
    FailureCategory,
)


def test_write_reports_json():
    result = PipelineResult(
        mode="trace",
        gate_decision="ACCEPT",
        gate_reasons=["val improved"],
        baseline={
            "train": SplitResult(
                pass_rate=0.0,
                metric_breakdown={"m1": 0.5},
                per_case={
                    "c1": PerCaseResult(case_id="c1", passed=False, metric_scores={"m1": 0.5}),
                },
            ),
            "val": SplitResult(
                pass_rate=0.0,
                metric_breakdown={"m1": 0.5},
                per_case={
                    "c2": PerCaseResult(case_id="c2", passed=False, metric_scores={"m1": 0.5}),
                },
            ),
        },
        candidate={
            "train": SplitResult(
                pass_rate=1.0,
                metric_breakdown={"m1": 1.0},
                per_case={
                    "c1": PerCaseResult(case_id="c1", passed=True, metric_scores={"m1": 1.0}),
                },
            ),
            "val": SplitResult(
                pass_rate=1.0,
                metric_breakdown={"m1": 1.0},
                per_case={
                    "c2": PerCaseResult(case_id="c2", passed=True, metric_scores={"m1": 1.0}),
                },
            ),
        },
        delta=SplitDelta(
            train=PerCaseDelta(newly_passing=["c1"], newly_failing=[], score_deltas={"c1": {"m1": 0.5}}, unchanged=[]),
            val=PerCaseDelta(newly_passing=["c2"], newly_failing=[], score_deltas={"c2": {"m1": 0.5}}, unchanged=[]),
            train_pass_rate_delta=1.0,
            val_pass_rate_delta=1.0,
        ),
        failure_attribution=FailureAttribution(
            total_cases=3,
            failed_cases=3,
            categories={
                "final_response_mismatch": FailureCategory(count=3, case_ids=["c1", "c2", "c3"]),
            },
        ),
        duration_seconds=1.5,
        seed=42,
        started_at="2026-01-01T00:00:00",
        finished_at="2026-01-01T00:00:02",
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        write_reports(result, tmpdir)

        json_path = os.path.join(tmpdir, "optimization_report.json")
        md_path = os.path.join(tmpdir, "optimization_report.md")

        assert os.path.isfile(json_path), "optimization_report.json not created"
        assert os.path.isfile(md_path), "optimization_report.md not created"

        # Verify JSON structure
        with open(json_path) as f:
            loaded = json.load(f)
            assert loaded["schemaVersion"] == "v1"
            assert loaded["gateDecision"] == "ACCEPT"
            assert loaded["mode"] == "trace"
            assert loaded["overfittingWarning"] is False

        # Verify MD contains key sections
        with open(md_path) as f:
            md_content = f.read()
            assert "# Optimization Report" in md_content
            assert "## Verdict" in md_content
            assert "ACCEPT" in md_content
            assert "## Pass Rates" in md_content
            assert "## Per-Case Delta" in md_content
            assert "## Failure Attribution" in md_content
            assert "## Gate Results" in md_content
            assert "## Overfitting Check" in md_content
            assert "## Audit" in md_content


def test_write_reports_reject():
    result = PipelineResult(
        mode="trace",
        gate_decision="REJECT",
        gate_reasons=["new_fails: val has newly failing cases"],
        overfitting_warning=True,
        duration_seconds=1.0,
        seed=42,
        started_at="2026-01-01T00:00:00",
        finished_at="2026-01-01T00:00:01",
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        write_reports(result, tmpdir)
        md_path = os.path.join(tmpdir, "optimization_report.md")
        with open(md_path) as f:
            md_content = f.read()
            assert "REJECT" in md_content
            assert "overfitting" in md_content.lower()
            assert "newly failing" in md_content.lower()


def test_write_reports_per_case_delta_all_transitions():
    result = PipelineResult(
        mode="trace",
        gate_decision="MIXED",
        gate_reasons=["all 4 transition types covered"],
        baseline={
            "train": SplitResult(
                pass_rate=0.5,
                metric_breakdown={"m1": 0.5},
                per_case={
                    "c_newp": PerCaseResult(case_id="c_newp", passed=False, metric_scores={"m1": 0.0}),
                    "c_newf": PerCaseResult(case_id="c_newf", passed=True, metric_scores={"m1": 1.0}),
                    "c_bothp": PerCaseResult(case_id="c_bothp", passed=True, metric_scores={"m1": 1.0}),
                    "c_bothf": PerCaseResult(case_id="c_bothf", passed=False, metric_scores={"m1": 0.0}),
                },
            ),
        },
        candidate={
            "train": SplitResult(
                pass_rate=0.5,
                metric_breakdown={"m1": 0.5},
                per_case={
                    "c_newp": PerCaseResult(case_id="c_newp", passed=True, metric_scores={"m1": 1.0}),
                    "c_newf": PerCaseResult(case_id="c_newf", passed=False, metric_scores={"m1": 0.0}),
                    "c_bothp": PerCaseResult(case_id="c_bothp", passed=True, metric_scores={"m1": 1.0}),
                    "c_bothf": PerCaseResult(case_id="c_bothf", passed=False, metric_scores={"m1": 0.0}),
                },
            ),
        },
        delta=SplitDelta(
            train=PerCaseDelta(
                newly_passing=["c_newp"],
                newly_failing=["c_newf"],
                score_deltas={"c_newp": {"m1": 1.0}, "c_newf": {"m1": -1.0}},
                unchanged=["c_bothp", "c_bothf"],
            ),
            val=PerCaseDelta(
                newly_passing=[],
                newly_failing=[],
                score_deltas={},
                unchanged=[],
            ),
            train_pass_rate_delta=0.0,
            val_pass_rate_delta=0.0,
        ),
        failure_attribution=FailureAttribution(
            total_cases=4,
            failed_cases=2,
            categories={},
        ),
        duration_seconds=0.5,
        seed=42,
        started_at="2026-01-01T00:00:00",
        finished_at="2026-01-01T00:00:01",
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        write_reports(result, tmpdir)

        md_path = os.path.join(tmpdir, "optimization_report.md")
        assert os.path.isfile(md_path)

        with open(md_path) as f:
            md_content = f.read()

        assert "## Per-Case Delta" in md_content
        assert "newly passing" in md_content
        assert "newly failing" in md_content
        assert "passed (both)" in md_content
        assert "failed (both)" in md_content
        assert "MIXED" in md_content
