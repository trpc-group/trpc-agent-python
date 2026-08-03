"""Baseline evaluation stage — runs AgentEvaluator on train and validation sets."""

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any

from .comparator import TraceMatcher, FailureCategory, default_matcher
from .config import PipelineConfig


def _ensure_repo_root_in_path() -> None:
    """确保项目根在 sys.path（trpc_agent_sdk 是源码包，位于项目根）。

    pipeline/ → eval_optimize_loop → optimization → examples → 项目根（4 级）。
    仅当项目根不在 sys.path 时插入；失败时记录 warning 而非静默吞掉。
    """
    try:
        _pipeline_dir = os.path.dirname(os.path.abspath(__file__))
        _repo_root = os.path.abspath(
            os.path.join(_pipeline_dir, os.pardir, os.pardir, os.pardir, os.pardir))
        if _repo_root not in sys.path:
            sys.path.insert(0, _repo_root)
    except Exception as e:  # pragma: no cover — 极端路径异常
        print(f"  ⚠️  warning: 无法将项目根加入 sys.path: {e}")


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


async def run_baseline_sdk(
    evalset_path: str,
    *,
    call_agent: Any = None,
    eval_config: Any = None,
) -> BaselineResult:
    """Run baseline evaluation using the real SDK AgentEvaluator.

    trace 格式的 evalset 可离线评测（无需 API key，无需 agent_module）；
    若提供 call_agent 则走真实 agent 调用。SDK 的 `evaluate_eval_set`
    要求必填 `eval_config`，此处从 optimizer.json 的 evaluate 段构造。

    Args:
        evalset_path: Path to .evalset.json file.
        call_agent: 可选，真实 agent 调用入口（async callable）。
        eval_config: 可选，SDK EvalConfig。缺省时从 data/optimizer.json 加载。

    Returns:
        BaselineResult from actual AgentEvaluator run.
    """
    try:
        # 确保项目根在 sys.path（trpc_agent_sdk 是源码包，位于项目根）。
        # pipeline/ → eval_optimize_loop → optimization → examples → 项目根（4 级）
        _ensure_repo_root_in_path()

        from trpc_agent_sdk.evaluation import AgentEvaluator, EvalSet
        from trpc_agent_sdk.evaluation._eval_metrics import EvalStatus

        result = BaselineResult(evalset_id=os.path.basename(evalset_path))
        if not os.path.exists(evalset_path):
            result.errors.append(f"Evalset not found: {evalset_path}")
            return result

        # eval_config 必填；缺省时从默认 data/optimizer.json 的 evaluate 段加载
        if eval_config is None:
            from .config import load_optimize_config
            _default_opt = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data", "optimizer.json")
            eval_config = load_optimize_config(_default_opt)

        with open(evalset_path, encoding="utf-8") as f:
            eval_set = EvalSet.model_validate_json(f.read())
        # trace 模式离线评测：evaluate_eval_set 返回 per-case 结果
        _, _, _, case_results = await AgentEvaluator.evaluate_eval_set(
            eval_set,
            call_agent=call_agent,
            eval_config=eval_config,
            print_detailed_results=False,
        )

        # case_results: dict[str, list[EvalCaseResult]] — case_id → results
        passed = 0
        failed_case_ids = []
        per_case = []
        total = 0
        for case_id, results in (case_results or {}).items():
            for cr in results:
                st = getattr(cr, "final_eval_status", None)
                if st == EvalStatus.NOT_EVALUATED:
                    # 未评测（如缺少 trace 数据）≠ 失败：跳过，不计入 total 也不计失败，
                    # 避免虚增失败数、拉低 pass_rate、污染 gate 的 improvement 判定。
                    continue
                total += 1
                ok = st == EvalStatus.PASSED
                if ok:
                    passed += 1
                else:
                    failed_case_ids.append(case_id)
                reason = getattr(cr, "error_message", "") or ("" if ok else "failed")
                per_case.append({
                    "eval_id": case_id,
                    "pass": ok,
                    "reason": reason,
                    "category": "",
                    "evidence": "",
                })

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
        # SDK 评测失败（如 evalset schema 不兼容）时，降级到 trace comparator 评测，
        # 保证 live 模式仍有有意义的 baseline，pipeline 不中断。
        from .config import PipelineConfig
        fallback = run_baseline_fake(evalset_path, PipelineConfig())
        fallback.errors = [
            f"SDK AgentEvaluator failed ({type(e).__name__}: {e}); "
            f"fell back to trace comparator"
        ]
        return fallback
