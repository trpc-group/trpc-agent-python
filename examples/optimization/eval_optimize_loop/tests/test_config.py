"""Tests for pipeline config module — loading and validation."""

import json
import os
import tempfile

import pytest

from pipeline.config import (
    PipelineConfig,
    load_evalset,
    load_optimizer_json,
    load_pipeline_config,
)


class TestLoadEvalset:
    """Tests for evalset JSON loading."""

    def test_load_train_evalset_valid(self, data_dir):
        data = load_evalset(str(data_dir / "train.evalset.json"))
        assert "eval_set_id" in data
        assert "eval_cases" in data
        assert len(data["eval_cases"]) >= 3  # Expanded evalset

    def test_load_val_evalset_valid(self, data_dir):
        data = load_evalset(str(data_dir / "val.evalset.json"))
        assert len(data["eval_cases"]) >= 3  # Expanded evalset
        assert "val" in data["eval_set_id"].lower()

    def test_load_evalset_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_evalset("nonexistent.json")

    def test_load_evalset_missing_eval_cases(self, temp_json_file):
        path = temp_json_file({"eval_set_id": "test"})
        try:
            with pytest.raises(ValueError):
                load_evalset(path)
        finally:
            os.unlink(path)

    def test_load_evalset_missing_eval_set_id(self, temp_json_file):
        path = temp_json_file({"eval_cases": []})
        try:
            with pytest.raises(ValueError):
                load_evalset(path)
        finally:
            os.unlink(path)

    def test_load_evalset_empty_cases(self, temp_json_file):
        path = temp_json_file({"eval_set_id": "empty", "eval_cases": []})
        try:
            data = load_evalset(path)
            assert len(data["eval_cases"]) == 0
        finally:
            os.unlink(path)


class TestLoadOptimizerJson:
    """Tests for optimizer.json loading."""

    def test_load_optimizer_json(self, data_dir):
        data = load_optimizer_json(str(data_dir / "optimizer.json"))
        assert "evaluate" in data
        assert "optimize" in data

    def test_load_optimizer_missing_evaluate(self, temp_json_file):
        path = temp_json_file({"optimize": {"algorithm": {"name": "test"}}})
        try:
            with pytest.raises(ValueError):
                load_optimizer_json(path)
        finally:
            os.unlink(path)

    def test_load_optimizer_missing_optimize(self, temp_json_file):
        path = temp_json_file({"evaluate": {"metrics": []}})
        try:
            with pytest.raises(ValueError):
                load_optimizer_json(path)
        finally:
            os.unlink(path)

    def test_load_optimizer_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_optimizer_json("nonexistent.json")


class TestPipelineConfig:
    """Tests for PipelineConfig defaults and overrides."""

    def test_defaults(self):
        cfg = load_pipeline_config()
        assert cfg.seed == 42
        assert cfg.mode == "fake"
        assert cfg.algorithm == "gepa_reflective"
        assert cfg.max_iterations == 3
        assert cfg.min_improvement_threshold == 0.05
        assert cfg.max_cost_budget == 10.0

    def test_seed_override(self):
        cfg = load_pipeline_config(seed=99)
        assert cfg.seed == 99

    def test_mode_override(self):
        cfg = load_pipeline_config(mode="live")
        assert cfg.mode == "live"

    def test_verbose_override(self):
        cfg = load_pipeline_config(verbose=True)
        assert cfg.verbose is True

    def test_max_iterations_override(self):
        cfg = load_pipeline_config(max_iterations=5)
        assert cfg.max_iterations == 5

    def test_min_improvement_override(self):
        cfg = load_pipeline_config(min_improvement_threshold=0.10)
        assert cfg.min_improvement_threshold == 0.10

    def test_max_cost_override(self):
        cfg = load_pipeline_config(max_cost_budget=20.0)
        assert cfg.max_cost_budget == 20.0

    def test_multiple_overrides(self):
        cfg = load_pipeline_config(
            seed=7, mode="live", max_iterations=10, verbose=True,
        )
        assert cfg.seed == 7
        assert cfg.mode == "live"
        assert cfg.max_iterations == 10
        assert cfg.verbose is True

    def test_ci_mode(self):
        cfg = load_pipeline_config(ci_mode=True)
        assert cfg.ci_mode is True
