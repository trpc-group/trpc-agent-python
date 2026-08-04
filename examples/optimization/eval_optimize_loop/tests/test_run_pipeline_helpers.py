"""Unit tests for run_pipeline.py helper functions.

Covers build_reproduce_command() and ci_exit_code() — pure functions
extracted so the CI exit-code contract and the audit reproduce command
are testable without running the full pipeline.
"""

import argparse
import os

from pipeline.gate import GateDecision, GateResult

from run_pipeline import (
    build_reproduce_command,
    ci_exit_code,
    is_output_dir_allowed,
    live_gate_downgrade,
    live_gate_exit_code,
)


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


class TestIsOutputDirAllowed:
    """Tests for is_output_dir_allowed() — 拒绝越界/外部绝对路径。"""

    def _repo_root(self) -> str:
        import os
        _here = os.path.dirname(os.path.abspath(__file__))  # eval_optimize_loop/tests
        # tests → eval_optimize_loop → optimization → examples → 仓库根（4 级）
        return os.path.realpath(os.path.join(_here, os.pardir, os.pardir, os.pardir, os.pardir))

    def test_accepts_repo_internal(self):
        assert is_output_dir_allowed(os.path.join(self._repo_root(), "results")) is True
        assert is_output_dir_allowed("sample_output") is True

    def test_rejects_absolute_path_outside_repo(self):
        assert is_output_dir_allowed("/tmp/pr139_out") is False

    def test_rejects_repo_root_itself(self):
        # 严格子目录：不允许直接写到仓库根（污染根目录）
        assert is_output_dir_allowed(self._repo_root()) is False

    def test_rejects_path_escape(self):
        import os
        outside = os.path.join(os.path.dirname(self._repo_root()), "escaped")
        assert is_output_dir_allowed(outside) is False

    def test_rejects_traversal(self):
        assert is_output_dir_allowed("../../../../../../etc") is False


class TestLiveGateDowngrade:
    """Tests for live_gate_downgrade() — live 下不可比评分驱动的决策降级。"""

    def _gate(self, decision, checks=None):
        return GateResult(decision=decision, reason="r",
                          details={"checks": checks if checks is not None else []})

    def test_fake_mode_unchanged(self):
        g = self._gate(GateDecision.ACCEPT)
        out = live_gate_downgrade(g, live=False)
        assert out.decision == GateDecision.ACCEPT

    def test_live_accept_downgraded(self):
        g = self._gate(GateDecision.ACCEPT)
        out = live_gate_downgrade(g, live=True)
        assert out.decision == GateDecision.NEEDS_REVIEW

    def test_live_reject_downgraded(self):
        g = self._gate(GateDecision.REJECT)
        out = live_gate_downgrade(g, live=True)
        assert out.decision == GateDecision.NEEDS_REVIEW

    def test_live_cost_reject_kept_with_real_check(self):
        # 真实 evaluate_gate 结构：cost_budget check passed=False = 成本超预算，
        # 是真实约束、与评分口径无关 → live 下保留 REJECT
        g = self._gate(GateDecision.REJECT,
                       checks=[{"check": "cost_budget", "passed": False}])
        out = live_gate_downgrade(g, live=True)
        assert out.decision == GateDecision.REJECT

    def test_live_reject_within_budget_downgraded(self):
        # 成本未超（cost_budget passed=True）→ 纯评分驱动 REJECT，降级
        g = self._gate(GateDecision.REJECT,
                       checks=[{"check": "cost_budget", "passed": True}])
        out = live_gate_downgrade(g, live=True)
        assert out.decision == GateDecision.NEEDS_REVIEW

    def test_live_overfit_reject_within_budget_downgraded(self):
        # 过拟合 REJECT：成本未超（checks 里 cost_budget passed=True）、
        # details 无 budget 键 → 评分驱动，live 下降级
        g = self._gate(GateDecision.REJECT,
                       checks=[{"check": "overfitting", "passed": False},
                               {"check": "cost_budget", "passed": True}])
        out = live_gate_downgrade(g, live=True)
        assert out.decision == GateDecision.NEEDS_REVIEW

    def test_live_scenario_config_error_reject_kept(self):
        # 场景配置错误是真实配置问题（非评分口径）→ live 下也保留 REJECT 与根因
        g = GateResult(
            decision=GateDecision.REJECT,
            reason="Validation scenario configuration error: empty val set",
            details={"reason_code": "scenario_config_error"},
        )
        out = live_gate_downgrade(g, live=True)
        assert out.decision == GateDecision.REJECT


class TestLiveGateExitCode:
    """Tests for live_gate_exit_code() — live 降级后 critical/overfit 不豁免非零退出码。"""

    def _gate(self, decision, details=None):
        return GateResult(decision=decision, reason="r",
                          details=details if details is not None else {})

    def test_live_needs_review_without_hard_signal_exits_zero(self):
        # 无 critical/overfit 信号：informational，exit 0
        g = self._gate(GateDecision.NEEDS_REVIEW,
                       details={"checks": [{"check": "improvement", "passed": False}]})
        assert live_gate_exit_code(g, ci_mode=True) == 0

    def test_live_needs_review_critical_regressed_kept(self):
        # 关键 case 回归是硬性失败信号：--ci 下保留非零（2）
        g = self._gate(GateDecision.NEEDS_REVIEW,
                       details={"critical_regressed": ["case_001"], "checks": []})
        assert live_gate_exit_code(g, ci_mode=True) == 2

    def test_live_needs_review_overfit_kept(self):
        # 过拟合（validation_new_failures>0）同样保留非零
        g = self._gate(GateDecision.NEEDS_REVIEW,
                       details={"validation_new_failures": 2, "checks": []})
        assert live_gate_exit_code(g, ci_mode=True) == 2

    def test_live_needs_review_hard_signal_non_ci_zero(self):
        # 非 CI 模式：无论信号如何都 exit 0（不阻断交互式运行）
        g = self._gate(GateDecision.NEEDS_REVIEW,
                       details={"critical_regressed": ["case_001"]})
        assert live_gate_exit_code(g, ci_mode=False) == 0

    def test_live_accept_unchanged(self):
        g = self._gate(GateDecision.ACCEPT)
        assert live_gate_exit_code(g, ci_mode=True) == 0

    def test_live_reject_unchanged(self):
        # 未降级的 REJECT（成本超预算等真实约束）走标准 ci_exit_code
        g = self._gate(GateDecision.REJECT, details={"budget": 5.0, "cost": 15.0})
        assert live_gate_exit_code(g, ci_mode=True) == 1


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


class TestFindRepoRoot:
    """Tests for pipeline._paths.find_repo_root() — 标记文件锚定仓库根。

    reviewer Warning③：repo-root 定位依赖硬编码目录层级，example 目录被移动
    或嵌套层级变化时会把 sys.path 指向错误路径。改为向上查找 pyproject.toml
    或 .git 标记文件锚定。
    """

    def test_finds_repo_root_by_pyproject(self, tmp_path):
        from pipeline import _paths
        root = tmp_path / "repo"
        (root / "pipeline").mkdir(parents=True)
        (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        assert _paths.find_repo_root(str(root / "pipeline")) == str(root)

    def test_finds_repo_root_by_git_dir(self, tmp_path):
        from pipeline import _paths
        root = tmp_path / "repo"
        (root / "src").mkdir(parents=True)
        (root / ".git").mkdir()
        assert _paths.find_repo_root(str(root / "src")) == str(root)

    def test_prefers_innermost_marker(self, tmp_path):
        # 嵌套多个标记文件：应返回离 start_dir 最近的（最内层）目录
        from pipeline import _paths
        outer = tmp_path / "outer"
        (outer / "inner").mkdir(parents=True)
        (outer / "pyproject.toml").write_text("", encoding="utf-8")
        (outer / "inner" / "pyproject.toml").write_text("", encoding="utf-8")
        assert _paths.find_repo_root(str(outer / "inner")) == str(outer / "inner")

    def test_returns_none_when_no_marker(self, tmp_path):
        from pipeline import _paths
        d = tmp_path / "no_marker"
        d.mkdir()
        assert _paths.find_repo_root(str(d)) is None
