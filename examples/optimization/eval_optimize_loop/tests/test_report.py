"""Tests for report generation module."""

import json

import pytest

from pipeline.attribution import attribute_failures
from pipeline.gate import evaluate_gate
from pipeline.report import generate_json_report, generate_md_report


class TestJsonReport:
    """Tests for generate_json_report()."""

    def test_basic_generation(self, sample_baseline):
        attribution = attribute_failures(sample_baseline.__dict__, {})
        gate = evaluate_gate(0.5, 0.85, {}, {}, min_improvement=0.1)
        report_str = generate_json_report(
            "test-001", sample_baseline, sample_baseline, attribution, gate,
        )
        data = json.loads(report_str)
        assert data["task_id"] == "test-001"
        assert data["gate"]["decision"] == "accept"

    def test_contains_all_sections(self, sample_baseline):
        attribution = attribute_failures(sample_baseline.__dict__, {})
        gate = evaluate_gate(0.5, 0.85, {}, {}, min_improvement=0.1)
        report_str = generate_json_report(
            "test-002", sample_baseline, sample_baseline, attribution, gate,
        )
        data = json.loads(report_str)
        for section in [
            "task_id", "generated_at", "baseline", "attribution",
            "gate", "optimizer", "audit",
        ]:
            assert section in data, f"Missing section: {section}"

    def test_reject_report(self, sample_baseline):
        attribution = attribute_failures(sample_baseline.__dict__, {})
        gate = evaluate_gate(0.8, 0.6, {}, {})
        report_str = generate_json_report(
            "test-003", sample_baseline, sample_baseline, attribution, gate,
        )
        data = json.loads(report_str)
        assert data["gate"]["decision"] == "reject"

    def test_audit_fields(self, sample_baseline):
        attribution = attribute_failures(sample_baseline.__dict__, {})
        gate = evaluate_gate(0.5, 0.85, {}, {}, min_improvement=0.1)
        audit = {"seed": 42, "mode": "fake", "duration_seconds": 1.5}
        report_str = generate_json_report(
            "test-004", sample_baseline, sample_baseline, attribution, gate,
            audit=audit,
        )
        data = json.loads(report_str)
        assert data["audit"]["seed"] == 42
        assert data["audit"]["mode"] == "fake"

    def test_optimizer_info(self, sample_baseline):
        attribution = attribute_failures(sample_baseline.__dict__, {})
        gate = evaluate_gate(0.5, 0.85, {}, {}, min_improvement=0.1)
        opt_info = {"algorithm": "gepa_reflective", "total_iterations": 3}
        report_str = generate_json_report(
            "test-005", sample_baseline, sample_baseline, attribution, gate,
            optimization_result=opt_info,
        )
        data = json.loads(report_str)
        assert data["optimizer"]["algorithm"] == "gepa_reflective"

    def test_valid_json_output(self, sample_baseline):
        attribution = attribute_failures(sample_baseline.__dict__, {})
        gate = evaluate_gate(0.5, 0.85, {}, {}, min_improvement=0.1)
        report_str = generate_json_report(
            "test-006", sample_baseline, sample_baseline, attribution, gate,
        )
        # Should be valid JSON
        json.loads(report_str)

    def test_ensure_ascii_false_for_unicode(self, sample_baseline):
        """Report uses ensure_ascii=False to preserve Unicode."""
        attribution = attribute_failures(sample_baseline.__dict__, {})
        gate = evaluate_gate(0.5, 0.85, {}, {}, min_improvement=0.1)
        report_str = generate_json_report(
            "test-中文", sample_baseline, sample_baseline, attribution, gate,
        )
        assert "中文" in report_str  # Not escaped to \uXXXX


class TestMarkdownReport:
    """Tests for generate_md_report()."""

    def test_basic_generation(self, sample_baseline):
        attribution = attribute_failures(sample_baseline.__dict__, {})
        gate = evaluate_gate(0.5, 0.85, {}, {}, min_improvement=0.1)
        md = generate_md_report(
            "test-001", sample_baseline, sample_baseline, attribution, gate,
        )
        assert "test-001" in md
        assert "Gate Decision" in md
        assert "Failure Attribution" in md

    def test_accept_shows_checkmark(self, sample_baseline):
        attribution = attribute_failures(sample_baseline.__dict__, {})
        gate = evaluate_gate(0.5, 0.85, {}, {}, min_improvement=0.1)
        md = generate_md_report(
            "test-002", sample_baseline, sample_baseline, attribution, gate,
        )
        assert "ACCEPT" in md

    def test_reject_formatting(self, sample_baseline):
        attribution = attribute_failures(sample_baseline.__dict__, {})
        gate = evaluate_gate(0.8, 0.6, {}, {})
        md = generate_md_report(
            "test-003", sample_baseline, sample_baseline, attribution, gate,
        )
        assert "REJECT" in md

    def test_contains_recommendations(self, sample_baseline):
        attribution = attribute_failures(sample_baseline.__dict__, {})
        gate = evaluate_gate(0.5, 0.85, {}, {}, min_improvement=0.1)
        md = generate_md_report(
            "test-004", sample_baseline, sample_baseline, attribution, gate,
        )
        assert "Recommendations" in md
