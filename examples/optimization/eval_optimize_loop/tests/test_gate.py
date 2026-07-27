"""Phase 5 Gate 单元测试"""

import pytest
from src.gate import AcceptanceGate, GateDecision


class TestGateAcceptImproved:
    """场景：候选全面改善 → 应接受"""

    def test_accepts_improved_candidate(
        self, gate_config, sample_baseline_scores, sample_candidate_scores
    ):
        gate = AcceptanceGate(gate_config)
        decision = gate.decide(
            baseline_scores=sample_baseline_scores,
            candidate_scores=sample_candidate_scores,
            baseline_cost=0.10,
            candidate_cost=0.11,
        )
        assert decision.accepted, f"应接受但被拒绝: {decision.reason}"
        assert len(decision.checks) >= 3  # 至少检查 total_score / hard_fail / cost
        assert all(c.passed for c in decision.checks), \
            [f"{c.name}: {c.detail}" for c in decision.failed_checks]


class TestGateRejectRegressed:
    """场景：候选退化 → 应拒绝"""

    def test_rejects_regressed_candidate(
        self, gate_config, sample_baseline_scores, sample_regressed_scores
    ):
        gate = AcceptanceGate(gate_config)
        decision = gate.decide(
            baseline_scores=sample_baseline_scores,
            candidate_scores=sample_regressed_scores,
            baseline_cost=0.10,
            candidate_cost=0.09,
        )
        assert not decision.accepted, "退化候选应被拒绝"
        assert any(not c.passed for c in decision.checks)


class TestGateOverfitDetection:
    """场景：过拟合检测"""

    def test_rejects_overfit(
        self, gate_config
    ):
        """训练集提升 + 验证集退化 → 拒绝"""
        gate = AcceptanceGate(gate_config)
        decision = gate.decide(
            baseline_scores={"v1": 0.80, "v2": 0.75},
            candidate_scores={"v1": 0.72, "v2": 0.70},     # 验证集退化
            baseline_train_scores={"t1": 0.50, "t2": 0.45},
            candidate_train_scores={"t1": 0.80, "t2": 0.75},  # 训练集提升
        )
        assert not decision.accepted, "过拟合应被拒绝"
        overfit_check = next(
            (c for c in decision.checks if c.name == "overfit_detection"), None
        )
        assert overfit_check is not None
        assert not overfit_check.passed

    def test_accepts_no_overfit(
        self, gate_config
    ):
        """训练集和验证集都提升 → 接受"""
        gate = AcceptanceGate(gate_config)
        decision = gate.decide(
            baseline_scores={"v1": 0.70, "v2": 0.65},
            candidate_scores={"v1": 0.85, "v2": 0.80},      # 都提升
            baseline_train_scores={"t1": 0.50},
            candidate_train_scores={"t1": 0.80},             # 都提升
        )
        overfit_check = next(
            (c for c in decision.checks if c.name == "overfit_detection"), None
        )
        assert overfit_check is not None
        assert overfit_check.passed, f"不过拟合应通过: {overfit_check.detail}"


class TestGateCriticalCases:
    """场景：关键 case 不退步"""

    def test_rejects_critical_regression(
        self, gate_config, sample_baseline_scores
    ):
        gate = AcceptanceGate(gate_config)
        # val_001 是关键 case，从 0.95 退化到 0.80
        decision = gate.decide(
            baseline_scores=sample_baseline_scores,
            candidate_scores={"val_001": 0.80, "val_002": 0.90, "val_003": 0.80},
            critical_case_ids=["val_001"],
        )
        critical_check = next(
            (c for c in decision.checks if c.name == "critical_case_no_regress"), None
        )
        assert critical_check is not None
        assert not critical_check.passed


class TestGateCostBudget:
    """场景：成本超预算"""

    def test_rejects_over_budget(self, gate_config, sample_baseline_scores, sample_candidate_scores):
        gate = AcceptanceGate(gate_config)
        decision = gate.decide(
            baseline_scores=sample_baseline_scores,
            candidate_scores=sample_candidate_scores,
            baseline_cost=0.10,
            candidate_cost=0.15,  # 1.5× → 超过 1.2× 阈值
        )
        cost_check = next(
            (c for c in decision.checks if c.name == "cost_within_budget"), None
        )
        assert cost_check is not None
        assert not cost_check.passed


class TestGateEdgeCases:
    """边界场景"""

    def test_empty_scores(self, gate_config):
        gate = AcceptanceGate(gate_config)
        decision = gate.decide(
            baseline_scores={},
            candidate_scores={},
        )
        # 总分提升 0.0 小于阈值 0.03 → 应失败
        total_check = next(
            (c for c in decision.checks if c.name == "total_score_improvement"), None
        )
        assert total_check is not None
        assert not total_check.passed

    def test_majority_strategy(self):
        """majority 策略：多数通过即接受"""
        config = {
            "rules": {
                "total_score_improvement": {"enabled": True, "threshold": 0.03},
                "no_new_hard_fail": {"enabled": True, "max_new_fails": 0},
                "cost_within_budget": {"enabled": True, "max_cost_ratio": 1.2},
            },
            "acceptance_strategy": "majority",
        }
        gate = AcceptanceGate(config)
        # 总分提升不达标（失败），但没有新 hard fail（通过），成本不超标（通过）→ 2/3 → 接受
        decision = gate.decide(
            baseline_scores={"v1": 0.80, "v2": 0.75},
            candidate_scores={"v1": 0.81, "v2": 0.76},  # 仅 +0.01 < 0.03
            baseline_cost=0.10,
            candidate_cost=0.10,
        )
        assert decision.accepted
        assert decision.strategy == "majority"

class TestGateNewHardFailCaseLevel:
    """Verify no_new_hard_fail uses case-level comparison, not net count."""

    def test_rejects_swapped_failures(self, gate_config):
        """Baseline fails on A, candidate fixes A but fails on B -> new fail detected."""
        gate = AcceptanceGate(gate_config)
        # baseline: only case_A fails (0.40 < 0.6), case_B passes (0.90)
        # candidate: case_A fixed (0.85), but case_B now fails (0.35)
        # Net count: 1 fail -> 1 fail, old logic says new_fails=0.
        # Case-level: case_B is a new hard fail (0.35 < 0.6, was 0.90 >= 0.6).
        decision = gate.decide(
            baseline_scores={"case_A": 0.40, "case_B": 0.90},
            candidate_scores={"case_A": 0.85, "case_B": 0.35},
        )
        hard_fail_check = next(
            (c for c in decision.checks if c.name == "no_new_hard_fail"), None
        )
        assert hard_fail_check is not None
        assert not hard_fail_check.passed, (
            f"Swapped failure should be detected as new hard fail: {hard_fail_check.detail}"
        )

    def test_skips_new_case_not_in_baseline(self, gate_config):
        """Cases absent from baseline are skipped for new-fail counting.

        A case that was never evaluated in baseline cannot be a regression.
        Callers expanding the evalset should re-baseline first so new cases
        have valid baseline scores before gate comparison.
        """
        gate = AcceptanceGate(gate_config)
        # baseline: only case_A = 0.90 (pass)
        # candidate: case_A = 0.85 (pass), case_B = 0.35 (hard fail, but new)
        decision = gate.decide(
            baseline_scores={"case_A": 0.90},
            candidate_scores={"case_A": 0.85, "case_B": 0.35},
        )
        hard_fail_check = next(
            (c for c in decision.checks if c.name == "no_new_hard_fail"), None
        )
        assert hard_fail_check is not None
        # case_B is not in baseline -> skipped, not counted as new hard fail
        assert hard_fail_check.passed, (
            f"New case absent from baseline should be skipped: {hard_fail_check.detail}"
        )

    def test_accepts_improved_failures(self, gate_config):
        """Candidate fixes old failures without introducing new ones -> pass."""
        gate = AcceptanceGate(gate_config)
        # baseline: case_A fails (0.40)
        # candidate: case_A improved (0.70), no new failures
        decision = gate.decide(
            baseline_scores={"case_A": 0.40, "case_B": 0.85},
            candidate_scores={"case_A": 0.70, "case_B": 0.90},
        )
        hard_fail_check = next(
            (c for c in decision.checks if c.name == "no_new_hard_fail"), None
        )
        assert hard_fail_check is not None
        assert hard_fail_check.passed, (
            f"Improved failures without new ones should pass: {hard_fail_check.detail}"
        )



    def test_critical_read_failure_rejects(self, gate_config, tmp_path):
        """fail_closed_for_unreadable_evalset forces reject with root cause.

        Exercises the same AcceptanceGate.fail_closed_for_unreadable_evalset
        method that run_pipeline.py calls in production, so the test and
        production code stay in sync.
        """
        from src.gate import AcceptanceGate

        gate = AcceptanceGate(gate_config)

        # Step 1: gate sees empty critical ids → skipped
        decision = gate.decide(
            baseline_scores={"case_A": 0.90},
            candidate_scores={"case_A": 0.85},
            critical_case_ids=[],
        )

        # Step 2: fail-closed override (same call as run_pipeline.py)
        final = AcceptanceGate.fail_closed_for_unreadable_evalset(
            decision, gate.strategy,
            error_detail="FileNotFoundError: val.evalset.json",
        )

        assert final.accepted is False
        critical_check = next(
            (c for c in final.checks if c.name == "critical_case_no_regress"),
            None,
        )
        assert critical_check is not None
        assert critical_check.passed is False
        assert "evalset" in critical_check.detail
        assert "FileNotFoundError" in critical_check.detail

import pytest, subprocess, os, sys
from pathlib import Path

PIPELINE_SCRIPT = Path(__file__).resolve().parent.parent / 'run_pipeline.py'

class TestLockRobustness:
    def _run_with_lock(self, lock_content, tmp_path):
        output_dir = tmp_path / 'output'
        output_dir.mkdir()
        lock_file = output_dir / '.pipeline.lock'
        lock_file.write_text(lock_content, encoding='utf-8')
        result = subprocess.run(
            [sys.executable, str(PIPELINE_SCRIPT), '--output', str(output_dir), '--quiet'],
            capture_output=True, text=True, timeout=30,
            cwd=str(PIPELINE_SCRIPT.parent),
        )
        return result.returncode

    @pytest.mark.skipif(sys.platform != "win32", reason="PID lock semantics; POSIX uses flock (file content irrelevant)")
    def test_empty_lock_cleaned_not_crash(self, tmp_path):
        rc = self._run_with_lock('', tmp_path)
        assert rc == 0, f'Expected exit 0 (corrupt lock cleaned + retry succeeded), got {rc}'
    @pytest.mark.skipif(sys.platform != "win32", reason="PID lock semantics; POSIX uses flock (file content irrelevant)")

    def test_non_numeric_lock_cleaned_not_crash(self, tmp_path):
        rc = self._run_with_lock('not-a-pid', tmp_path)
        assert rc == 0, f'Expected exit 0 (corrupt lock cleaned + retry succeeded), got {rc}'


# ============================================================================
# Lock module smoke test (R24 — ensure new src/lock.py imports cleanly)
# ============================================================================

class TestLockModule:
    def test_import(self):
        from src.lock import acquire_pipeline_lock, release_pipeline_lock
        assert callable(acquire_pipeline_lock)
        assert callable(release_pipeline_lock)

    def test_acquire_release_lifecycle(self, tmp_path):
        from src.lock import acquire_pipeline_lock, release_pipeline_lock
        lock_path = str(tmp_path / '.pipeline.lock')
        token = acquire_pipeline_lock(lock_path)
        assert token is not None, 'Should acquire lock on empty dir'
        release_pipeline_lock(token, lock_path)


class TestPipelineFakeE2E:
    """End-to-end smoke test: run_pipeline.py fake mode exits 0 and produces output files."""

    def test_fake_pipeline_exits_zero(self):
        """run_pipeline.py --quiet exits 0 in fake mode."""
        import subprocess
        result = subprocess.run(
            ["python", str(PIPELINE_SCRIPT), "--quiet"],
            capture_output=True, text=True, timeout=30,
            cwd=str(PIPELINE_SCRIPT.parent),
        )
        assert result.returncode == 0, (
            f"Pipeline failed: stderr={result.stderr[:200]}"
        )

    def test_fake_pipeline_produces_report_json(self):
        """run_pipeline.py produces output/reports/optimization_report.json."""
        import subprocess, json
        result = subprocess.run(
            ["python", str(PIPELINE_SCRIPT), "--quiet"],
            capture_output=True, text=True, timeout=30,
            cwd=str(PIPELINE_SCRIPT.parent),
        )
        assert result.returncode == 0
        report_path = PIPELINE_SCRIPT.parent / "output" / "reports" / "optimization_report.json"
        assert report_path.exists(), f"Missing report: {report_path}"
        with open(report_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        assert "gate_decision" in data
        assert "baseline" in data

    def test_fake_pipeline_produces_audit_dir(self):
        """run_pipeline.py produces output/audit/<timestamp>/ directory."""
        import subprocess
        result = subprocess.run(
            ["python", str(PIPELINE_SCRIPT), "--quiet"],
            capture_output=True, text=True, timeout=30,
            cwd=str(PIPELINE_SCRIPT.parent),
        )
        assert result.returncode == 0
        audit_dir = PIPELINE_SCRIPT.parent / "output" / "audit"
        assert audit_dir.exists()
        subdirs = [d for d in audit_dir.iterdir() if d.is_dir()]
        assert len(subdirs) >= 1, f"No audit subdirectories in {audit_dir}"

    def test_fake_pipeline_rejects_broken_evalset(self):
        """run_pipeline.py with non-existent evalset exits non-zero."""
        import subprocess
        result = subprocess.run(
            ["python", str(PIPELINE_SCRIPT), "--quiet", "--val", "nonexistent.json"],
            capture_output=True, text=True, timeout=30,
            cwd=str(PIPELINE_SCRIPT.parent),
        )
        # Should fail when evalset is missing
        assert result.returncode != 0, (
            f"Expected non-zero exit for broken evalset, got {result.returncode}"
        )
