"""Stage 4: 候选验证 — 写回 best_prompts 后重评验证集，与基线逐 case 对比.

Why write-back is mandatory: ``AgentOptimizer.optimize(update_source=False)`` 在
``finally`` 把源 prompt 回滚成 baseline（optimization.md §3.3 FAQ）。Stage 4 若不
显式写回候选 prompt，评的就是 baseline —— 候选报告会与基线完全相同，门控形同虚设。
因此 real 模式下必须在 ``applied_prompts`` 上下文内重评**同一份**验证集，退出时
（含异常路径）自动还原 baseline。

demo 模式下 ``best_prompts`` 为空 dict —— 无 prompt 可写回，直接评测（TraceBackend
从预录制轨迹算分，与 prompt 无关）。

调用方式：
    candidate_report, deltas = await ValidationComparator.evaluate_and_compare(
        backend=backend,
        val_eval_path="data/val.evalset.json",
        metrics_config_path="data/gate_metrics.json",
        target_prompt=target_prompt,
        best_prompts=opt_report.best_prompts,
        baseline_report=val_report,
        scenario_map=SCENARIO_MAP,
    )
"""

from __future__ import annotations

from trpc_agent_sdk.evaluation._target_prompt import TargetPrompt

from pipeline._eval_backend import EvalBackend, applied_prompts
from pipeline._models import EvalSetReport, PerCaseDelta


class ValidationComparator:
    """候选验证对比器：写回候选 prompt 重评验证集，并计算与基线的差异。"""

    @staticmethod
    async def evaluate_and_compare(
        *,
        backend: EvalBackend,
        val_eval_path: str,
        metrics_config_path: str,
        target_prompt: TargetPrompt,
        best_prompts: dict[str, str],
        baseline_report: EvalSetReport,
        scenario_map: dict[str, str] | None = None,
        num_runs: int = 2,
    ) -> tuple[EvalSetReport, list[PerCaseDelta]]:
        """写回 best_prompts 后重评验证集，返回候选报告与逐 case delta。

        Args:
            backend: 评测后端（TraceBackend / LiveBackend）。
            val_eval_path: 验证集 evalset 文件路径 —— 与 Stage 1 基线**同一份**。
            metrics_config_path: 门控 metric 配置文件路径。
            target_prompt: prompt 写回目标（real 模式指向 agent/system.md）。
            best_prompts: Stage 3 产出的最优 prompt；demo 模式为空 dict。
            baseline_report: Stage 1 验证集基线的 EvalSetReport。
            scenario_map: eval_id → 场景类型的可选映射（用于标注 delta 的场景）。
            num_runs: 重复评测次数，用于抵消 LLM 非确定性（README §7）。

        Returns:
            (候选 EvalSetReport, 逐 case PerCaseDelta 列表)。
        """
        if scenario_map is None:
            scenario_map = {}

        # demo 模式下 best_prompts 为空 → 不写回
        if best_prompts:
            async with applied_prompts(target_prompt, best_prompts):
                _, candidate_report = await backend.evaluate(
                    eval_set_path=val_eval_path,
                    metrics_config_path=metrics_config_path,
                    num_runs=num_runs,
                )
        else:
            _, candidate_report = await backend.evaluate(
                eval_set_path=val_eval_path,
                metrics_config_path=metrics_config_path,
                num_runs=num_runs,
            )

        return candidate_report, _compute_deltas(
            baseline_report=baseline_report,
            candidate_report=candidate_report,
            scenario_map=scenario_map,
        )


def _compute_deltas(
    *,
    baseline_report: EvalSetReport,
    candidate_report: EvalSetReport,
    scenario_map: dict[str, str],
) -> list[PerCaseDelta]:
    """逐 case 对比基线与候选：状态转换 + 各 metric 分数变化。"""
    base = {c.eval_id: c for c in baseline_report.per_case}
    cand = {c.eval_id: c for c in candidate_report.per_case}
    deltas: list[PerCaseDelta] = []

    for eval_id in sorted(set(base) | set(cand)):
        b = base.get(eval_id)
        c = cand.get(eval_id)
        b_status = b.overall_status if b else "MISSING"
        c_status = c.overall_status if c else "MISSING"
        b_scores = dict(b.metric_scores) if b else {}
        c_scores = dict(c.metric_scores) if c else {}
        delta = {
            m: c_scores.get(m, 0.0) - b_scores.get(m, 0.0)
            for m in (set(b_scores) | set(c_scores))
        }
        deltas.append(PerCaseDelta(
            eval_id=eval_id,
            scenario=scenario_map.get(eval_id, "unknown"),
            baseline_status=b_status,
            candidate_status=c_status,
            baseline_scores=b_scores,
            candidate_scores=c_scores,
            score_delta=delta,
            transition=f"{b_status}->{c_status}",
        ))
    return deltas
