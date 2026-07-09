"""Tests for audit tracing module (tracing.py)."""

import json

import pytest

from pipeline.tracing import (
    AuditTrail,
    AuditTracer,
    StageTiming,
)


class TestStageTiming:
    """Tests for StageTiming dataclass."""

    def test_duration_calculation(self):
        timing = StageTiming(
            stage="baseline",
            start_time=100.0,
            end_time=101.5,
        )
        assert timing.duration_ms == 1500.0
        assert timing.duration_s == 1.5

    def test_zero_duration(self):
        timing = StageTiming(
            stage="config",
            start_time=50.0,
            end_time=50.0,
        )
        assert timing.duration_ms == 0.0


class TestAuditTrail:
    """Tests for AuditTrail dataclass."""

    def test_default_values(self):
        audit = AuditTrail()
        assert audit.seed == 42
        assert audit.mode == "fake"
        assert audit.total_cost_usd == 0.0

    def test_custom_seed(self):
        audit = AuditTrail(seed=123, mode="live")
        assert audit.seed == 123
        assert audit.mode == "live"


class TestAuditTracer:
    """Tests for AuditTracer class."""

    def test_start_and_end_stage(self):
        tracer = AuditTracer(seed=42, mode="fake")
        tracer.start_stage("baseline")
        timing = tracer.end_stage("baseline")
        assert timing.stage == "baseline"
        assert timing.duration_ms >= 0

    def test_multiple_stages(self):
        tracer = AuditTracer(seed=42, mode="fake")
        stages = ["config", "baseline", "attribution", "optimize"]

        for stage in stages:
            tracer.start_stage(stage)
            tracer.end_stage(stage)

        audit = tracer.finalize()
        assert len(audit.stages) == 4
        stage_names = [s.stage for s in audit.stages]
        assert stage_names == stages

    def test_add_cost(self):
        tracer = AuditTracer()
        tracer.add_cost(0.05, "optimization")
        tracer.add_cost(0.10, "evaluation")
        assert tracer.to_dict()["cost"]["total_usd"] == 0.15

    def test_add_error_and_warning(self):
        tracer = AuditTracer()
        tracer.add_error("Something went wrong")
        tracer.add_warning("Proceed with caution")
        audit_dict = tracer.to_dict()
        assert "Something went wrong" in audit_dict["errors"]
        assert "Proceed with caution" in audit_dict["warnings"]

    def test_set_results(self):
        tracer = AuditTracer()
        tracer.set_results(0.5, 0.85, 0.35)
        audit_dict = tracer.to_dict()
        assert audit_dict["results"]["baseline_train_pass_rate"] == 0.5
        assert audit_dict["results"]["candidate_train_pass_rate"] == 0.85
        assert audit_dict["results"]["improvement"] == 0.35

    def test_to_dict_is_valid_json(self):
        tracer = AuditTracer(seed=42, mode="fake", algorithm="gepa_reflective")
        tracer.start_stage("baseline")
        tracer.end_stage("baseline")
        tracer.add_cost(0.05)
        tracer.set_results(0.5, 0.8, 0.3)

        audit_dict = tracer.to_dict()
        # Should be JSON serializable
        json_str = json.dumps(audit_dict, indent=2)
        data = json.loads(json_str)
        assert data["reproducibility"]["seed"] == 42
        assert data["reproducibility"]["mode"] == "fake"

    def test_reproduce_command(self):
        tracer = AuditTracer(seed=42, mode="fake")
        audit = tracer.finalize()
        assert "run_pipeline.py" in audit.reproduce_command
        assert "--mode fake" in audit.reproduce_command

    def test_seed_in_reproduce_command(self):
        tracer = AuditTracer(seed=99, mode="fake")
        audit = tracer.finalize()
        assert "--seed 99" in audit.reproduce_command

    def test_default_seed_omitted_from_command(self):
        tracer = AuditTracer(seed=42, mode="fake")
        audit = tracer.finalize()
        # Default seed 42 should be omitted from reproduce command
        assert "--seed 42" not in audit.reproduce_command

    def test_to_dict_structure(self):
        tracer = AuditTracer(seed=42, mode="fake")
        tracer.start_stage("baseline")
        tracer.end_stage("baseline")
        audit_dict = tracer.to_dict()

        required_top_keys = [
            "reproducibility", "timing", "cost",
            "environment", "results", "errors", "warnings",
        ]
        for key in required_top_keys:
            assert key in audit_dict, f"Missing key: {key}"
