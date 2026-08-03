"""Baseline evaluation stage — runs AgentEvaluator on train and validation sets."""

import json
import os
from dataclasses import dataclass, field
from typing import Any

from .comparator import TraceMatcher, FailureCategory, default_matcher
from .config import PipelineConfig
from ._paths import ensure_repo_root_in_path


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

    try:
        with open(evalset_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        # 与缺失文件一致：解析失败也优雅返回 errors，而非抛 JSONDecodeError 崩溃
        return BaselineResult(errors=[f"Failed to parse evalset {evalset_path}: {e}"])

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
    optimizer_config_path: str | None = None,
) -> BaselineResult:
    """Run baseline evaluation using the real SDK AgentEvaluator.

    trace 格式的 evalset 可离线评测（无需 API key，无需 agent_module）；
    若提供 call_agent 则走真实 agent 调用。SDK 的 `evaluate_eval_set`
    要求必填 `eval_config`，此处从 optimizer.json 的 evaluate 段构造。

    Args:
        evalset_path: Path to .evalset.json file.
        call_agent: 可选，真实 agent 调用入口（async callable）。
        eval_config: 可选，SDK EvalConfig。缺省时从 optimizer_config_path
            （或默认 data/optimizer.json）加载。
        optimizer_config_path: 可选，用户指定的 optimizer.json 路径；
            避免静默改用默认配置的 metrics/阈值。

    Returns:
        BaselineResult from actual AgentEvaluator run.
    """
    try:
        # 确保项目根在 sys.path（trpc_agent_sdk 是源码包，位于项目根）。
        # pipeline/ → eval_optimize_loop → optimization → examples → 项目根（4 级）
        ensure_repo_root_in_path()

        # 用 SDK 公开导出，避免耦合私有模块（_eval_metrics 等重构后 ImportError
        # 会被误判为 "SDK 不可用" 而静默降级为 fake 评分）
        from trpc_agent_sdk.evaluation import (
            AgentEvaluator,
            EvalSet,
            EvalStatus,
        )

        result = BaselineResult(evalset_id=os.path.basename(evalset_path))
        if not os.path.exists(evalset_path):
            result.errors.append(f"Evalset not found: {evalset_path}")
            return result

        # eval_config 必填；缺省时从用户 optimizer_config_path 加载，
        # 未指定才回退到默认 data/optimizer.json（避免静默改用默认配置评分）
        if eval_config is None:
            from .config import load_optimize_config
            if optimizer_config_path is None:
                optimizer_config_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "optimizer.json")
            eval_config = load_optimize_config(optimizer_config_path)

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
            # 以 case 为单位聚合：num_runs>1 时每个 case 有多个 result，
            # 避免 total 按"运行数"统计、failed_case_ids 出现重复。
            case_passed = False
            case_failed = False
            fail_reason = ""
            for cr in results:
                st = getattr(cr, "final_eval_status", None)
                if st == EvalStatus.NOT_EVALUATED:
                    # 未评测（如缺少 trace 数据）≠ 失败：跳过，不计入 total 也不计失败，
                    # 避免虚增失败数、拉低 pass_rate、污染 gate 的 improvement 判定。
                    continue
                if st == EvalStatus.PASSED:
                    case_passed = True
                else:
                    case_failed = True
                    fail_reason = getattr(cr, "error_message", "") or "failed"
            if not case_passed and not case_failed:
                continue  # 该 case 全部 NOT_EVALUATED
            total += 1
            ok = case_passed  # 任一 run 通过即视为 case 通过（宽松聚合）
            if ok:
                passed += 1
            else:
                failed_case_ids.append(case_id)
            per_case.append({
                "eval_id": case_id,
                "pass": ok,
                "reason": fail_reason if not ok else "",
                "category": "",
                "evidence": "",
            })

        if total == 0 and not result.errors:
            # 全部 case 都是 NOT_EVALUATED：空评分不能静默当合法 baseline
            result.errors.append(
                "all SDK EvalCaseResults were NOT_EVALUATED — no baseline scored")

        result.total_cases = total
        result.passed_cases = passed
        result.failed_cases = total - passed
        result.failed_case_ids = failed_case_ids
        result.pass_rate = passed / total if total > 0 else 0.0
        result.per_case_results = per_case
        # 与 run_baseline_fake 的键集合对齐（SDK 路径没有 per-case score，用 pass_rate 兜底）
        result.metric_breakdown = {
            "overall_pass_rate": result.pass_rate,
            "final_response_avg_score": result.pass_rate,
        }
        return result

    except ImportError:
        # SDK 不可用 → 与其它降级路径一致回退到 trace comparator，
        # 使 run_pipeline 的 "fell back to trace comparator" 告警与真实状态相符。
        # 保留 fake 回退自身的原始错误（如 evalset 缺失），不覆盖真实根因。
        from .config import PipelineConfig
        fallback = run_baseline_fake(evalset_path, PipelineConfig())
        fallback.errors = [
            "SDK AgentEvaluator not available — fell back to trace comparator"
        ] + fallback.errors
        return fallback
    except (ValueError, KeyError, TypeError) as e:
        # evalset/配置校验失败（如 pydantic ValidationError，系 ValueError 子类）。
        # 为保持 live 模式可运行，仍降级到 trace comparator，但清晰标记：
        # 该结果来自 comparator、**不是** SDK 评分，避免把配置/数据问题伪装成
        # "SDK 评分正常"。保留 fake 自身错误。其余非预期异常（AttributeError 等
        # pipeline bug）向上抛出。
        from .config import PipelineConfig
        fallback = run_baseline_fake(evalset_path, PipelineConfig())
        fallback.errors = [
            f"SDK AgentEvaluator rejected evalset ({type(e).__name__}: {e}); "
            f"result is trace-comparator scored, NOT SDK scoring"
        ] + fallback.errors
        return fallback
