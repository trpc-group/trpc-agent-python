# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""阶段②：失败归因 —— 把失败 case 聚类成六种失败类型。

六类失败类型（issue 需求 2 原文对应）：

===================== ==============================
final_answer_mismatch  最终回复不匹配
wrong_tool_call        工具调用错误（漏调/多调/调错工具）
wrong_tool_args        工具参数错误（工具对了、参数不对）
llm_rubric_fail        LLM rubric 不达标
knowledge_recall_miss  知识召回不足
format_violation       格式不符合要求
===================== ==============================

归因规则是**通用的**（只依赖框架 metric 结果的结构，不依赖本 example 的
具体 case），隐藏样本上同样适用：

1. ``tool_trajectory_avg_score`` 失败 → 比较实际/期望调用的**名字多重集**：
   名字集合不同（漏调/多调/调错）→ ``wrong_tool_call``；名字一致但参数
   不同 → ``wrong_tool_args``。若漏调的工具是知识检索工具（默认
   ``knowledge_search``）→ 追加一条 ``knowledge_recall_miss``。
2. ``llm_rubric_knowledge_recall`` 失败 → ``knowledge_recall_miss``
   （证据 = 未通过的 rubric id 与理由）。
3. ``final_response_avg_score`` 失败 → 若期望回答是结构化 JSON（对象/数组，
   裸标量如 ``42``/``true`` 不算）而实际回答不是 → ``format_violation``
   （要求结构化输出而给了自由文本，是最常见的格式违规）；否则
   ``final_answer_mismatch``。
4. ``llm_rubric_response`` 失败 → ``llm_rubric_fail``（证据 = 未通过的
   rubric id 与理由）。

主要归因（primary）按严重度优先级取第一个：
wrong_tool_call > wrong_tool_args > knowledge_recall_miss >
format_violation > llm_rubric_fail > final_answer_mismatch
（轨迹错误在链路上游、通常是根因，故优先级最高。）

兜底保证「每个失败 case 至少一个可解释原因」：以上规则都没命中时，任何
FAILED metric 都会映射成一条 finding（附 metric 失败理由）。
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal, Optional

from .evaluate import CaseEvalRecord

FailureType = Literal[
    "wrong_tool_call",
    "wrong_tool_args",
    "knowledge_recall_miss",
    "format_violation",
    "llm_rubric_fail",
    "final_answer_mismatch",
]

# 主要归因的优先级（越靠前越接近根因）
FAILURE_TYPE_PRECEDENCE: tuple[FailureType, ...] = (
    "wrong_tool_call",
    "wrong_tool_args",
    "knowledge_recall_miss",
    "format_violation",
    "llm_rubric_fail",
    "final_answer_mismatch",
)

FAILURE_TYPE_LABELS_ZH: dict[str, str] = {
    "wrong_tool_call": "工具调用错误",
    "wrong_tool_args": "工具参数错误",
    "knowledge_recall_miss": "知识召回不足",
    "format_violation": "格式不符合要求",
    "llm_rubric_fail": "LLM rubric 不达标",
    "final_answer_mismatch": "最终回复不匹配",
}

# 知识检索类工具名（与 eval 配置的 knowledge_tool_names 保持一致）
DEFAULT_KNOWLEDGE_TOOLS = frozenset({"knowledge_search"})

_METRIC_FALLBACK_TYPE: dict[str, FailureType] = {
    "tool_trajectory_avg_score": "wrong_tool_call",
    "final_response_avg_score": "final_answer_mismatch",
    "llm_rubric_response": "llm_rubric_fail",
    "llm_rubric_knowledge_recall": "knowledge_recall_miss",
}


@dataclass
class FailureFinding:
    """一条可解释的失败归因。"""

    type: FailureType
    metric: str
    evidence: str
    explanation: str  # 中文可读说明


@dataclass
class AttributionSummary:
    """一个切分（或全体）失败归因的聚类视图。"""

    counts: dict[str, int] = field(default_factory=dict)  # 失败类型 → case 数（按出现的 case 去重计数）
    per_case: dict[str, list[FailureFinding]] = field(default_factory=dict)
    primary: dict[str, str] = field(default_factory=dict)  # eval_id → 主要失败类型


def _truncate(text: str, limit: int = 120) -> str:
    text = (text or "").replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def _is_structured_json(text: str) -> bool:
    """期望「结构化输出」仅指 JSON 对象/数组；裸标量（'42'、'true'）是普通答案。"""
    try:
        return isinstance(json.loads(text), (dict, list))
    except (ValueError, TypeError):
        return False


def _failed(record: CaseEvalRecord, metric: str) -> bool:
    return record.metric_status.get(metric) == "FAILED"


def _failing_rubrics(record: CaseEvalRecord, metric: str) -> list[dict]:
    return [r for r in record.rubric_verdicts.get(metric, []) if (r.get("score") or 0.0) < 1.0]


def _fmt_calls(calls: list[dict]) -> str:
    if not calls:
        return "（无调用）"
    return "; ".join(f"{c['name']}({json.dumps(c['args'], ensure_ascii=False)})" for c in calls)


def attribute_case(
    record: CaseEvalRecord,
    knowledge_tools: frozenset[str] = DEFAULT_KNOWLEDGE_TOOLS,
) -> list[FailureFinding]:
    """对一条失败 case 产出 ≥1 条归因；通过的 case 返回空列表。"""
    if record.passed:
        return []
    findings: list[FailureFinding] = []

    # 规则 1：工具轨迹
    if _failed(record, "tool_trajectory_avg_score"):
        actual_names = Counter(c["name"] for c in record.actual_tool_calls)
        expected_names = Counter(c["name"] for c in record.expected_tool_calls)
        evidence = f"期望 {_fmt_calls(record.expected_tool_calls)}，实际 {_fmt_calls(record.actual_tool_calls)}"
        if actual_names != expected_names:
            missing = list((expected_names - actual_names).elements())
            extra = list((actual_names - expected_names).elements())
            detail_parts = []
            if missing:
                detail_parts.append(f"缺少调用：{'、'.join(missing)}")
            if extra:
                detail_parts.append(f"多余调用：{'、'.join(extra)}")
            findings.append(
                FailureFinding(
                    type="wrong_tool_call",
                    metric="tool_trajectory_avg_score",
                    evidence=evidence,
                    explanation="工具调用集合与期望不一致（" + ("；".join(detail_parts) or "调用了错误的工具") + "）",
                ))
            if any(name in knowledge_tools for name in missing):
                findings.append(
                    FailureFinding(
                        type="knowledge_recall_miss",
                        metric="tool_trajectory_avg_score",
                        evidence=evidence,
                        explanation=f"缺少知识检索调用（{'、'.join(n for n in missing if n in knowledge_tools)}），"
                        "无法召回作答所需知识",
                    ))
        else:
            findings.append(
                FailureFinding(
                    type="wrong_tool_args",
                    metric="tool_trajectory_avg_score",
                    evidence=evidence,
                    explanation="工具选择正确，但调用参数与期望不一致",
                ))

    # 规则 2：知识召回 rubric
    if _failed(record, "llm_rubric_knowledge_recall"):
        failing = _failing_rubrics(record, "llm_rubric_knowledge_recall")
        ids = "、".join(r.get("id", "?") for r in failing) or "(未提供 rubric 明细)"
        reasons = "；".join(_truncate(r.get("reason", "")) for r in failing)
        findings.append(
            FailureFinding(
                type="knowledge_recall_miss",
                metric="llm_rubric_knowledge_recall",
                evidence=f"未通过 rubric：{ids}。{reasons}",
                explanation="知识召回不足：检索结果无法支撑作答所需的关键信息",
            ))

    # 规则 3：最终回复精确匹配
    if _failed(record, "final_response_avg_score"):
        evidence = (f"期望「{_truncate(record.expected_response)}」，"
                    f"实际「{_truncate(record.actual_response)}」")
        if _is_structured_json(record.expected_response) and not _is_structured_json(record.actual_response):
            findings.append(
                FailureFinding(
                    type="format_violation",
                    metric="final_response_avg_score",
                    evidence=evidence,
                    explanation="格式不符合要求：期望结构化 JSON 输出，实际是自由文本",
                ))
        else:
            findings.append(
                FailureFinding(
                    type="final_answer_mismatch",
                    metric="final_response_avg_score",
                    evidence=evidence,
                    explanation="最终回复与参考答案不匹配",
                ))

    # 规则 4：回答质量 rubric
    if _failed(record, "llm_rubric_response"):
        failing = _failing_rubrics(record, "llm_rubric_response")
        ids = "、".join(r.get("id", "?") for r in failing) or "(未提供 rubric 明细)"
        reasons = "；".join(_truncate(r.get("reason", "")) for r in failing)
        findings.append(
            FailureFinding(
                type="llm_rubric_fail",
                metric="llm_rubric_response",
                evidence=f"未通过 rubric：{ids}。{reasons}",
                explanation="LLM rubric 评审不达标",
            ))

    # 兜底：保证每个失败 case 至少一条可解释归因
    if not findings:
        for metric, status in record.metric_status.items():
            if status != "FAILED":
                continue
            findings.append(
                FailureFinding(
                    type=_METRIC_FALLBACK_TYPE.get(metric, "final_answer_mismatch"),
                    metric=metric,
                    evidence=_truncate(record.metric_reasons.get(metric) or "metric 评分未达阈值"),
                    explanation=f"metric {metric} 未达阈值",
                ))
    if not findings:  # 理论上不可达：case FAILED 必有 FAILED metric
        findings.append(
            FailureFinding(
                type="final_answer_mismatch",
                metric="(unknown)",
                evidence="case 标记为 FAILED 但无 metric 明细",
                explanation="评测框架未提供 metric 明细，按最终回复不匹配处理",
            ))
    return findings


def primary_type(findings: list[FailureFinding]) -> Optional[str]:
    """按优先级取主要失败类型。"""
    present = {f.type for f in findings}
    for failure_type in FAILURE_TYPE_PRECEDENCE:
        if failure_type in present:
            return failure_type
    return None


def cluster(
    records: dict[str, CaseEvalRecord],
    knowledge_tools: frozenset[str] = DEFAULT_KNOWLEDGE_TOOLS,
) -> AttributionSummary:
    """对一批 case 聚类归因；counts 按「出现该类型的 case 数」计。"""
    summary = AttributionSummary()
    type_counter: Counter[str] = Counter()
    for eval_id in sorted(records):
        findings = attribute_case(records[eval_id], knowledge_tools)
        if not findings:
            continue
        summary.per_case[eval_id] = findings
        primary = primary_type(findings)
        if primary is not None:
            summary.primary[eval_id] = primary
        for failure_type in {f.type for f in findings}:
            type_counter[failure_type] += 1
    summary.counts = {t: type_counter[t] for t in FAILURE_TYPE_PRECEDENCE if type_counter[t]}
    return summary
