"""Baseline evaluation stage — runs AgentEvaluator on train and validation sets."""

import json
import os
from dataclasses import dataclass, field
from typing import Any

from .comparator import TraceMatcher, FailureCategory, default_matcher
from .config import PipelineConfig


@dataclass
class BaselineResult:
    """Baseline evaluation results for an evalset."""
    evalset_id: str = ""
    pass_rate: float = 0.0
    total_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    failed_case_ids: list[str] = field(default_factory=list)
    metric_breakdown: dict[str, float] = field(default_factory=dict)
    per_case_results: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class EvalSetData:
    """In-memory representation of an evalset for fake mode."""
    eval_set_id: str
    cases: list[dict]


def run_baseline_fake(evalset_path: str, config: PipelineConfig) -> BaselineResult:
    """Run baseline evaluation in fake (trace-replay) mode.

    与 SDK AgentEvaluator 语义对齐：比较 `conversation`（期望）与
    `actual_conversation`（实际回放），用 TraceMatcher 逐 case 评分。
    修复旧实现"有 conversation 即通过"的空转问题——标注为 `_fail`
    的 case 现在会真实失败，从而驱动后续的失败归因与优化。

    Args:
        evalset_path: Path to .evalset.json file.
        config: Pipeline configuration.

    Returns:
        BaselineResult with trace-replay evaluation outcomes.
    """
    if not os.path.exists(evalset_path):
        return BaselineResult(errors=[f"Evalset not found: {evalset_path}"])

    with open(evalset_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    eval_set_id = data.get("eval_set_id", os.path.basename(evalset_path))
    cases = data.get("eval_cases", [])
    total = len(cases)

    matcher = default_matcher()
    passed = 0
    failed_case_ids = []
    per_case = []
    score_sum = 0.0

    for case in cases:
        case_id = case.get("eval_id", "unknown")
        verdict = matcher.evaluate(case)
        is_pass = verdict.passed

        if is_pass:
            passed += 1
        else:
            failed_case_ids.append(case_id)

        score_sum += verdict.score
        per_case.append({
            "eval_id": case_id,
            "pass": is_pass,
            "score": round(verdict.score, 4),
            "reason": verdict.detail or ("passed" if is_pass else "failed"),
            "category": str(verdict.category) if verdict.category else "",
            "evidence": verdict.evidence,
            "expected_final": verdict.expected_final,
            "actual_final": verdict.actual_final,
        })

    pass_rate = passed / total if total > 0 else 0.0
    avg_score = score_sum / total if total > 0 else 0.0

    return BaselineResult(
        evalset_id=eval_set_id,
        pass_rate=pass_rate,
        total_cases=total,
        passed_cases=passed,
        failed_cases=total - passed,
        failed_case_ids=failed_case_ids,
        metric_breakdown={
            "overall_pass_rate": pass_rate,
            "final_response_avg_score": round(avg_score, 4),
        },
        per_case_results=per_case,
    )


def run_baseline_sdk(evalset_path: str, *, call_agent: Any = None) -> BaselineResult:
    """Run baseline evaluation using the real SDK AgentEvaluator.

    trace 格式的 evalset 可离线评测（无需 API key）；若提供 call_agent
    则走真实 agent 调用。所有 SDK 调用均以 try/except 保护，失败返回
    errors 而非崩溃。

    Args:
        evalset_path: Path to .evalset.json file.
        call_agent: 可选，真实 agent 调用入口（async callable）。

    Returns:
        BaselineResult from actual AgentEvaluator run.
    """
    try:
        from trpc_agent_sdk.evaluation import AgentEvaluator

        result = BaselineResult(evalset_id=os.path.basename(evalset_path))
        if not os.path.exists(evalset_path):
            result.errors.append(f"Evalset not found: {evalset_path}")
            return result

        executer = AgentEvaluator.get_executer(
            evalset_path,
            call_agent=call_agent,
            print_detailed_results=False,
            print_summary_report=False,
        )
        try:
            executer.evaluate()
        except Exception:
            # SDK 在部分 case 失败时会抛异常，但 evaluate() 仍产出结果
            pass
        eval_result = executer.get_result()

        # 映射 SDK 结果到 BaselineResult
        cases = eval_result.eval_case_results if hasattr(eval_result, "eval_case_results") else []
        passed = 0
        failed_case_ids = []
        per_case = []
        for cr in cases:
            case_id = getattr(cr, "case_id", "") or getattr(cr, "eval_id", "") or "unknown"
            ok = getattr(cr, "passed", False)
            if ok:
                passed += 1
            else:
                failed_case_ids.append(case_id)
            per_case.append({
                "eval_id": case_id,
                "pass": ok,
                "reason": getattr(cr, "failure_reason", "") or ("" if ok else "failed"),
                "category": "",
                "evidence": "",
            })

        total = len(cases)
        result.total_cases = total
        result.passed_cases = passed
        result.failed_cases = total - passed
        result.failed_case_ids = failed_case_ids
        result.pass_rate = passed / total if total > 0 else 0.0
        result.per_case_results = per_case
        result.metric_breakdown = {"overall_pass_rate": result.pass_rate}
        return result

    except ImportError:
        return BaselineResult(
            errors=["SDK AgentEvaluator not available — use fake mode"]
        )
    except Exception as e:
        return BaselineResult(errors=[str(e)])
