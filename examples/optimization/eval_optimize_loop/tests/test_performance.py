"""Tests for timing and performance characteristics of the pipeline.

All thresholds are intentionally loose to accommodate CI variability,
slow hardware, and environments with resource contention. These tests
primarily guard against regressions like infinite loops or accidentally
quadratic behavior.
"""

import json
import os
import tempfile
import time

import pytest

from pipeline.attribution import AttributionReport, attribute_failures
from pipeline.baseline import BaselineResult, run_baseline_fake
from pipeline.config import (PipelineConfig, load_evalset, load_optimizer_json,
                             load_pipeline_config)
from pipeline.gate import GateDecision, GateResult, evaluate_gate
from pipeline.optimize import OptimizeResult, run_optimize_fake
from pipeline.report import generate_json_report, generate_md_report
from pipeline.validate import run_validation_fake, ValidationResult


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_case(eval_id: str, has_conversation: bool = True) -> dict:
    """Build a minimal evalset case dict."""
    case = {"eval_id": eval_id, "eval_mode": "trace"}
    if has_conversation:
        case["conversation"] = [
            {
                "invocation_id": "inv-1",
                "user_content": {"parts": [{"text": "q"}], "role": "user"},
                "final_response": {"parts": [{"text": "a"}], "role": "model"},
            }
        ]
    return case


def _make_evalset(cases: list[dict], evalset_id: str = "perf-set") -> str:
    """Write a temporary evalset and return path."""
    data = {"eval_set_id": evalset_id, "name": "PerfTest", "eval_cases": cases}
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(data, f)
        return f.name


def _bulk_cases(count: int) -> list[dict]:
    """Generate `count` cases, alternating pass/fail (has_conversation)."""
    return [_make_case(f"case_{i:04d}", i % 2 == 0) for i in range(count)]


def _bulk_failure_baseline(count: int) -> BaselineResult:
    """Generate a BaselineResult with `count` failures."""
    return BaselineResult(
        evalset_id="bulk-fail",
        total_cases=count,
        passed_cases=0,
        failed_cases=count,
        failed_case_ids=[f"f_{i}" for i in range(count)],
        metric_breakdown={"overall_pass_rate": 0.0},
        per_case_results=[
            {"eval_id": f"f_{i}", "pass": False,
             "reason": "tool_call_error: param missing" if i % 3 == 0
             else "final_response_mismatch" if i % 3 == 1
             else "format_not_as_required: bad schema"}
            for i in range(count)
        ],
    )


# ---------------------------------------------------------------------------
# TestFakePipelineSixCases
# ---------------------------------------------------------------------------

class TestFakePipelineSixCases:
    """Fake-mode pipeline with 6 cases completes within a reasonable time."""

    # Maximum wall-clock seconds for a 6-case fake pipeline end-to-end.
    # Fake mode does no real inference — this is extremely generous.
    MAX_SIX_CASE_SECONDS = 10.0

    def test_six_case_baseline_timing(self, pipeline_config):
        """6-case baseline runs well under the threshold."""
        cases = _bulk_cases(6)
        path = _make_evalset(cases)
        try:
            start = time.monotonic()
            result = run_baseline_fake(path, pipeline_config)
            elapsed = time.monotonic() - start
            assert result.total_cases == 6
            assert elapsed < self.MAX_SIX_CASE_SECONDS, (
                f"6-case baseline took {elapsed:.2f}s, "
                f"threshold={self.MAX_SIX_CASE_SECONDS}s"
            )
        finally:
            os.unlink(path)

    def test_six_case_e2e_pipeline_timing(self):
        """Full 6-case pipeline: baseline → attribution → optimize → gate → report."""
        cases = _bulk_cases(6)
        path = _make_evalset(cases)
        try:
            start = time.monotonic()

            # Stage 1: baseline
            cfg = PipelineConfig(seed=42, max_iterations=3)
            bl = run_baseline_fake(path, cfg)

            # Stage 2: attribution
            attr = attribute_failures(bl.__dict__, {})

            # Stage 3: optimize
            opt = run_optimize_fake(attr, cfg)

            # Stage 4: gate
            candidate_pass_rate = min(1.0, bl.pass_rate + 0.1)
            gate = evaluate_gate(
                baseline_pass_rate=bl.pass_rate,
                candidate_pass_rate=candidate_pass_rate,
                baseline_metrics={},
                candidate_metrics={},
                min_improvement=0.05,
            )

            # Stage 5: report (JSON + MD)
            json_rpt = generate_json_report(
                task_id="perf-e2e", baseline_train=bl, baseline_val=bl,
                attribution=attr, gate=gate,
            )
            md_rpt = generate_md_report(
                task_id="perf-e2e", baseline_train=bl, baseline_val=bl,
                attribution=attr, gate=gate,
            )

            elapsed = time.monotonic() - start
            assert json_rpt  # non-empty
            assert md_rpt   # non-empty
            assert elapsed < self.MAX_SIX_CASE_SECONDS, (
                f"6-case E2E pipeline took {elapsed:.2f}s, "
                f"threshold={self.MAX_SIX_CASE_SECONDS}s"
            )
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# TestLargeEvalsetLoading
# ---------------------------------------------------------------------------

class TestLargeEvalsetLoading:
    """50-case evalset loads within a reasonable time."""

    MAX_FIFTY_CASE_SECONDS = 5.0

    def test_fifty_case_evalset_loads_fast(self):
        """Loading a 50-case evalset with load_evalset is fast."""
        cases = _bulk_cases(50)
        path = _make_evalset(cases)
        try:
            start = time.monotonic()
            data = load_evalset(path)
            elapsed = time.monotonic() - start
            assert len(data["eval_cases"]) == 50
            assert elapsed < self.MAX_FIFTY_CASE_SECONDS, (
                f"50-case evalset load took {elapsed:.2f}s, "
                f"threshold={self.MAX_FIFTY_CASE_SECONDS}s"
            )
        finally:
            os.unlink(path)

    def test_fifty_case_baseline_timing(self, pipeline_config):
        """50-case baseline run completes quickly."""
        cases = _bulk_cases(50)
        path = _make_evalset(cases)
        try:
            start = time.monotonic()
            result = run_baseline_fake(path, pipeline_config)
            elapsed = time.monotonic() - start
            assert result.total_cases == 50
            assert elapsed < self.MAX_FIFTY_CASE_SECONDS, (
                f"50-case baseline took {elapsed:.2f}s, "
                f"threshold={self.MAX_FIFTY_CASE_SECONDS}s"
            )
        finally:
            os.unlink(path)

    def test_load_evalset_handles_fifty_cases_without_error(self):
        """50-case load_evalset returns valid structured data."""
        cases = _bulk_cases(50)
        path = _make_evalset(cases)
        try:
            data = load_evalset(path)
            assert data["eval_set_id"] == "perf-set"
            assert len(data["eval_cases"]) == 50
            # Verify each case has expected fields
            for case in data["eval_cases"]:
                assert "eval_id" in case
                assert "eval_mode" in case
                # conversation is optional (missing on fail cases)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# TestFailureAttributionPerformance
# ---------------------------------------------------------------------------

class TestFailureAttributionPerformance:
    """30-failure attribution completes within a reasonable time."""

    MAX_THIRTY_ATTRIBUTION_SECONDS = 5.0

    def test_thirty_failures_attribution_timing(self):
        """Attribute 30 failures — should be well under threshold."""
        baseline = _bulk_failure_baseline(30)
        start = time.monotonic()
        report = attribute_failures(baseline.__dict__, {})
        elapsed = time.monotonic() - start
        assert report.total_failures == 30
        assert len(report.entries) == 30
        assert elapsed < self.MAX_THIRTY_ATTRIBUTION_SECONDS, (
            f"30-failure attribution took {elapsed:.2f}s, "
            f"threshold={self.MAX_THIRTY_ATTRIBUTION_SECONDS}s"
        )

    def test_category_counts_sum_correctly_with_thirty(self):
        """30 failures: category count sum equals total_failures."""
        baseline = _bulk_failure_baseline(30)
        report = attribute_failures(baseline.__dict__, {})
        assert sum(report.by_category.values()) == 30

    def test_each_entry_has_confidence(self):
        """Every attribution entry has a confidence value between 0 and 1."""
        baseline = _bulk_failure_baseline(30)
        report = attribute_failures(baseline.__dict__, {})
        for entry in report.entries:
            assert 0.0 <= entry.confidence <= 1.0
            assert entry.case_id
            assert entry.detail


# ---------------------------------------------------------------------------
# TestReportGenerationPerformance
# ---------------------------------------------------------------------------

class TestReportGenerationPerformance:
    """JSON + MD report generation completes quickly."""

    MAX_REPORT_SECONDS = 2.0

    @pytest.fixture
    def report_inputs(self, sample_baseline, all_pass_baseline):
        """Common fixtures needed by report generation."""
        attr = attribute_failures(sample_baseline.__dict__, {})
        gate = GateResult(
            decision=GateDecision.ACCEPT,
            reason="All checks passed",
            details={
                "improvement": 0.35,
                "checks": [
                    {"check": "improvement_threshold", "passed": True,
                     "detail": "Improvement: +35% (threshold: 5%)"},
                    {"check": "critical_cases", "passed": True,
                     "detail": "No critical cases regressed"},
                    {"check": "cost_budget", "passed": True,
                     "detail": "Cost: $0.15 / $10.00"},
                ],
            },
        )
        return {
            "task_id": "perf-report-001",
            "baseline_train": sample_baseline,
            "baseline_val": all_pass_baseline,
            "attribution": attr,
            "gate": gate,
        }

    def test_json_report_generation_timing(self, report_inputs):
        """JSON report generation is fast."""
        start = time.monotonic()
        rpt = generate_json_report(**report_inputs)
        elapsed = time.monotonic() - start
        assert rpt
        assert len(rpt) > 100
        assert elapsed < self.MAX_REPORT_SECONDS, (
            f"JSON report generation took {elapsed:.2f}s, "
            f"threshold={self.MAX_REPORT_SECONDS}s"
        )

    def test_md_report_generation_timing(self, report_inputs):
        """Markdown report generation is fast."""
        start = time.monotonic()
        rpt = generate_md_report(**report_inputs)
        elapsed = time.monotonic() - start
        assert rpt
        assert len(rpt) > 100
        assert elapsed < self.MAX_REPORT_SECONDS, (
            f"MD report generation took {elapsed:.2f}s, "
            f"threshold={self.MAX_REPORT_SECONDS}s"
        )

    def test_both_reports_generated_together_timing(self, report_inputs):
        """Generating both JSON + MD together is fast."""
        start = time.monotonic()
        json_rpt = generate_json_report(**report_inputs)
        md_rpt = generate_md_report(**report_inputs)
        elapsed = time.monotonic() - start
        assert json_rpt
        assert md_rpt
        # Combined should still be fast
        assert elapsed < self.MAX_REPORT_SECONDS * 1.5, (
            f"Both reports generation took {elapsed:.2f}s, "
            f"threshold={self.MAX_REPORT_SECONDS * 1.5}s"
        )

    def test_json_report_is_valid_json(self, report_inputs):
        """Generated JSON report parses as valid JSON."""
        rpt = generate_json_report(**report_inputs)
        parsed = json.loads(rpt)
        assert parsed["task_id"] == "perf-report-001"
        assert parsed["gate"]["decision"] == "accept"
        assert "baseline" in parsed
        assert "attribution" in parsed


# ---------------------------------------------------------------------------
# TestLargeScaleFakePipeline
# ---------------------------------------------------------------------------

class TestLargeScaleFakePipeline:
    """100-case end-to-end fake pipeline benchmark."""

    MAX_HUNDRED_E2E_SECONDS = 15.0

    def test_hundred_case_e2e_pipeline(self):
        """Full pipeline with 100 cases: baseline → attr → opt → gate → report."""
        cases = _bulk_cases(100)
        path = _make_evalset(cases)
        try:
            start = time.monotonic()

            cfg = PipelineConfig(seed=42, max_iterations=3)
            bl = run_baseline_fake(path, cfg)

            attr = attribute_failures(bl.__dict__, {})

            opt = run_optimize_fake(attr, cfg)

            candidate_pr = min(1.0, bl.pass_rate + 0.1)
            gate = evaluate_gate(
                baseline_pass_rate=bl.pass_rate,
                candidate_pass_rate=candidate_pr,
                baseline_metrics={},
                candidate_metrics={},
                min_improvement=0.05,
            )

            json_rpt = generate_json_report(
                task_id="perf-100", baseline_train=bl, baseline_val=bl,
                attribution=attr, gate=gate,
            )
            md_rpt = generate_md_report(
                task_id="perf-100", baseline_train=bl, baseline_val=bl,
                attribution=attr, gate=gate,
            )

            elapsed = time.monotonic() - start

            assert bl.total_cases == 100
            assert json_rpt
            assert md_rpt
            assert elapsed < self.MAX_HUNDRED_E2E_SECONDS, (
                f"100-case E2E pipeline took {elapsed:.2f}s, "
                f"threshold={self.MAX_HUNDRED_E2E_SECONDS}s"
            )
        finally:
            os.unlink(path)

    def test_hundred_case_baseline_is_linear(self, pipeline_config):
        """100 cases should scale roughly linearly (no quadratic blowup)."""
        cases_50 = _bulk_cases(50)
        path_50 = _make_evalset(cases_50, "perf-50")
        cases_100 = _bulk_cases(100)
        path_100 = _make_evalset(cases_100, "perf-100")
        try:
            start = time.monotonic()
            run_baseline_fake(path_50, pipeline_config)
            t50 = time.monotonic() - start

            start = time.monotonic()
            run_baseline_fake(path_100, pipeline_config)
            t100 = time.monotonic() - start

            # 100 cases at 2x the data, allow generous ratio for fast ops
            assert t100 < max(t50 * 20.0, 0.5), (
                f"100-case ({t100:.3f}s) vs 50-case ({t50:.3f}s) "
                f"— ratio {t100 / max(t50, 0.001):.1f}x"
            )
        finally:
            os.unlink(path_50)
            os.unlink(path_100)
