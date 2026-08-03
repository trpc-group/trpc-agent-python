"""Validation — re-evaluate candidate prompts on the validation set.

Compares baseline vs candidate on the held-out validation set to detect
overfitting (train improvement without val improvement).
"""

import copy
import json
import os
from dataclasses import dataclass, field

from .baseline import BaselineResult
from .comparator import TraceMatcher, default_matcher
from .config import PipelineConfig


@dataclass
class ValidationDelta:
    """Per-case comparison between baseline and candidate."""
    eval_id: str
    baseline_passed: bool
    candidate_passed: bool
    change: str          # "new_pass", "new_fail", "improved", "degraded", "unchanged"


@dataclass
class ValidationResult:
    """Validation set comparison results."""
    baseline: BaselineResult | None = None
    candidate: BaselineResult | None = None
    candidate_train: BaselineResult | None = None
    deltas: list[ValidationDelta] = field(default_factory=list)

    @property
    def new_passes(self) -> int:
        return sum(1 for d in self.deltas if d.change == "new_pass")

    @property
    def new_failures(self) -> int:
        return sum(1 for d in self.deltas if d.change == "new_fail")

    @property
    def unchanged(self) -> int:
        return sum(1 for d in self.deltas if d.change == "unchanged")

    @property
    def is_overfitting(self) -> bool:
        """Overfitting: candidate introduces new failures that weren't in baseline."""
        return self.new_failures > 0


def run_validation_fake(
    val_evalset_path: str,
    baseline_val: BaselineResult,
    candidate_baseline: BaselineResult,
    config: PipelineConfig,
) -> ValidationResult:
    """Run validation comparison in fake mode.

    Args:
        val_evalset_path: Path to validation evalset.
        baseline_val: Baseline evaluation on validation set.
        candidate_baseline: Candidate evaluation on validation set (simulated).
        config: Pipeline configuration.

    Returns:
        ValidationResult with per-case deltas.
    """
    # In fake mode, the candidate results are simulated
    # We create deltas by comparing baseline vs candidate per-case results
    baseline_map = {
        c.get("eval_id"): c.get("pass", True)
        for c in baseline_val.per_case_results
    }
    candidate_map = {
        c.get("eval_id"): c.get("pass", True)
        for c in candidate_baseline.per_case_results
    }

    deltas = []
    all_ids = set(baseline_map.keys()) | set(candidate_map.keys())

    for case_id in sorted(all_ids):
        bl_pass = baseline_map.get(case_id, True)
        cd_pass = candidate_map.get(case_id, True)

        if not bl_pass and cd_pass:
            change = "new_pass"
        elif bl_pass and not cd_pass:
            change = "new_fail"
        else:
            change = "unchanged"

        deltas.append(ValidationDelta(
            eval_id=case_id,
            baseline_passed=bl_pass,
            candidate_passed=cd_pass,
            change=change,
        ))

    return ValidationResult(
        baseline=baseline_val,
        candidate=candidate_baseline,
        deltas=deltas,
    )


# ─────────────────────────────────────────────────────────────────────
# Section: 候选评估（场景驱动的真实评分）
# ─────────────────────────────────────────────────────────────────────


def _load_cases(path: str) -> list[dict]:
    """加载 evalset 的 cases 列表。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("eval_cases", [])


def _copy_case(case: dict) -> dict:
    """深拷贝一个 case，避免修改原始数据。"""
    return copy.deepcopy(case)


def _apply_scenario(case: dict, scenario: str, *, is_train: bool,
                    val_regression_cases: list[str] | None = None) -> dict:
    """根据场景生成候选 actual_conversation。

    - fix_attributed：train 上失败的 case 用期望替换实际（修复成功）。
      非失败 case 保持不变。
    - noop：候选 actual = baseline actual（无变化）。
    - overfit：train 全部用期望（"记住"训练集）；val 上指定回归 case 扰动（退化）。

    支持 case 携带可选的 `candidate_conversation` 字段：若存在则优先回放该内容，
    支持隐藏样本的真实评分（验收标准 #2）。
    """
    new_case = _copy_case(case)

    # 优先使用 case 自带的 candidate_conversation（真实回放）
    if "candidate_conversation" in case:
        new_case["actual_conversation"] = case["candidate_conversation"]
        return new_case

    conversation = case.get("conversation", [])
    actual = case.get("actual_conversation", [])
    case_id = str(case.get("eval_id", ""))

    if scenario == "noop":
        # 无变化：保持 baseline actual
        return new_case

    if scenario == "fix_attributed":
        # 仅修复归因失败类别的 case（用期望替换实际）
        failed = not _case_passes(case)
        if is_train and failed:
            new_case["actual_conversation"] = conversation
        return new_case

    if scenario == "overfit":
        if is_train:
            # train 全部"记住"期望 → train 提升
            if conversation:
                new_case["actual_conversation"] = conversation
        else:
            # val 指定回归 case 扰动 → val 退化
            if val_regression_cases and case_id in val_regression_cases:
                new_case["actual_conversation"] = _perturb_case(case)
        return new_case

    return new_case


def _case_passes(case: dict) -> bool:
    """用 TraceMatcher 判断 case 当前是否通过。"""
    matcher = default_matcher()
    return matcher.evaluate(case).passed


def _case_is_perturbable(case: dict) -> bool:
    """case 是否可被 _perturb_case 真正扰动（conversation[0] 有 final_response.parts）。"""
    conv = case.get("conversation") or []
    if not conv:
        return False
    parts = (conv[0].get("final_response") or {}).get("parts") or []
    return bool(parts)


def _perturb_case(case: dict) -> list[dict]:
    """扰动 case 的 actual_conversation，制造退化（val 回归场景）。

    把最终回复替换为固定错误文本，确保与期望不一致。
    """
    conversation = case.get("conversation", [])
    if not conversation:
        return case.get("actual_conversation", [])
    perturbed = _copy_case(conversation)
    for inv in perturbed:
        # final_response 可能显式为 None（部分 evalset schema 允许）→ 用 `or {}` 防护
        parts = (inv.get("final_response") or {}).get("parts", [])
        if parts:
            parts[0]["text"] = "[过拟合退化] 回复被错误改写，与期望不一致。"
    return perturbed


def _evaluate_cases(cases: list[dict], eval_set_id: str, matcher: TraceMatcher) -> BaselineResult:
    """用 TraceMatcher 批量评估 cases，返回 BaselineResult。"""
    total = len(cases)
    passed = 0
    failed_ids: list[str] = []
    per_case: list[dict] = []
    score_sum = 0.0

    for case in cases:
        verdict = matcher.evaluate(case)
        case_id = str(case.get("eval_id", "unknown"))
        if verdict.passed:
            passed += 1
        else:
            failed_ids.append(case_id)
        score_sum += verdict.score
        per_case.append({
            "eval_id": case_id,
            "pass": verdict.passed,
            "score": round(verdict.score, 4),
            "reason": verdict.detail or ("passed" if verdict.passed else "failed"),
            "category": str(verdict.category) if verdict.category else "",
            "evidence": verdict.evidence,
            "expected_final": verdict.expected_final,
            "actual_final": verdict.actual_final,
        })

    return BaselineResult(
        evalset_id=eval_set_id,
        pass_rate=passed / total if total > 0 else 0.0,
        total_cases=total,
        passed_cases=passed,
        failed_cases=total - passed,
        failed_case_ids=failed_ids,
        metric_breakdown={
            "overall_pass_rate": passed / total if total > 0 else 0.0,
            "final_response_avg_score": round(score_sum / total, 4) if total > 0 else 0.0,
        },
        per_case_results=per_case,
    )


def run_validation_trace(
    train_evalset_path: str,
    val_evalset_path: str,
    baseline_val: BaselineResult,
    optimizer_result,
    config: PipelineConfig,
    *,
    scenario: str = "fix_attributed",
    val_regression_cases: list[str] | None = None,
) -> ValidationResult:
    """场景驱动的候选评估（带 per_case_results）。

    根据候选场景生成候选 actuals，用 TraceMatcher 重评 train 和 val，
    与 baseline 逐 case 对比，检测过拟合（val 新增失败）。

    Args:
        train_evalset_path: 训练集路径。
        val_evalset_path: 验证集路径。
        baseline_val: baseline 在 val 上的结果。
        optimizer_result: 优化阶段结果（含 candidate_strategy / fixed_categories）。
        config: Pipeline 配置。
        scenario: 候选生成策略。
        val_regression_cases: overfit 场景下要扰动的 val case id 列表。

    Returns:
        ValidationResult with candidate train/val per-case deltas.
    """
    matcher = default_matcher()
    train_cases = _load_cases(train_evalset_path)
    val_cases = _load_cases(val_evalset_path)

    strat = scenario or getattr(optimizer_result, "candidate_strategy", "fix_attributed")

    # overfit 场景且未指定回归 case 时，自动选前 2 个 val case 扰动，
    # 避免"train 提升 + val 无退化"被误 ACCEPT（reviewer 指出的问题）
    effective_regression = val_regression_cases
    if strat == "overfit" and not effective_regression and val_cases:
        # 只选"可扰动"的 case（conversation[0].final_response.parts 存在）作为回归候选，
        # 避免选到无法制造退化的 case 落到下方 new_failures==0 的报错。
        perturbable = [c for c in val_cases if _case_is_perturbable(c)]
        pool = perturbable or val_cases
        effective_regression = [str(c.get("eval_id", "")) for c in pool[:2]]

    if strat == "overfit" and not effective_regression:
        # 空 val 集 / 未指定回归 case：overfit 场景无法演示"val 退化"，
        # 否则会变成 "train 全记住 + val 无退化" 而被 gate 误 ACCEPT。
        raise ValueError(
            "overfit scenario requires at least one val case to regress; "
            "val set is empty or --val-regression-cases not provided")

    # 生成候选 actuals
    candidate_train_cases = [
        _apply_scenario(c, strat, is_train=True, val_regression_cases=effective_regression)
        for c in train_cases
    ]
    candidate_val_cases = [
        _apply_scenario(c, strat, is_train=False, val_regression_cases=effective_regression)
        for c in val_cases
    ]

    candidate_train = _evaluate_cases(candidate_train_cases, "candidate-train", matcher)
    candidate_val = _evaluate_cases(candidate_val_cases, "candidate-val", matcher)

    # 计算 delta（与 baseline val 对比）
    baseline_map = {
        c.get("eval_id"): c.get("pass", True)
        for c in baseline_val.per_case_results
    }
    deltas = []
    all_ids = set(baseline_map.keys()) | {c.get("eval_id", "") for c in candidate_val.per_case_results}
    for case_id in sorted(all_ids):
        bl_pass = baseline_map.get(case_id, True)
        cd_pass = next(
            (c.get("pass", True) for c in candidate_val.per_case_results if c.get("eval_id") == case_id),
            True,
        )
        if not bl_pass and cd_pass:
            change = "new_pass"
        elif bl_pass and not cd_pass:
            change = "new_fail"
        else:
            change = "unchanged"
        deltas.append(ValidationDelta(
            eval_id=case_id,
            baseline_passed=bl_pass,
            candidate_passed=cd_pass,
            change=change,
        ))

    result = ValidationResult(
        baseline=baseline_val,
        candidate=candidate_val,
        deltas=deltas,
    )
    # 附上候选 train 结果供报告使用
    result.candidate_train = candidate_train

    if strat == "overfit" and result.new_failures == 0:
        # 指定的回归 case 未产生 new_fail（如缺少 final_response.parts 无法扰动），
        # 会让过拟合候选被 gate 误 ACCEPT，违反验收标准 #3 → 显式报错。
        raise ValueError(
            "overfit scenario did not produce any validation regression — "
            f"selected cases may lack final_response.parts: {effective_regression}")

    return result
