"""Unit tests for run_pipeline.py helper functions.

Covers build_reproduce_command() and ci_exit_code() — pure functions
extracted so the CI exit-code contract and the audit reproduce command
are testable without running the full pipeline.
"""

import argparse

from pipeline.gate import GateDecision

from run_pipeline import build_reproduce_command, ci_exit_code


def _ns(**overrides) -> argparse.Namespace:
    """Build a Namespace with argparse defaults, overridable per test."""
    defaults = dict(
        mode="fake",
        seed=42,
        scenario="fix_attributed",
        max_iterations=3,
        min_improvement=0.05,
        max_cost=10.0,
        output_dir="sample_output",
        train_evalset="data/train.evalset.json",
        val_evalset="data/val.evalset.json",
        holdout_evalset="data/holdout.evalset.json",
        optimizer_config="data/optimizer.json",
        val_regression_cases="",
        critical_cases="",
        verbose=False,
        ci=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestBuildReproduceCommand:
    """Tests for build_reproduce_command()."""

    def test_default_run_is_minimal(self):
        cmd = build_reproduce_command(_ns())
        assert cmd == "python run_pipeline.py --mode fake"

    def test_live_mode_always_included(self):
        cmd = build_reproduce_command(_ns(mode="live"))
        assert cmd == "python run_pipeline.py --mode live"

    def test_non_default_seed_appended(self):
        cmd = build_reproduce_command(_ns(seed=99))
        assert "--seed 99" in cmd

    def test_default_seed_omitted(self):
        cmd = build_reproduce_command(_ns())
        assert "--seed" not in cmd

    def test_scenario_appended_when_non_default(self):
        cmd = build_reproduce_command(_ns(scenario="overfit"))
        assert "--scenario overfit" in cmd

    def test_scenario_default_omitted(self):
        cmd = build_reproduce_command(_ns())
        assert "--scenario" not in cmd

    def test_max_iterations_appended_when_non_default(self):
        cmd = build_reproduce_command(_ns(max_iterations=5))
        assert "--max-iterations 5" in cmd

    def test_evalset_overrides_appended(self):
        cmd = build_reproduce_command(_ns(
            train_evalset="data/my_train.json",
            val_evalset="data/my_val.json",
            holdout_evalset="data/my_holdout.json",
            optimizer_config="data/my_opt.json",
        ))
        assert "--train-evalset data/my_train.json" in cmd
        assert "--val-evalset data/my_val.json" in cmd
        assert "--holdout-evalset data/my_holdout.json" in cmd
        assert "--optimizer-config data/my_opt.json" in cmd

    def test_val_regression_cases_appended(self):
        cmd = build_reproduce_command(_ns(val_regression_cases="v1,v2"))
        assert "--val-regression-cases v1,v2" in cmd

    def test_critical_cases_appended(self):
        cmd = build_reproduce_command(_ns(critical_cases="c1,c2"))
        assert "--critical-cases c1,c2" in cmd

    def test_ci_and_verbose_flags_appended(self):
        cmd = build_reproduce_command(_ns(ci=True, verbose=True))
        assert "--ci" in cmd
        assert "--verbose" in cmd

    def test_paths_with_spaces_are_quoted(self):
        # 路径含空格/shell 元字符时，复现命令必须可原样重放
        cmd = build_reproduce_command(_ns(
            output_dir="my results dir",
            train_evalset="data/my train.json",
            val_regression_cases="v1,v2",
        ))
        assert "--output-dir 'my results dir'" in cmd
        assert "--train-evalset 'data/my train.json'" in cmd

    def test_full_non_default_run(self):
        cmd = build_reproduce_command(_ns(
            mode="live", seed=7, scenario="noop", max_iterations=5,
            min_improvement=0.10, max_cost=20.0, output_dir="results",
            ci=True, critical_cases="c1",
        ))
        assert cmd.startswith("python run_pipeline.py --mode live")
        assert all(tok in cmd for tok in (
            "--seed 7", "--scenario noop", "--max-iterations 5",
            "--min-improvement 0.1", "--max-cost 20.0", "--output-dir results",
            "--ci", "--critical-cases c1",
        ))


class TestCiExitCode:
    """Tests for ci_exit_code()."""

    def test_ci_disabled_always_zero(self):
        for decision in (GateDecision.ACCEPT, GateDecision.REJECT, GateDecision.NEEDS_REVIEW):
            assert ci_exit_code(decision, ci_mode=False) == 0

    def test_accept_returns_zero(self):
        assert ci_exit_code(GateDecision.ACCEPT, ci_mode=True) == 0

    def test_reject_returns_one(self):
        assert ci_exit_code(GateDecision.REJECT, ci_mode=True) == 1

    def test_needs_review_returns_two(self):
        assert ci_exit_code(GateDecision.NEEDS_REVIEW, ci_mode=True) == 2
