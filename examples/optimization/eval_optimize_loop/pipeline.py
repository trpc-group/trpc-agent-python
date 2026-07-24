"""Reproducible evaluation, optimization, regression, and audit pipeline.

The bundled trace adapter makes the complete workflow runnable without model
credentials. Production callers can replace ``TraceModel`` and
``PromptOptimizer`` while retaining attribution, comparison, gates, and report
serialization.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EvaluationResult:
    case_id: str
    score: float
    metric_scores: dict[str, float]
    passed: bool
    hard_fail: bool
    reason: str | None
    attribution: str | None
    trace: dict[str, Any]
    cost: float


class TraceModel:
    """Deterministic fake model backed by recorded baseline/candidate traces."""

    def run(self, case: dict[str, Any], prompt_version: str) -> dict[str, Any]:
        trace = case["_loop"]["traces"][prompt_version]
        return json.loads(json.dumps(trace))


class FailureAttributor:
    """Classify failures from expected behavior and the recorded trace."""

    @staticmethod
    def classify(case: dict[str, Any], trace: dict[str, Any]) -> tuple[str, str]:
        spec = case["_loop"]
        if spec.get("expected_tool") != trace.get("tool"):
            return "tool_call_error", "expected tool was not called"
        if spec.get("expected_args") != trace.get("args"):
            return "parameter_error", "tool arguments differ from expectation"
        if spec.get("requires_knowledge") and not trace.get("knowledge_recalled", False):
            return "knowledge_recall_insufficient", "required fact was not recalled"
        response = trace.get("response", "")
        if spec.get("format_regex") and not re.search(spec["format_regex"], response):
            return "format_noncompliance", "response does not match required format"
        if trace.get("rubric_score", 1.0) < spec.get("rubric_threshold", 0.0):
            return "llm_rubric_below_threshold", "LLM rubric score is below threshold"
        return "final_response_mismatch", "final response differs from reference"


class EvalOptimizePipeline:
    """Run baseline, attribution, optimization, validation, and gate stages."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.config = self._load_json("optimizer.json")
        self.model = TraceModel()
        self.attributor = FailureAttributor()

    def run(self, output_dir: str | Path | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        train_cases = self._load_json("train.evalset.json")["eval_cases"]
        val_cases = self._load_json("val.evalset.json")["eval_cases"]
        baseline_prompt = (self.root / self.config["prompt"]["source"]).read_text(encoding="utf-8")

        baseline_train = self.evaluate(train_cases, "baseline")
        baseline_val = self.evaluate(val_cases, "baseline")
        failure_counts = Counter(
            item.attribution for item in baseline_train if item.attribution
        )
        candidate_prompt = self.optimize_prompt(baseline_prompt, failure_counts)
        candidate_train = self.evaluate(train_cases, "candidate")
        candidate_val = self.evaluate(val_cases, "candidate")
        comparisons = self.compare(baseline_val, candidate_val)
        gate = self.apply_gate(baseline_val, candidate_val, comparisons)

        rounds = [{
            "round": 1,
            "prompt": candidate_prompt,
            "failure_attribution": dict(failure_counts),
            "train_score": self._average(candidate_train),
            "validation_score": self._average(candidate_val),
            "cost": sum(item.cost for item in candidate_train + candidate_val),
        }]
        report = {
            "experiment": {
                **self.config["experiment"],
                "mode": "trace",
                "duration_seconds": round(time.perf_counter() - started, 6),
                "inputs": {
                    "train": "train.evalset.json",
                    "validation": "val.evalset.json",
                    "optimizer": "optimizer.json",
                    "prompt": self.config["prompt"]["source"],
                },
                "configuration": self.config,
            },
            "baseline": self._evaluation_block(baseline_train, baseline_val),
            "candidate": {
                **self._evaluation_block(candidate_train, candidate_val),
                "prompt": candidate_prompt,
            },
            "delta": {
                "train_score": round(self._average(candidate_train) - self._average(baseline_train), 6),
                "validation_score": round(self._average(candidate_val) - self._average(baseline_val), 6),
                "cases": comparisons,
            },
            "failure_attribution": dict(failure_counts),
            "rounds": rounds,
            "gate": gate,
        }
        destination = Path(output_dir) if output_dir else self.root
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "optimization_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (destination / "optimization_report.md").write_text(
            self.render_markdown(report), encoding="utf-8"
        )
        return report

    def evaluate(self, cases: list[dict[str, Any]], prompt_version: str) -> list[EvaluationResult]:
        results = []
        for case in cases:
            trace = self.model.run(case, prompt_version)
            spec = case["_loop"]
            metric_scores = self._metric_scores(spec, trace)
            score = round(sum(metric_scores.values()) / len(metric_scores), 6) if metric_scores else 1.0
            passed = score >= spec.get("pass_threshold", 1.0)
            attribution = reason = None
            if not passed:
                attribution, reason = self.attributor.classify(case, trace)
            results.append(EvaluationResult(
                case_id=case["eval_id"],
                score=score,
                metric_scores=metric_scores,
                passed=passed,
                hard_fail=bool(spec.get("hard") and not passed),
                reason=reason,
                attribution=attribution,
                trace=trace,
                cost=float(trace.get("cost", 0.0)),
            ))
        return results

    @staticmethod
    def _metric_scores(spec: dict[str, Any], trace: dict[str, Any]) -> dict[str, float]:
        checks: dict[str, float] = {}
        if "expected_response" in spec:
            checks["final_response"] = float(
                spec["expected_response"].lower() in trace.get("response", "").lower()
            )
        if "expected_tool" in spec:
            checks["tool_call"] = float(spec["expected_tool"] == trace.get("tool"))
        if "expected_args" in spec:
            checks["tool_arguments"] = float(spec["expected_args"] == trace.get("args"))
        if "format_regex" in spec:
            checks["response_format"] = float(
                bool(re.search(spec["format_regex"], trace.get("response", "")))
            )
        if spec.get("requires_knowledge"):
            checks["knowledge_recall"] = float(bool(trace.get("knowledge_recalled")))
        if "rubric_threshold" in spec:
            checks["llm_rubric"] = float(
                trace.get("rubric_score", 0.0) >= spec["rubric_threshold"]
            )
        return checks

    @staticmethod
    def optimize_prompt(baseline: str, failures: Counter) -> str:
        advice = {
            "format_noncompliance": "Return exactly the requested machine-readable format.",
            "parameter_error": "Verify every tool argument and unit before calling the tool.",
            "tool_call_error": "Select the tool named by the task and do not simulate its output.",
            "knowledge_recall_insufficient": "Ground factual answers in the provided knowledge context.",
            "llm_rubric_below_threshold": "Explain the answer clearly and satisfy every rubric.",
            "final_response_mismatch": "Check the final answer against all explicit constraints.",
        }
        additions = [advice[name] for name, _ in failures.most_common() if name in advice]
        optimized = "\n".join(f"- {item}" for item in additions)
        return baseline.rstrip() + "\n\n# Optimized instructions\n" + optimized + "\n"

    @staticmethod
    def compare(
        baseline: list[EvaluationResult], candidate: list[EvaluationResult]
    ) -> list[dict[str, Any]]:
        output = []
        for before, after in zip(baseline, candidate):
            if not before.passed and after.passed:
                change = "new_pass"
            elif before.passed and not after.passed:
                change = "new_fail"
            elif after.score > before.score:
                change = "improved"
            elif after.score < before.score:
                change = "regressed"
            else:
                change = "unchanged"
            output.append({
                "case_id": before.case_id,
                "baseline_score": before.score,
                "candidate_score": after.score,
                "delta": round(after.score - before.score, 6),
                "change": change,
                "hard_fail": after.hard_fail,
            })
        return output

    def apply_gate(
        self,
        baseline: list[EvaluationResult],
        candidate: list[EvaluationResult],
        comparisons: list[dict[str, Any]],
    ) -> dict[str, Any]:
        policy = self.config["gate"]
        improvement = self._average(candidate) - self._average(baseline)
        total_cost = sum(item.cost for item in candidate)
        critical = set(policy.get("critical_case_ids", []))
        reasons = []
        if improvement < policy["min_validation_improvement"]:
            reasons.append("validation improvement is below the configured threshold")
        if policy.get("no_new_hard_fail") and any(
            item["change"] == "new_fail" and item["hard_fail"] for item in comparisons
        ):
            reasons.append("candidate introduces a new hard fail")
        if any(item["case_id"] in critical and item["delta"] < 0 for item in comparisons):
            reasons.append("a critical validation case regressed")
        if any(item["delta"] < -policy["max_case_regression"] for item in comparisons):
            reasons.append("per-case regression exceeds the configured limit")
        if total_cost > policy["max_validation_cost"]:
            reasons.append("validation cost exceeds budget")
        return {
            "decision": "reject" if reasons else "accept",
            "accepted": not reasons,
            "reasons": reasons or ["all validation, regression, critical-case, and cost gates passed"],
            "observed": {
                "validation_improvement": round(improvement, 6),
                "validation_cost": total_cost,
                "new_hard_fails": sum(
                    item["change"] == "new_fail" and item["hard_fail"] for item in comparisons
                ),
            },
            "policy": policy,
        }

    @staticmethod
    def render_markdown(report: dict[str, Any]) -> str:
        gate = report["gate"]
        lines = [
            "# Optimization Report",
            "",
            f"**Decision:** `{gate['decision']}`",
            "",
            f"- Baseline train: {report['baseline']['train_score']:.3f}",
            f"- Candidate train: {report['candidate']['train_score']:.3f}",
            f"- Baseline validation: {report['baseline']['validation_score']:.3f}",
            f"- Candidate validation: {report['candidate']['validation_score']:.3f}",
            f"- Validation delta: {report['delta']['validation_score']:+.3f}",
            "",
            "## Gate reasons",
            "",
            *[f"- {reason}" for reason in gate["reasons"]],
            "",
            "## Validation case comparison",
            "",
            "| Case | Baseline | Candidate | Delta | Change |",
            "|---|---:|---:|---:|---|",
        ]
        lines.extend(
            f"| {item['case_id']} | {item['baseline_score']:.3f} | "
            f"{item['candidate_score']:.3f} | {item['delta']:+.3f} | {item['change']} |"
            for item in report["delta"]["cases"]
        )
        lines.extend(["", "## Failure attribution", ""])
        lines.extend(
            f"- `{name}`: {count}" for name, count in report["failure_attribution"].items()
        )
        return "\n".join(lines) + "\n"

    @staticmethod
    def _average(results: list[EvaluationResult]) -> float:
        return sum(item.score for item in results) / len(results) if results else 0.0

    @classmethod
    def _evaluation_block(
        cls, train: list[EvaluationResult], validation: list[EvaluationResult]
    ) -> dict[str, Any]:
        serialize = lambda item: {
            "case_id": item.case_id,
            "score": item.score,
            "metric_scores": item.metric_scores,
            "passed": item.passed,
            "hard_fail": item.hard_fail,
            "failure_reason": item.reason,
            "attribution": item.attribution,
            "trace": item.trace,
            "cost": item.cost,
        }
        return {
            "train_score": round(cls._average(train), 6),
            "validation_score": round(cls._average(validation), 6),
            "train_cases": [serialize(item) for item in train],
            "validation_cases": [serialize(item) for item in validation],
            "cost": sum(item.cost for item in train + validation),
        }

    def _load_json(self, name: str) -> dict[str, Any]:
        return json.loads((self.root / name).read_text(encoding="utf-8"))
