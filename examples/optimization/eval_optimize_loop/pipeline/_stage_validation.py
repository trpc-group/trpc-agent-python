"""Stage 4: 候选验证 — 重新评估验证集并计算逐 case delta。

本阶段对优化后的验证集（val_optimized.evalset.json）运行评测，
并与 Stage 1 的基线验证结果逐 case 对比，生成：
  - 候选验证报告（EvalSetReport）
  - 逐 case 对比详情（PerCaseDelta 列表）

逐 case delta 包含了每个 case 的：
  - 状态转换（如 FAILED→PASSED 表示优化成功）
  - 各 metric 分数变化
  - 场景类型标注（optimizable_success 等）

调用方式：
    candidate_report, deltas = await ValidationComparator.evaluate_and_compare(
        optimized_eval_set_path="data/val_optimized.evalset.json",
        metrics_config_path="data/test_config.json",
        baseline_report=val_report,
        scenario_map=SCENARIO_MAP,
    )
"""

from __future__ import annotations

from trpc_agent_sdk.evaluation._eval_metrics import EvalStatus
from trpc_agent_sdk.evaluation._eval_result import EvaluateResult

from pipeline._models import EvalSetReport, PerCaseDelta
from pipeline._stage_baseline import BaselineEvaluator


class ValidationComparator:
    """候选验证对比器：评测优化后验证集并计算与基线的差异。

    复用 BaselineEvaluator.evaluate() 对优化后的验证集运行相同评测，
    然后逐 case 对比基线 vs 候选的状态和分数。
    """

    @staticmethod
    async def evaluate_and_compare(
        *,
        optimized_eval_set_path: str,
        metrics_config_path: str,
        baseline_report: EvalSetReport,
        scenario_map: dict[str, str] | None = None,
    ) -> tuple[EvalSetReport, list[PerCaseDelta]]:
        """评测候选 prompt 并计算与基线的逐 case 差异。

        Args:
            optimized_eval_set_path: 优化后的验证集 evalset 文件路径（trace 模式）。
            metrics_config_path: test_config.json metric 配置文件路径。
            baseline_report: Stage 1 基线验证的 EvalSetReport。
            scenario_map: eval_id → 场景类型的可选映射（用于标注 delta 的场景）。

        Returns:
            (EvalSetReport, list[PerCaseDelta]) 元组：
            - EvalSetReport: 候选 prompt 的验证报告
            - list[PerCaseDelta]: 逐 case 的基线 vs 候选对比
        """
        # 对优化后的验证集运行相同评测
        _, candidate_report = await BaselineEvaluator.evaluate(
            eval_set_path=optimized_eval_set_path,
            metrics_config_path=metrics_config_path,
        )

        # 构建 eval_id → PerCaseScore 的快速查找映射
        baseline_by_id = {
            c.eval_id: c for c in baseline_report.per_case
        }
        candidate_by_id = {
            c.eval_id: c for c in candidate_report.per_case
        }

        if scenario_map is None:
            scenario_map = {}

        deltas: list[PerCaseDelta] = []
        # 合并基线和候选的所有 eval_id
        all_eval_ids = set(baseline_by_id.keys()) | set(candidate_by_id.keys())

        for eval_id in sorted(all_eval_ids):
            base = baseline_by_id.get(eval_id)
            cand = candidate_by_id.get(eval_id)

            # 提取状态（缺失的 case 标记为 MISSING）
            base_status = base.overall_status if base else "MISSING"
            cand_status = cand.overall_status if cand else "MISSING"

            # 提取分数
            base_scores = dict(base.metric_scores) if base else {}
            cand_scores = dict(cand.metric_scores) if cand else {}

            # 计算各 metric 的分数变化
            score_delta = {}
            all_metrics = set(base_scores.keys()) | set(cand_scores.keys())
            for metric in all_metrics:
                score_delta[metric] = cand_scores.get(metric, 0.0) - base_scores.get(metric, 0.0)

            # 状态转换字符串（如 FAILED->PASSED）
            transition = f"{base_status}->{cand_status}"

            deltas.append(PerCaseDelta(
                eval_id=eval_id,
                scenario=scenario_map.get(eval_id, "unknown"),
                baseline_status=base_status,
                candidate_status=cand_status,
                baseline_scores=base_scores,
                candidate_scores=cand_scores,
                score_delta=score_delta,
                transition=transition,
            ))

        return candidate_report, deltas
