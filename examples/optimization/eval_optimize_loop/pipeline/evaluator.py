"""评测封装：跑 SDK AgentEvaluator.evaluate_eval_set 并归一化为 SplitResult。

trace 模式下 actual 预录制（fixtures），SDK 跳过 agent 直接评分，
两个确定性 metric（final_response contains / tool_trajectory exact）全程无 LLM。
"""
from __future__ import annotations

from statistics import mean
from typing import Any

from trpc_agent_sdk.evaluation import AgentEvaluator, EvalConfig

from offline.fixtures import VariantOutput, build_trace_eval_set, cases_for_split
from .models import CaseSnapshot, SplitResult


def _passed(status: Any) -> bool:
    if status is None:
        return False
    return str(getattr(status, "value", status)).upper() == "PASSED"


async def evaluate_split(cases: list[dict[str, Any]], variant: str, split: str, eval_config: EvalConfig) -> SplitResult:
    """对给定 variant + split 跑 trace 评测，返回归一化的 SplitResult。"""
    eval_set = build_trace_eval_set(cases, variant, split)
    _failed, _details, _lines, results_by_id = await AgentEvaluator.evaluate_eval_set(eval_set,
                                                                                      eval_config=eval_config,
                                                                                      print_detailed_results=False)

    ordered = cases_for_split(cases, split)
    case_map = {c["eval_id"]: c for c in ordered}
    snapshots_by_id: dict[str, CaseSnapshot] = {}
    for eval_id, runs in results_by_id.items():
        cr = runs[0]  # num_runs = 1
        metrics = {m.metric_name: float(m.score) for m in cr.overall_eval_metric_results}
        # trace 模式下用 metric 分数判 passed（所有 metric 达 threshold=1.0），
        # 不依赖 final_eval_status——其在 trace + 多 metric 组合下语义不稳定。
        ok = bool(metrics) and all(s >= 1.0 - 1e-9 for s in metrics.values())
        score = mean(metrics.values()) if metrics else (1.0 if ok else 0.0)
        c = case_map.get(eval_id, {})
        v: VariantOutput = c.get("variants", {}).get(variant, {})
        snapshots_by_id[eval_id] = CaseSnapshot(
            eval_id=eval_id,
            passed=ok,
            score=score,
            hard_fail=(score <= 0.0),
            metrics=metrics,
            actual_response=v.get("response"),
            expected_response=c.get("expected_response"),
            key_trajectory=[t["name"] for t in v.get("tool_uses", [])],
        )

    # 保持 fixtures 原始顺序（results_by_id 顺序不保证）
    snapshots = [snapshots_by_id[c["eval_id"]] for c in ordered if c["eval_id"] in snapshots_by_id]
    n = len(snapshots)
    pass_rate = sum(1 for s in snapshots if s.passed) / n if n else 0.0
    avg = mean(s.score for s in snapshots) if snapshots else 0.0
    return SplitResult(split=split, pass_rate=pass_rate, average_score=avg, cases=snapshots)
