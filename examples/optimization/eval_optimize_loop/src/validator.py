"""Phase 4: 候选验证引擎。"""
from __future__ import annotations
from pathlib import Path
import json
from dataclasses import dataclass, field
from typing import Optional
from fake.fake_judge import FakeJudge
from src.baseline import BaselineResult
from src.optimizer import OptimizationResult

@dataclass
class DeltaCase:
    case_id: str; ground_truth: str
    baseline_predicted: str; baseline_score: float; baseline_passed: bool
    candidate_predicted: str; candidate_score: float; candidate_passed: bool
    score_delta: float; status: str = "unchanged"; char_delta: int = 0
    baseline_judge: dict = field(default_factory=dict)
    candidate_judge: dict = field(default_factory=dict)
    baseline_cost: float = 0.0; candidate_cost: float = 0.0
    def to_dict(self):
        return {k: round(v,6) if isinstance(v,float) else v for k,v in self.__dict__.items() if not k.startswith("_")}

@dataclass
class ValidationSummary:
    total: int = 0; improved: int = 0; regressed: int = 0; unchanged: int = 0
    avg_baseline_score: float = 0.0; avg_candidate_score: float = 0.0
    avg_score_delta: float = 0.0; total_cost_baseline: float = 0.0; total_cost_candidate: float = 0.0
    def to_dict(self):
        return {k: round(v,6) if isinstance(v,float) else v for k,v in self.__dict__.items() if not k.startswith("_")}

@dataclass
class ValidationResult:
    candidate_id: str = ""; delta_cases: list = field(default_factory=list)
    summary: ValidationSummary = field(default_factory=ValidationSummary)
    optimization_target: str = ""
    @property
    def score_map(self): return {d.case_id: d.candidate_score for d in self.delta_cases}
    @property
    def new_failures(self): return [d for d in self.delta_cases if d.baseline_passed and not d.candidate_passed]
    def to_dict(self):
        return {"candidate_id":self.candidate_id,"delta_cases":[d.to_dict() for d in self.delta_cases],"summary":self.summary.to_dict(),"optimization_target":self.optimization_target}

# Each category has distinguishable predictions so validator differentiates
# optimization strategies per failure type (fix: round-4 review)
CANDIDATE_PREDICTIONS = {
    "final_answer_mismatch":        {"val_001":"粤B54321","val_002":"苏D13579","val_003":"浙C36912"},
    "knowledge_recall_insufficient": {"val_001":"粤B54321","val_002":"苏D13579","val_003":"浙C3691Z"},
    "tool_call_error":               {"val_001":"粤XXXXX","val_002":"苏D1?79","val_003":"浙C3691X"},
    "param_error":                   {"val_001":"粤B5?321","val_002":"苏D13XXX","val_003":"浙C36111"},
    "llm_rubric_fail":               {"val_001":"粤B55321","val_002":"苏D1S579","val_003":"浙C3691Z"},
    "format_invalid":                {"val_001":"粤B-54321","val_002":"苏D13579-ERR","val_003":"null"},
}
REGRESSION_PREDICTIONS = {"val_001":"粤B5432Z","val_002":"苏D1XXXX","val_003":"浙XXXXX"}

class ValidationRunner:
    def __init__(self, mode="fake", **kwargs):
        if mode not in ("fake","real"): raise ValueError(f"Unknown mode: {mode}")
        if mode == "real":
            import warnings
            warnings.warn("ValidationRunner real mode is not yet implemented. Use fake mode.", FutureWarning, stacklevel=2)
        self.mode = mode; self.kwargs = kwargs
        if mode == "fake": self._judge = FakeJudge()

    def run(self, val_baseline, optimization_result, simulate_regression=False):
        candidate = optimization_result.latest_candidate
        if candidate is None: return ValidationResult(candidate_id="none")
        if self.mode == "fake": return self._run_fake(val_baseline, candidate, simulate_regression)
        return self._run_real(val_baseline, candidate)

    def _run_fake(self, val_baseline, candidate, simulate_regression=False):
        pred_map = REGRESSION_PREDICTIONS if simulate_regression else CANDIDATE_PREDICTIONS.get(
            candidate.failure_category)
        if pred_map is None:
            import warnings
            warnings.warn(
                f"Unknown failure_category '{candidate.failure_category}' not in CANDIDATE_PREDICTIONS; "
                f"falling back to final_answer_mismatch"
            )
            pred_map = CANDIDATE_PREDICTIONS["final_answer_mismatch"]
        deltas = []
        for bl in val_baseline.cases:
            cp_pred = pred_map.get(bl.case_id, bl.predicted)
            cj = self._judge.evaluate(bl.case_id, bl.ground_truth, cp_pred)
            cc = sum(1 for i,c in enumerate(cp_pred) if i<len(bl.ground_truth) and c==bl.ground_truth[i])
            sd = cj.score.overall - bl.score
            st = "improved" if sd>0.005 else ("regressed" if sd<-0.005 else "unchanged")
            cd = cc - bl.char_correct
            deltas.append(DeltaCase(
                case_id=bl.case_id, ground_truth=bl.ground_truth,
                baseline_predicted=bl.predicted, baseline_score=bl.score, baseline_passed=bl.passed,
                candidate_predicted=cp_pred, candidate_score=cj.score.overall, candidate_passed=cj.passed,
                score_delta=sd, status=st, char_delta=cd,
                baseline_judge={"recognition":bl.judge_recognition,"blacklist":bl.judge_blacklist,"response":bl.judge_response},
                candidate_judge={"recognition":cj.score.recognition_quality,"blacklist":cj.score.blacklist_quality,"response":cj.score.response_quality},
                baseline_cost=bl.cost, candidate_cost=bl.cost*1.15))
        s = self._build_summary(deltas)
        return ValidationResult(candidate_id=candidate.candidate_id, delta_cases=deltas, summary=s,
            optimization_target=f"{candidate.target_prompt_type}:{candidate.failure_category}")

    def _run_real(self, val_baseline, candidate):
        try: from trpc_agent.optimization import AgentEvaluator
        except ImportError: raise ImportError("Real mode requires trpc_agent. Use fake mode.")
        raise NotImplementedError("Real mode pending.")

    @staticmethod
    def _build_summary(deltas):
        t = len(deltas)
        if t == 0: return ValidationSummary()
        imp = sum(1 for d in deltas if d.status=="improved")
        reg = sum(1 for d in deltas if d.status=="regressed")
        ab = sum(d.baseline_score for d in deltas)/t
        ac = sum(d.candidate_score for d in deltas)/t
        return ValidationSummary(total=t, improved=imp, regressed=reg, unchanged=t-imp-reg,
            avg_baseline_score=ab, avg_candidate_score=ac, avg_score_delta=ac-ab,
            total_cost_baseline=sum(d.baseline_cost for d in deltas),
            total_cost_candidate=sum(d.candidate_cost for d in deltas))

def run_validation(val_baseline, optimization_result, mode="fake", simulate_regression=False):
    return ValidationRunner(mode=mode).run(val_baseline, optimization_result, simulate_regression)
