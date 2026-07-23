"""Phase 4 Validator ????"""

import json
import asyncio
from pathlib import Path

import pytest
import pytest_asyncio
from src.baseline import BaselineRunner, BaselineResult, BaselineCaseResult
from src.attribution import AttributionRunner
from src.optimizer import FakeOptimizer, OptimizationResult, PromptCandidate
from src.validator import (
    ValidationRunner,
    ValidationResult,
    DeltaCase,
    ValidationSummary,
    run_validation,
    CANDIDATE_PREDICTIONS,
    REGRESSION_PREDICTIONS,
)


# ?? Fixtures ????????????????????????????????????????????

@pytest_asyncio.fixture
async def val_baseline():
    """Fake mode val baseline?"""
    br = BaselineRunner(mode="fake")
    return await br.run_split(
        Path(__file__).parent.parent / "config" / "val.evalset.json", "val"
    )


@pytest_asyncio.fixture
async def full_pipeline():
    """?? fake pipeline: baseline ? attribution ? optimizer?"""
    base = Path(__file__).parent.parent / "config"
    br = BaselineRunner(mode="fake")
    results = await br.run(
        base / "train.evalset.json", base / "val.evalset.json",
    )
    ar = AttributionRunner()
    attr = ar.run(results["train"], results["val"])
    opt = FakeOptimizer()
    opt_result = opt.optimize(attr)
    return results["val"], opt_result


# ?? ?????? ????????????????????????????????????????

class TestDeltaCase:
    def test_improved_status(self):
        d = DeltaCase(
            case_id="v1", ground_truth="A",
            baseline_predicted="B", baseline_score=0.4, baseline_passed=False,
            candidate_predicted="A", candidate_score=0.9, candidate_passed=True,
            score_delta=0.5, status="improved", char_delta=1,
        )
        assert d.status == "improved"
        assert d.score_delta > 0

    def test_regressed_status(self):
        d = DeltaCase(
            case_id="v1", ground_truth="A",
            baseline_predicted="A", baseline_score=0.9, baseline_passed=True,
            candidate_predicted="B", candidate_score=0.4, candidate_passed=False,
            score_delta=-0.5, status="regressed", char_delta=-1,
        )
        assert d.status == "regressed"

    def test_to_dict(self):
        d = DeltaCase(
            case_id="v1", ground_truth="A",
            baseline_predicted="A", baseline_score=1.0, baseline_passed=True,
            candidate_predicted="A", candidate_score=1.0, candidate_passed=True,
            score_delta=0.0, status="unchanged",
            baseline_judge={"recognition": 1.0}, candidate_judge={"recognition": 1.0},
        )
        dd = d.to_dict()
        assert dd["case_id"] == "v1"
        assert dd["status"] == "unchanged"
        assert dd["baseline_judge"]["recognition"] == 1.0


class TestValidationResult:
    def test_score_map(self):
        result = ValidationResult(
            candidate_id="c1",
            delta_cases=[
                DeltaCase(case_id="a", ground_truth="", baseline_predicted="", baseline_score=0.5, baseline_passed=False, candidate_predicted="", candidate_score=0.8, candidate_passed=True, score_delta=0.3, status="improved"),
                DeltaCase(case_id="b", ground_truth="", baseline_predicted="", baseline_score=0.9, baseline_passed=True, candidate_predicted="", candidate_score=0.91, candidate_passed=True, score_delta=0.01, status="improved"),
            ],
        )
        sm = result.score_map
        assert sm["a"] == 0.8
        assert sm["b"] == 0.91

    def test_new_failures(self):
        result = ValidationResult(
            delta_cases=[
                DeltaCase(case_id="pass_to_fail", ground_truth="", baseline_predicted="", baseline_score=0.9, baseline_passed=True, candidate_predicted="", candidate_score=0.4, candidate_passed=False, score_delta=-0.5, status="regressed"),
                DeltaCase(case_id="fail_to_pass", ground_truth="", baseline_predicted="", baseline_score=0.4, baseline_passed=False, candidate_predicted="", candidate_score=0.9, candidate_passed=True, score_delta=0.5, status="improved"),
            ],
        )
        nf = result.new_failures
        assert len(nf) == 1
        assert nf[0].case_id == "pass_to_fail"


# ?? ValidationRunner Fake ?? ?????????????????????????

class TestValidationRunnerFake:
    def test_run_returns_result(self, full_pipeline):
        val_bl, opt_result = full_pipeline
        runner = ValidationRunner(mode="fake")
        result = runner.run(val_bl, opt_result)
        assert isinstance(result, ValidationResult)
        assert result.candidate_id == opt_result.latest_candidate.candidate_id
        assert len(result.delta_cases) == 3

    def test_summary_has_improvement(self, full_pipeline):
        """?????????????"""
        val_bl, opt_result = full_pipeline
        runner = ValidationRunner(mode="fake")
        result = runner.run(val_bl, opt_result)
        assert result.summary.improved >= 1
        assert result.summary.avg_score_delta > 0

    def test_val_001_critical_unchanged(self, full_pipeline):
        """?? case val_001 ?????"""
        val_bl, opt_result = full_pipeline
        runner = ValidationRunner(mode="fake")
        result = runner.run(val_bl, opt_result)
        d = next(c for c in result.delta_cases if c.case_id == "val_001")
        assert d.status in ("improved", "unchanged")
        assert not (d.baseline_passed and not d.candidate_passed)

    def test_val_002_improved(self, full_pipeline):
        """val_002 ???????"""
        val_bl, opt_result = full_pipeline
        runner = ValidationRunner(mode="fake")
        result = runner.run(val_bl, opt_result)
        d = next(c for c in result.delta_cases if c.case_id == "val_002")
        assert d.status == "improved" or d.score_delta > 0

    def test_regression_mode(self, full_pipeline):
        """????????? case ????????"""
        val_bl, opt_result = full_pipeline
        runner = ValidationRunner(mode="fake")
        result = runner.run(val_bl, opt_result, simulate_regression=True)
        v1 = next(c for c in result.delta_cases if c.case_id == "val_001")
        assert v1.status == "regressed", f"val_001 should regress in regression mode, got {v1.status}"
        assert result.summary.regressed >= 1

    def test_serializable(self, full_pipeline):
        val_bl, opt_result = full_pipeline
        runner = ValidationRunner(mode="fake")
        result = runner.run(val_bl, opt_result)
        j = json.dumps(result.to_dict(), ensure_ascii=False)
        parsed = json.loads(j)
        assert parsed["candidate_id"]
        assert len(parsed["delta_cases"]) == 3

    def test_no_candidate_returns_empty(self):
        """??? prompt ???????"""
        runner = ValidationRunner(mode="fake")
        result = runner.run(
            BaselineResult(dataset_name="val"),
            OptimizationResult(candidates=[]),
        )
        assert result.candidate_id == "none"
        assert len(result.delta_cases) == 0

    def test_optimization_target_set(self, full_pipeline):
        val_bl, opt_result = full_pipeline
        runner = ValidationRunner(mode="fake")
        result = runner.run(val_bl, opt_result)
        assert "system_prompt" in result.optimization_target
        assert "final_answer_mismatch" in result.optimization_target


class TestValidationRunnerModes:
    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Unknown mode"):
            ValidationRunner(mode="production")

    def test_real_mode_not_implemented(self, full_pipeline):
        val_bl, opt_result = full_pipeline
        runner = ValidationRunner(mode="real")
        with pytest.raises((NotImplementedError, ImportError)):
            runner.run(val_bl, opt_result)


# ?? ?????? ????????????????????????????????????????

class TestConvenienceFunction:
    def test_run_validation(self, full_pipeline):
        val_bl, opt_result = full_pipeline
        result = run_validation(val_bl, opt_result, mode="fake")
        assert isinstance(result, ValidationResult)


# ?? ??????? ??????????????????????????????????????

class TestPredictionMaps:
    def test_all_categories_have_val_cases(self):
        for cat in ["final_answer_mismatch", "knowledge_recall_insufficient",
                     "tool_call_error", "param_error", "llm_rubric_fail", "format_invalid"]:
            assert cat in CANDIDATE_PREDICTIONS, f"missing {cat}"
            pmap = CANDIDATE_PREDICTIONS[cat]
            for cid in ["val_001", "val_002", "val_003"]:
                assert cid in pmap, f"{cat} missing {cid}"

    def test_regression_map_has_all(self):
        for cid in ["val_001", "val_002", "val_003"]:
            assert cid in REGRESSION_PREDICTIONS


# ?? ?????: 4-phase pipeline + gate ?????????????????

class TestFullPipelineWithGate:
    """baseline ? attribution ? optimizer ? validator ? gate ????"""

    @pytest.mark.asyncio
    async def test_four_phase_to_gate(self):
        from src.gate import AcceptanceGate
        import json

        base = Path(__file__).parent.parent / "config"

        # Phase 1: baseline
        br = BaselineRunner(mode="fake")
        results = await br.run(
            base / "train.evalset.json", base / "val.evalset.json",
        )

        # Phase 2: attribution
        ar = AttributionRunner()
        attr = ar.run(results["train"], results["val"])

        # Phase 3: optimizer
        opt = FakeOptimizer()
        opt_result = opt.optimize(attr)

        # Phase 4: validator
        vr = ValidationRunner(mode="fake")
        val_result = vr.run(results["val"], opt_result)

        # Phase 5: gate
        with open(base / "optimizer.json", "r", encoding="utf-8") as f:
            gate_config = json.load(f)["gate"]
        gate = AcceptanceGate(gate_config)

        decision = gate.decide(
            baseline_scores=results["val"].score_map,
            candidate_scores=val_result.score_map,
            baseline_train_scores=results["train"].score_map,
            candidate_train_scores=val_result.score_map,
            baseline_cost=results["val"].summary.avg_cost * results["val"].summary.total,
            candidate_cost=val_result.summary.total_cost_candidate,
        )

        # ???????????
        full_output = {
            "baseline": {
                "train": results["train"].to_dict(),
                "val": results["val"].to_dict(),
            },
            "attribution": attr.to_dict(),
            "optimization": opt_result.to_dict(),
            "validation": val_result.to_dict(),
            "gate_decision": {
                "accepted": decision.accepted,
                "reason": decision.reason,
            },
        }
        j = json.dumps(full_output, ensure_ascii=False, indent=2)
        assert len(j) > 2000
        assert decision.accepted, f"Gate should accept: {decision.reason}"

        print(f"\n  Gate decision: accepted={decision.accepted} reason={decision.reason[:80]}")
        print(f"  Val delta: {val_result.summary.avg_score_delta:+.3f}")
        print(f"  Improved: {val_result.summary.improved} Regressed: {val_result.summary.regressed}")
