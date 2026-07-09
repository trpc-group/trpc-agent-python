"""Integration tests for the full pipeline in fake mode."""

import json
import os
import tempfile

import pytest

from pipeline.config import load_evalset, load_pipeline_config
from pipeline.baseline import run_baseline_fake, BaselineResult
from pipeline.attribution import attribute_failures
from pipeline.gate import evaluate_gate, GateDecision
from pipeline.validate import run_validation_fake
from pipeline.report import generate_json_report, generate_md_report
from pipeline.optimize import run_optimize_fake


class TestFullPipelineFakeMode:
    """End-to-end pipeline tests in fake mode."""

    def test_complete_pipeline(self, data_dir):
        cfg = load_pipeline_config(
            train_evalset=str(data_dir / "train.evalset.json"),
            val_evalset=str(data_dir / "val.evalset.json"),
            mode="fake", verbose=False,
        )

        # Stage 1: Config
        train_data = load_evalset(cfg.train_evalset)
        val_data = load_evalset(cfg.val_evalset)
        assert len(train_data["eval_cases"]) >= 3  # Expanded evalset
        assert len(val_data["eval_cases"]) >= 3

        # Stage 2: Baseline
        bl_train = run_baseline_fake(cfg.train_evalset, cfg)
        bl_val = run_baseline_fake(cfg.val_evalset, cfg)
        assert bl_train.total_cases >= 3  # Expanded evalset

        # Stage 3: Attribution
        attr = attribute_failures(bl_train.__dict__, bl_val.__dict__)

        # Stage 4: Optimization
        opt_result = run_optimize_fake(attr, cfg)

        # Stage 5: Validate
        # Simulate candidate improvement
        if attr.total_failures > 0 and opt_result.total_iterations > 0:
            new_pass_rate = min(1.0, bl_train.pass_rate + 0.2)
            new_passes = min(bl_train.total_cases,
                           bl_train.passed_cases + attr.total_failures)
        else:
            new_pass_rate = bl_train.pass_rate
            new_passes = bl_train.passed_cases

        candidate = BaselineResult(
            evalset_id=bl_train.evalset_id,
            pass_rate=new_pass_rate,
            total_cases=bl_train.total_cases,
            passed_cases=new_passes,
            failed_cases=bl_train.total_cases - new_passes,
            failed_case_ids=bl_train.failed_case_ids[attr.total_failures:],
        )
        validation = run_validation_fake(cfg.val_evalset, bl_val, candidate, cfg)

        # Stage 6: Gate
        gate = evaluate_gate(
            baseline_pass_rate=bl_train.pass_rate,
            candidate_pass_rate=candidate.pass_rate,
            baseline_metrics=bl_train.metric_breakdown,
            candidate_metrics=candidate.metric_breakdown,
            baseline_failed=bl_train.failed_case_ids,
            candidate_failed=candidate.failed_case_ids,
            optimization_cost=opt_result.total_cost,
        )

        # Stage 7: Report
        report = generate_json_report(
            "int-test", bl_train, bl_val, attr, gate,
            validation=validation,
            optimization_result={
                "algorithm": opt_result.algorithm,
                "total_iterations": opt_result.total_iterations,
                "converged": opt_result.converged,
            },
        )
        data = json.loads(report)
        assert "task_id" in data

    def test_pipeline_with_overfitting_rejection(self, data_dir):
        cfg = load_pipeline_config(mode="fake")
        bl_train = run_baseline_fake(str(data_dir / "train.evalset.json"), cfg)

        # Simulate big degradation
        gate = evaluate_gate(
            baseline_pass_rate=0.8,
            candidate_pass_rate=0.2,
            baseline_metrics={}, candidate_metrics={},
        )
        assert gate.decision == GateDecision.REJECT

    def test_empty_evalset_no_crash(self, temp_json_file):
        path = temp_json_file({"eval_set_id": "empty", "eval_cases": []})
        try:
            cfg = load_pipeline_config()
            result = run_baseline_fake(path, cfg)
            assert result.total_cases == 0
        finally:
            os.unlink(path)

    def test_single_case_pipeline(self, temp_json_file):
        """Pipeline works with a single eval case."""
        path = temp_json_file({
            "eval_set_id": "single",
            "eval_cases": [
                {"eval_id": "only_case", "conversation": [{"text": "2+2"}]},
            ],
        })
        try:
            cfg = load_pipeline_config()
            result = run_baseline_fake(path, cfg)
            assert result.total_cases == 1
            assert result.passed_cases == 1
        finally:
            os.unlink(path)

    def test_pipeline_with_zero_failures(self, temp_json_file):
        """Full pipeline when baseline has zero failures."""
        path = temp_json_file({
            "eval_set_id": "perfect",
            "eval_cases": [
                {"eval_id": "c1", "conversation": [{"text": "q"}]},
                {"eval_id": "c2", "conversation": [{"text": "q"}]},
            ],
        })
        try:
            cfg = load_pipeline_config(
                train_evalset=path, val_evalset=path, mode="fake",
            )
            bl = run_baseline_fake(path, cfg)
            assert bl.passed_cases == 2

            attr = attribute_failures(bl.__dict__, {})
            opt = run_optimize_fake(attr, cfg)
            # Zero failures → converged, no iterations
            assert opt.converged is True
            assert opt.total_iterations == 0
        finally:
            os.unlink(path)

    def test_report_output_to_file(self, data_dir, tmp_path):
        """Verify reports can be written to disk."""
        cfg = load_pipeline_config(
            train_evalset=str(data_dir / "train.evalset.json"),
            val_evalset=str(data_dir / "val.evalset.json"),
            mode="fake",
        )
        bl_train = run_baseline_fake(cfg.train_evalset, cfg)
        bl_val = run_baseline_fake(cfg.val_evalset, cfg)
        attr = attribute_failures(bl_train.__dict__, bl_val.__dict__)
        gate = evaluate_gate(0.5, 0.85, {}, {}, min_improvement=0.1)

        json_report = generate_json_report("test", bl_train, bl_val, attr, gate)
        md_report = generate_md_report("test", bl_train, bl_val, attr, gate)

        # Write to temp directory
        json_path = tmp_path / "report.json"
        md_path = tmp_path / "report.md"
        json_path.write_text(json_report, encoding="utf-8")
        md_path.write_text(md_report, encoding="utf-8")

        assert json_path.exists()
        assert md_path.exists()
        assert json.loads(json_path.read_text(encoding="utf-8"))

    def test_ci_mode_rejection_exit_code_concept(self, data_dir):
        """Gate REJECT scenario should be detectable for CI mode."""
        cfg = load_pipeline_config(mode="fake")
        bl_train = run_baseline_fake(str(data_dir / "train.evalset.json"), cfg)

        # Simulate a rejection scenario
        gate = evaluate_gate(
            baseline_pass_rate=0.8,
            candidate_pass_rate=0.3,
            baseline_metrics={}, candidate_metrics={},
        )
        # CI mode would exit(1) on this
        assert gate.decision == GateDecision.REJECT
