# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""阶段① / ④：评测执行 + 逐 case 结构化记录（CaseEvalRecord）提取。

对 ``AgentEvaluator`` 的两点关键用法：

1. ``get_executer(...)`` + ``await evaluate()``：任何 case 失败时框架会抛
   ``_EvaluationCasesFailed``（``AssertionError`` 子类，抛出前已填好结果）——
   这里精确 ``except _EvaluationCasesFailed: pass`` 后照常 ``get_result()``，
   与 SDK 内部 ``_optimize_evaluator_call.run_evaluator`` 的姿势一致；
   其它 ``AssertionError``（SDK/三方库真实断言失败）照常抛出，不被吞掉。
2. ``eval_metrics_file_path_or_dir=`` 显式指定共享 metric 配置文件，
   覆盖数据集目录的 ``test_config.json`` 约定 —— baseline 与候选回归
   必须使用同一份验收 metric 套件，评分口径才可比。

``CaseEvalRecord`` 是后续归因（阶段②）、delta 对比（阶段④）、报告
（阶段⑥）共用的最小充分信息集：metric 分与状态、失败理由、rubric 明细、
实际/期望工具轨迹、实际/期望最终回答。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from trpc_agent_sdk.evaluation import AgentEvaluator
from trpc_agent_sdk.evaluation._agent_evaluator import _EvaluationCasesFailed
from trpc_agent_sdk.evaluation._eval_case import get_all_tool_calls
from trpc_agent_sdk.evaluation._eval_metrics import EvalStatus


@dataclass
class CaseEvalRecord:
    """一条 eval case 的一次评测结果（num_runs=1 取第 1 轮）。"""

    eval_id: str
    passed: bool
    final_status: str
    case_score: float  # 各 metric score 的均值（score=None 记 0）
    metric_scores: dict[str, Optional[float]] = field(default_factory=dict)
    metric_status: dict[str, str] = field(default_factory=dict)
    metric_reasons: dict[str, Optional[str]] = field(default_factory=dict)
    rubric_verdicts: dict[str, list[dict]] = field(default_factory=dict)  # metric -> [{id, score, reason}]
    actual_tool_calls: list[dict] = field(default_factory=list)  # [{"name", "args"}]
    expected_tool_calls: list[dict] = field(default_factory=list)
    actual_response: str = ""
    expected_response: str = ""


@dataclass
class SplitSummary:
    """一个数据切分（train/val）的汇总视图。"""

    pass_rate: float
    mean_case_score: float
    metric_breakdown: dict[str, float]
    total: int
    passed: int


def _text_of(content: Any) -> str:
    """Content.parts 里的纯文本拼接。"""
    if content is None or not getattr(content, "parts", None):
        return ""
    return "\n".join((p.text or "") for p in content.parts if getattr(p, "text", None)).strip()


def _tool_calls_of(invocation: Any) -> list[dict]:
    """Invocation.intermediate_data 里的工具调用列表 → [{"name","args"}]。"""
    if invocation is None:
        return []
    calls = get_all_tool_calls(getattr(invocation, "intermediate_data", None))
    return [{"name": c.name, "args": dict(c.args or {})} for c in calls]


def _record_from_case_result(case_result: Any) -> CaseEvalRecord:
    """把框架的 EvalCaseResult 压平成 CaseEvalRecord。"""
    metric_scores: dict[str, Optional[float]] = {}
    metric_status: dict[str, str] = {}
    metric_reasons: dict[str, Optional[str]] = {}
    rubric_verdicts: dict[str, list[dict]] = {}

    for m in case_result.overall_eval_metric_results:
        metric_scores[m.metric_name] = m.score
        metric_status[m.metric_name] = m.eval_status.name
        details = m.details
        metric_reasons[m.metric_name] = details.reason if details is not None else None
        if details is not None and details.rubric_scores:
            rubric_verdicts[m.metric_name] = [{
                "id": getattr(r, "id", ""),
                "score": getattr(r, "score", None),
                "reason": getattr(r, "reason", ""),
            } for r in details.rubric_scores]

    actual_tool_calls: list[dict] = []
    expected_tool_calls: list[dict] = []
    actual_response = ""
    expected_response = ""
    if case_result.eval_metric_result_per_invocation:
        # 本 example 的 case 均为单 invocation；多轮对话取首轮即可满足归因需要
        per_inv = case_result.eval_metric_result_per_invocation[0]
        actual_tool_calls = _tool_calls_of(per_inv.actual_invocation)
        expected_tool_calls = _tool_calls_of(per_inv.expected_invocation)
        actual_response = _text_of(getattr(per_inv.actual_invocation, "final_response", None))
        if per_inv.expected_invocation is not None:
            expected_response = _text_of(getattr(per_inv.expected_invocation, "final_response", None))

    scores = [(s if s is not None else 0.0) for s in metric_scores.values()]
    return CaseEvalRecord(
        eval_id=case_result.eval_id,
        passed=case_result.final_eval_status == EvalStatus.PASSED,
        final_status=case_result.final_eval_status.name,
        case_score=(sum(scores) / len(scores)) if scores else 0.0,
        metric_scores=metric_scores,
        metric_status=metric_status,
        metric_reasons=metric_reasons,
        rubric_verdicts=rubric_verdicts,
        actual_tool_calls=actual_tool_calls,
        expected_tool_calls=expected_tool_calls,
        actual_response=actual_response,
        expected_response=expected_response,
    )


async def run_eval(
    dataset_path: str,
    eval_config_path: str,
    *,
    agent_module: Optional[str] = "loop_agent",
) -> dict[str, CaseEvalRecord]:
    """跑一个数据集，返回 eval_id → CaseEvalRecord。

    Args:
        dataset_path: evalset JSON 路径。
        eval_config_path: 共享 metric 配置（验收套件）。
        agent_module: 被评 agent 的模块名；传 ``None`` 表示数据集是纯
            trace 模式（预录轨迹回放，不执行 agent）。
    """
    executer = AgentEvaluator.get_executer(
        dataset_path,
        agent_module=agent_module,
        eval_metrics_file_path_or_dir=eval_config_path,
        print_detailed_results=False,
        print_summary_report=False,
    )
    try:
        await executer.evaluate()
    except _EvaluationCasesFailed:
        pass  # 结果已填好，失败信息由报告呈现；其它 AssertionError 照常抛出
    result = executer.get_result()
    if result is None:  # pragma: no cover - evaluate() 非断言异常时才可能
        raise RuntimeError(f"evaluation produced no result for {dataset_path}")

    records: dict[str, CaseEvalRecord] = {}
    for agg in result.results_by_eval_set_id.values():
        for eval_id, case_results in agg.eval_results_by_eval_id.items():
            records[eval_id] = _record_from_case_result(case_results[0])
    return records


def summarize(records: dict[str, CaseEvalRecord]) -> SplitSummary:
    """聚合一个切分的通过率 / 平均 case 分 / 各 metric 平均分。"""
    total = len(records)
    passed = sum(1 for r in records.values() if r.passed)
    metric_sums: dict[str, float] = {}
    metric_counts: dict[str, int] = {}
    for record in records.values():
        for name, score in record.metric_scores.items():
            metric_sums[name] = metric_sums.get(name, 0.0) + (score if score is not None else 0.0)
            metric_counts[name] = metric_counts.get(name, 0) + 1
    return SplitSummary(
        pass_rate=(passed / total) if total else 0.0,
        mean_case_score=(sum(r.case_score for r in records.values()) / total) if total else 0.0,
        metric_breakdown={name: metric_sums[name] / metric_counts[name]
                          for name in sorted(metric_sums)},
        total=total,
        passed=passed,
    )
