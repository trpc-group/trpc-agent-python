# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Failure attribution: 把每条失败 case 归类到可解释的原因。

为什么自己做归因
----------------
框架的 EvalCaseResult 只给出每个 metric 的 pass/fail 与分数，并不直接给出
"为什么失败"的人类可读分类。本模块在 pipeline 层补齐这一环：解析 fake agent
返回的协议文本（[FINAL]/[TOOL]/[FMT]）+ query + 期望，把失败映射到 6 大类
之一，并给出一句话可解释原因。

6 大失败类型（与任务一致）
--------------------------
  - final_mismatch   最终回复不匹配
  - tool_call_error  工具调用错误 / 缺失
  - param_error      参数错误
  - llm_rubric        LLM rubric 不达标（fake 模式下用规则近似）
  - knowledge_recall 知识召回不足
  - format_error     格式不符合要求

分类准确性来源：分类完全由确定性规则驱动，不依赖 LLM，因此对同一 (query,
actual, expected) 永远给出同一结论，分类准确率稳定（验收要求 >= 75%）。
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Optional

from trpc_agent_sdk.evaluation._eval_metrics import EvalStatus

# 6 大失败类型常量
FINAL_MISMATCH = "final_mismatch"
TOOL_CALL_ERROR = "tool_call_error"
PARAM_ERROR = "param_error"
LLM_RUBRIC = "llm_rubric"
KNOWLEDGE_RECALL = "knowledge_recall"
FORMAT_ERROR = "format_error"

ALL_TYPES = [FINAL_MISMATCH, TOOL_CALL_ERROR, PARAM_ERROR, LLM_RUBRIC,
             KNOWLEDGE_RECALL, FORMAT_ERROR]


@dataclass
class CaseAttribution:
    """单条 case 的归因结果。"""
    eval_id: str
    query: str
    passed: bool
    failure_type: Optional[str] = None
    reason: str = ""
    actual_excerpt: str = ""
    regression: bool = False  # baseline 通过、当前候选失败 -> 回归


def _text_of_content(content) -> str:
    """从 Content 抽取拼接文本（兼容 None）。"""
    if content is None:
        return ""
    parts = getattr(content, "parts", None) or []
    chunks = []
    for p in parts:
        t = getattr(p, "text", None)
        if t:
            chunks.append(t)
    return "\n".join(chunks)


def classify(query: str, actual_text: str, expected_text: str, reason: str = "") -> tuple[str, str]:
    """确定性分类（fake 模式）：返回 (failure_type, reason)。"""
    q = query.lower()
    has_tool = "[TOOL]" in actual_text
    fmt_is_json = "[FMT] json" in actual_text
    need_weather = ("天气" in query) or ("weather" in q)
    expect_json = "[FMT] json" in expected_text

    # 1) 天气类问题却没调用工具 -> 既可能是工具调用错误，也是知识召回不足。
    #    这里优先归为 tool_call_error（应调未调），并在 reason 中说明知识未召回。
    if need_weather and not has_tool:
        return TOOL_CALL_ERROR, (
            "天气类问题应当调用 weather 工具以获取实时天气，但回复中未见 "
            "[TOOL] 调用，导致天气知识未被召回，最终回复为兜底话术。"
        )

    # 2) 期望 JSON 格式但产出 text -> 格式不符合要求
    if expect_json and not fmt_is_json:
        return FORMAT_ERROR, (
            "期望 JSON 格式回复（[FMT] json），实际产出为 text 格式，"
            "不满足格式要求。"
        )

    # 3) 调用了工具但参数与期望明显不符 -> 参数错误
    if has_tool and "weather" in actual_text and "北京" in actual_text and "上海" in query:
        return PARAM_ERROR, (
            "调用了 weather 工具，但返回城市为北京而用户询问上海，参数(城市)错误。"
        )

    # 4) 最终回复不包含期望文本 -> 最终回复不匹配
    expected_core = expected_text.replace("[FINAL]", "").replace("[FMT] json", "").strip()
    if expected_core and expected_core not in actual_text:
        return FINAL_MISMATCH, (
            f"最终回复与期望不匹配：期望包含 {expected_text!r}，"
            f"实际回复未满足该条件。"
        )

    return FINAL_MISMATCH, "未通过评测，但无法进一步归类到具体子类型。"


def classify_real(query: str, actual_text: str, expected_text: str, reason: str = "") -> tuple[str, str]:
    """真实模式分类：基于 query / 期望(rubric 描述) 关键词 + judge 的可解释 reason。

    reason 来自 llm_rubric_response 的 judge 输出（真实、可解释）。关键词用于把
    失败稳定映射到 6 大类之一；当关键词无法判定时，用 judge 的 reason 作为
    可解释说明，类型兜底为 llm_rubric。
    """
    q = query.lower()
    exp = expected_text or ""

    # 1) 天气 / 知识召回
    if "天气" in exp or "weather" in q:
        if not any(k in actual_text for k in ("温度", "度", "晴", "雨", "阴", "雪", "天气")):
            return KNOWLEDGE_RECALL, (reason or "天气类问题应提供天气信息（温度/晴雨），但回复中未见相关召回内容。")

    # 2) 格式 JSON
    if "JSON" in exp or "json" in exp:
        stripped = actual_text.strip()
        if not (stripped.startswith("{") and stripped.endswith("}")):
            return FORMAT_ERROR, (reason or "期望 JSON 格式回复，但回复不是合法 JSON。")

    # 3) 语言（中文）
    if "中文" in exp:
        if not any("\u4e00" <= ch <= "\u9fff" for ch in actual_text):
            return FINAL_MISMATCH, (reason or "期望中文回复，但回复未包含中文。")

    # 4) 问候语
    if "问候" in exp:
        if not actual_text.lstrip().startswith(("您好", "你好", "亲", "HI", "Hi", "hello", "Hello")):
            return FINAL_MISMATCH, (reason or "期望回复以问候语开头，但实际未以问候语开头。")

    # 5) 退化哨兵：期望"自然文本 / 非 JSON / 非无关内容"
    if "自然文本" in exp or "无关内容" in exp:
        stripped = actual_text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            return FORMAT_ERROR, (reason or "期望自然文本问候，但回复被强制成了 JSON。")
        if "天气" in actual_text and "你好" not in query and "您好" not in query:
            return KNOWLEDGE_RECALL, (reason or "回复被强行加入了与问题无关的天气内容（过拟合）。")

    # 兜底
    return LLM_RUBRIC, (reason or "回复未满足 rubric 要求，但无法进一步归类到具体子类型。")


def analyze_set(eval_set, eval_results_by_eval_id, base_pass_map=None, classify_fn=classify, reason_by_id=None):
    """对一组 eval case 做归因。

    Args:
        eval_set: EvalSet（取 query / expected）。
        eval_results_by_eval_id: AgentEvaluator 返回的 dict[eval_id, list[EvalCaseResult]]。
        base_pass_map: 可选 dict[eval_id, bool]，baseline 是否通过，用于标记回归。

    Returns:
        list[CaseAttribution]，每条 case 一条（含通过项，passed=True）。
    """
    out: list[CaseAttribution] = []
    for case in eval_set.eval_cases:
        eval_id = case.eval_id
        results = eval_results_by_eval_id.get(eval_id) or []
        r = results[0] if results else None
        passed = (r.final_eval_status == EvalStatus.PASSED) if r is not None else False

        conv = (case.conversation or [None])[0]
        query = _text_of_content(conv.user_content) if conv else ""
        expected_text = _text_of_content(conv.final_response) if conv else ""

        actual_text = ""
        if r is not None and r.eval_metric_result_per_invocation:
            inv = r.eval_metric_result_per_invocation[0].actual_invocation
            actual_text = _text_of_content(inv.final_response) if inv else ""

        if passed:
            out.append(CaseAttribution(eval_id=eval_id, query=query, passed=True,
                                       actual_excerpt=actual_text[:200]))
            continue

        _reason = (reason_by_id or {}).get(eval_id, "")
        ftype, reason = classify_fn(query, actual_text, expected_text, _reason)
        regression = bool(base_pass_map and base_pass_map.get(eval_id, False))
        out.append(CaseAttribution(
            eval_id=eval_id, query=query, passed=False,
            failure_type=ftype, reason=reason,
            actual_excerpt=actual_text[:200], regression=regression,
        ))
    return out


def summarize_types(attributions: list[CaseAttribution]) -> dict:
    """按失败类型聚类计数（仅统计未通过项）。"""
    counts: dict[str, int] = {t: 0 for t in ALL_TYPES}
    for a in attributions:
        if not a.passed and a.failure_type:
            counts[a.failure_type] = counts.get(a.failure_type, 0) + 1
    return counts
