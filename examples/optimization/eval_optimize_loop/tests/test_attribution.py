"""Tests for failure attribution module."""

import pytest

from pipeline.attribution import (
    AttributionEntry,
    AttributionReport,
    FailureCategory,
    attribute_failures,
    _categorize_failure,
)
from pipeline.baseline import BaselineResult


class TestCategorizeFailure:
    """Tests for _categorize_failure keyword matching."""

    def test_tool_parameter_error(self):
        assert _categorize_failure("tool_call_error: wrong parameter") == FailureCategory.TOOL_PARAMETER_ERROR

    def test_wrong_tool_selected(self):
        assert _categorize_failure("wrong_tool_selected: used add instead of multiply") == FailureCategory.WRONG_TOOL_SELECTED

    def test_tool_call_generic(self):
        assert _categorize_failure("function call failed: timeout") == FailureCategory.TOOL_CALL_ERROR

    def test_final_response_mismatch(self):
        assert _categorize_failure("final_response_mismatch") == FailureCategory.FINAL_RESPONSE_MISMATCH

    def test_llm_rubric_not_met(self):
        assert _categorize_failure("llm_rubric_not_met: quality score below threshold") == FailureCategory.LLM_RUBRIC_NOT_MET

    def test_knowledge_recall(self):
        assert _categorize_failure("knowledge recall failed: retrieval empty") == FailureCategory.KNOWLEDGE_RECALL_INSUFFICIENT

    def test_format_not_required(self):
        assert _categorize_failure("format mismatch: expected pattern not found") == FailureCategory.FORMAT_NOT_AS_REQUIRED

    def test_missing_expected_output(self):
        assert _categorize_failure("missing expected output in response") == FailureCategory.MISSING_EXPECTED_OUTPUT

    def test_response_match(self):
        assert _categorize_failure("output answer did not match expected") == FailureCategory.FINAL_RESPONSE_MISMATCH

    def test_unknown(self):
        assert _categorize_failure("something_weird_happened") == FailureCategory.UNKNOWN

    def test_empty_reason(self):
        assert _categorize_failure("") == FailureCategory.UNKNOWN


class TestAttributionReport:
    """Tests for AttributionReport dataclass."""

    def test_empty_report(self):
        report = AttributionReport()
        assert report.total_failures == 0
        assert report.get_summary() == "No failures to attribute."

    def test_get_summary_with_failures(self):
        report = AttributionReport(
            total_failures=5,
            by_category={
                "tool_call_error": 3,
                "final_response_mismatch": 2,
            },
        )
        summary = report.get_summary()
        assert "5 failures" in summary
        assert "tool_call_error: 3" in summary
        assert "final_response_mismatch: 2" in summary


class TestAttributeFailures:
    """Tests for attribute_failures() function."""

    def test_with_failures(self, sample_baseline):
        report = attribute_failures(sample_baseline.__dict__, {})
        assert report.total_failures == 3
        assert len(report.by_category) >= 2

    def test_no_failures(self, all_pass_baseline):
        report = attribute_failures(all_pass_baseline.__dict__, {})
        assert report.total_failures == 0
        assert len(report.by_category) == 0

    def test_with_val_failures(self, sample_baseline, all_fail_baseline):
        report = attribute_failures(
            sample_baseline.__dict__,
            all_fail_baseline.__dict__,
        )
        # train has 3 failures, val has 4
        assert report.total_failures >= 3

    def test_entries_have_required_fields(self, sample_baseline):
        report = attribute_failures(sample_baseline.__dict__, {})
        for entry in report.entries:
            assert entry.case_id
            assert entry.category
            assert 0 <= entry.confidence <= 1
            assert entry.detail

    def test_all_categories_covered(self, all_fail_baseline):
        """Each failure in all_fail_baseline belongs to a different category."""
        report = attribute_failures(all_fail_baseline.__dict__, {})
        assert report.total_failures == 4
        # Should have 4 distinct categories
        assert len(report.by_category) == 4

    def test_category_counts_sum_to_total(self, sample_baseline):
        report = attribute_failures(sample_baseline.__dict__, {})
        assert sum(report.by_category.values()) == report.total_failures
