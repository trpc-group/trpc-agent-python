# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""评测阶段：调**真实** AgentEvaluator 对一个 evalset 打分，抽成结构化记录。

real / fake 两种模式在这里**共用同一套评测代码**——差别只在传进来的
``call_agent`` 是真实多 agent 还是确定性求解器。评测器、metric、pass/fail
判定完全一致，这样 fake 的分数与 real 的分数口径可比。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from trpc_agent_sdk.evaluation import AgentEvaluator
from trpc_agent_sdk.evaluation import EvalStatus
from trpc_agent_sdk.evaluation import get_all_tool_calls


CallAgent = Callable[[str], Awaitable[str]]


@dataclass
class MetricScore:
    """单个 metric 在单条 case 上的结果。"""

    name: str
    score: float
    passed: bool
    threshold: float
    reason: str = ""


@dataclass
class CaseEval:
    """单条 case 的评测结果（跨 metric 汇总）。"""

    eval_id: str
    passed: bool
    score: float  # 主 metric 分（这里是 final_response_avg_score）
    metrics: list[MetricScore] = field(default_factory=list)
    query: str = ""
    expected_text: str = ""
    actual_text: str = ""
    error: str = ""
    trajectory: list[str] = field(default_factory=list)  # 关键轨迹（工具调用 + 最终答复）


@dataclass
class SetEval:
    """一个 evalset 的整体评测结果。"""

    set_id: str
    cases: dict[str, CaseEval]

    @property
    def pass_count(self) -> int:
        return sum(1 for c in self.cases.values() if c.passed)

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def avg_score(self) -> float:
        if not self.cases:
            return 0.0
        return sum(c.score for c in self.cases.values()) / len(self.cases)


def _content_text(content) -> str:
    if content is None or not getattr(content, "parts", None):
        return ""
    return "".join(p.text for p in content.parts if getattr(p, "text", None)).strip()


def _load_expected(dataset_path: Path) -> dict[str, tuple[str, str]]:
    """从 evalset 文件读每条 case 的 (query, expected_text)，用于报告与归因。"""
    data = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    out: dict[str, tuple[str, str]] = {}
    for case in data.get("eval_cases", []):
        conv = case.get("conversation", [])
        if not conv:
            continue
        first = conv[0]
        query = "".join(p.get("text", "") for p in first.get("user_content", {}).get("parts", []))
        expected = "".join(p.get("text", "") for p in first.get("final_response", {}).get("parts", []))
        out[case["eval_id"]] = (query.strip(), expected.strip())
    return out


async def evaluate_set(
    dataset_path: Path,
    call_agent: CallAgent,
    metrics_path: Path,
    output_dir: Path,
) -> SetEval:
    """对单个 evalset 跑真实 AgentEvaluator，返回结构化 SetEval。"""
    executer = AgentEvaluator.get_executer(
        str(dataset_path),
        call_agent=call_agent,
        num_runs=1,
        print_detailed_results=False,
        eval_metrics_file_path_or_dir=str(metrics_path),
        eval_result_output_dir=str(output_dir),
    )
    # AgentEvaluator 在有 case 未达标时会抛 AssertionError(_EvaluationCasesFailed)，
    # 但抛出前已把完整结果写入 executer。我们要的是回归信号（含失败），因此捕获
    # 断言、照常取回结果——失败是评测的正常输出，不是流程错误。
    try:
        await executer.evaluate()
    except AssertionError:
        pass
    result = executer.get_result()

    expected_map = _load_expected(dataset_path)
    cases: dict[str, CaseEval] = {}

    for set_id, agg in (result.results_by_eval_set_id if result else {}).items():
        for eval_id, case_runs in agg.eval_results_by_eval_id.items():
            case = case_runs[0]  # num_runs=1
            metric_scores: list[MetricScore] = []
            for m in case.overall_eval_metric_results:
                metric_scores.append(
                    MetricScore(
                        name=m.metric_name,
                        score=float(m.score) if m.score is not None else 0.0,
                        passed=m.eval_status == EvalStatus.PASSED,
                        threshold=float(getattr(m, "threshold", 1.0) or 1.0),
                        reason=(m.details.reason if m.details and m.details.reason else ""),
                    )
                )
            # 抽取实际 agent 输出文本 + 关键轨迹（取第一个 invocation）
            actual_text = ""
            trajectory: list[str] = []
            if case.eval_metric_result_per_invocation:
                actual_inv = case.eval_metric_result_per_invocation[0].actual_invocation
                actual_text = _content_text(actual_inv.final_response)
                # 关键轨迹：记录每次工具调用（名称 + 参数摘要），再附最终答复。
                # 单 agent/无工具时轨迹只含最终答复；多 agent + 工具时可见调用链。
                for call in get_all_tool_calls(actual_inv.intermediate_data):
                    args = getattr(call, "args", None) or {}
                    trajectory.append(f"tool_call:{getattr(call, 'name', '?')}({args})")
                if actual_text:
                    trajectory.append(f"final_response:{actual_text}")
            query, expected = expected_map.get(eval_id, ("", ""))
            cases[eval_id] = CaseEval(
                eval_id=eval_id,
                passed=case.final_eval_status == EvalStatus.PASSED,
                score=metric_scores[0].score if metric_scores else 0.0,
                metrics=metric_scores,
                query=query,
                expected_text=expected,
                actual_text=actual_text,
                error=case.error_message or "",
                trajectory=trajectory,
            )

    # 兜底：若某 case 未出现在结果里，也补一条 failed 记录
    for eval_id, (query, expected) in expected_map.items():
        cases.setdefault(
            eval_id,
            CaseEval(eval_id=eval_id, passed=False, score=0.0, query=query, expected_text=expected),
        )

    return SetEval(set_id=Path(dataset_path).stem, cases=cases)
