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
    total = 0

    matcher = default_matcher()
    passed = 0
    failed_case_ids = []
    per_case = []
    score_sum = 0.0

    for case in cases:
        case_id = str(case.get("eval_id", "unknown"))
        verdict = matcher.evaluate(case)
        is_pass = verdict.passed

        if verdict.unreviewed:
            # legacy 未评测（无 actual_conversation）：不计入 pass_rate，
            # 与 SDK 路径 NOT_EVALUATED 一致，避免未评测样本虚高通过率、
            # 污染 gate 的 improvement 判定。per_case 保留标注供审计区分。
            per_case.append({
                "eval_id": case_id,
                "pass": is_pass,
                "score": round(verdict.score, 4),
                "reason": verdict.detail or "未评测",
                "category": str(verdict.category) if verdict.category else "",
                "evidence": verdict.evidence,
                "expected_final": verdict.expected_final,
                "actual_final": verdict.actual_final,
                "unreviewed": True,
            })
            continue

        total += 1
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
    config: Any = None,
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
        config: 可选，PipelineConfig；降级回退时用它而非默认配置。

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
        # trace 模式离线评测：evaluate_eval_set 返回 per-case 结果。
        # 先对返回值做长度/结构校验：SDK 接口结构变更（如新增/调整返回字段）
        # 在这里显式失败并向上抛出，而不是在后续 unpack 时抛 ValueError 被
        # except ValueError 分支降级为 comparator——避免把 SDK 结构变更伪装成
        # "可继续的 trace 基线"。
        _ret = await AgentEvaluator.evaluate_eval_set(
            eval_set,
            call_agent=call_agent,
            eval_config=eval_config,
            print_detailed_results=False,
        )
        # 先判类型再取 len：非 tuple/list 没有 len 可读，分开判定使意图清晰
        # （reviewer Suggestion）。
        if not isinstance(_ret, (tuple, list)):
            raise ValueError(
                "SDK evaluate_eval_set returned unexpected shape "
                f"({type(_ret).__name__}), expected a 4-tuple")
        if len(_ret) != 4:
            raise ValueError(
                "SDK evaluate_eval_set returned unexpected shape "
                f"(len={len(_ret)}), expected a 4-tuple")
        _, _, _, case_results = _ret

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
                if st is None:
                    # SDK 结果缺 final_eval_status（接口结构变更/异常态）：视作
                    # 未评测跳过，避免静默计为失败虚增失败数、拉低 pass_rate
                    # （reviewer Warning）。
                    continue
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
            # 全部 run 通过才算 case 通过；任一 run 失败即失败，避免"一过一败"
            # 的 flaky 结果被宽松聚合掩盖、抬高 baseline pass_rate。
            ok = case_passed and not case_failed
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
        # SDK 路径无 per-case score：final_response_avg_score 用 pass_rate 兜底，
        # 与 fake 路径（per-case score 均值）口径不同。为避免下游按同名字段误比，
        # SDK 路径改用 *_approximated 后缀与 fake 路径字段隔离——仅作审计字段、
        # 不参与 gate 决策（gate 只读 pass_rate）（reviewer Warning）。
        result.metric_breakdown = {
            "overall_pass_rate": result.pass_rate,
            "final_response_avg_score_approximated": result.pass_rate,
        }
        return result

    except ImportError as e:
        # 仅当缺失的是 trpc_agent_sdk（预期降级）才回退到 trace comparator；
        # 其它 ImportError（如将来代码里的拼写/导入 bug）向上抛出，避免被伪装成
        # "SDK 不可用"。保留 fake 回退自身的原始错误，不覆盖真实根因。
        if not (e.name or "").startswith("trpc_agent_sdk"):
            raise
        from .config import PipelineConfig
        _cfg = config or PipelineConfig()
        fallback = run_baseline_fake(evalset_path, _cfg)
        fallback.errors = [
            "SDK AgentEvaluator not available — fell back to trace comparator"
        ] + fallback.errors
        return fallback
    except ValueError as e:
        # evalset/配置校验失败（如 pydantic ValidationError，系 ValueError 子类）。
        # 这是配置/数据问题，**不是** "SDK 不可用"：若像 ImportError 那样降级为
        # trace comparator，会把校验失败伪装成 "trace-comparator scored" 的合法
        # 基线，下游 gate 仍会基于不可比口径给出决策。故直接抛出，由调用方
        # （run_pipeline 的 live 编排）显式处理——宁可失败，不假装可继续。
        # KeyError/TypeError 不收窄在此：它们更可能是 SDK 结果处理中的 pipeline
        # bug（缺键/对 None 取属性），本就不在此分支，自然向上抛出暴露根因。
        raise
