# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""失败归因阶段：把每条失败 case 归入六大类之一，并给出可解释原因。

六大类（对齐 issue）::

    最终回复不匹配 / 工具调用错误 / 参数错误 /
    LLM rubric 不达标 / 知识召回不足 / 格式不符合要求

两种后端
--------
- real（纯 LLM 裁判，issue 指定）：用一个 LlmAgent 裁判读『题面/期望/实际』
  输出 JSON 分类；语义最灵活。
- fake（离线确定性桩）：从『期望文本 vs 实际文本 vs 运行错误』用规则确定性
  判类。保证无 key 时归因结果稳定、可复现（验收第 4 条要求分类准确率与可解释性）。

无论哪种后端，输出结构一致：{eval_id: Attribution}，并可聚类成类别计数。
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Optional

from .evaluate import CaseEval


# 六大失败类别（value 为报告中展示的中文标签）
CATEGORIES = {
    "final_response_mismatch": "最终回复不匹配",
    "tool_call_error": "工具调用错误",
    "param_error": "参数错误",
    "llm_rubric_fail": "LLM rubric 不达标",
    "knowledge_gap": "知识召回不足",
    "format_error": "格式不符合要求",
}

_REFUSAL_MARK = "无法解答"
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass
class Attribution:
    eval_id: str
    category: str          # CATEGORIES 的 key
    category_label: str    # 中文标签
    reason: str            # 可解释原因（每条失败至少一条）
    source: str            # "fake" | "llm"


def _first_number(text: str) -> Optional[str]:
    m = _NUM_RE.search(text or "")
    return m.group(0) if m else None


def classify_fake(case: CaseEval) -> Attribution:
    """确定性归因：仅凭已有评测信号（期望/实际/错误）判类。"""
    actual = case.actual_text or ""
    expected = case.expected_text or ""

    if case.error:
        cat, reason = "tool_call_error", f"运行期报错，链路未产出答复：{case.error[:80]}"
    elif not actual:
        cat, reason = "final_response_mismatch", "agent 未产出任何最终答复文本。"
    elif _REFUSAL_MARK in actual:
        cat, reason = "knowledge_gap", "agent 声明无法解答，说明缺少对应题型的解题能力（技能/知识缺口）。"
    else:
        exp_num, act_num = _first_number(expected), _first_number(actual)
        if exp_num is not None and act_num is not None and exp_num != act_num:
            cat, reason = "param_error", f"计算结果数值错误：期望 {exp_num}，实际 {act_num}（运算或取数有误）。"
        elif expected and expected not in actual:
            # 数字对得上，但整体串不匹配 → 多半是格式（缺「答案：」前缀或单位）
            cat, reason = "format_error", f"数值正确但格式不符：期望包含『{expected}』，实际输出『{actual}』。"
        else:
            cat, reason = "final_response_mismatch", f"最终答复与期望不一致：期望『{expected}』，实际『{actual}』。"

    return Attribution(case.eval_id, cat, CATEGORIES[cat], reason, source="fake")


_JUDGE_INSTRUCTION = (
    "你是评测失败归因裁判。给定一道题的『题面/期望答案/agent 实际答复』，"
    "把这次失败归入且仅归入以下六类之一，并给出一句可解释原因。\n"
    "类别（用括号里的英文 key）：最终回复不匹配(final_response_mismatch)、"
    "工具调用错误(tool_call_error)、参数错误(param_error)、"
    "LLM rubric 不达标(llm_rubric_fail)、知识召回不足(knowledge_gap)、"
    "格式不符合要求(format_error)。\n"
    "只输出 JSON：{\"category\": \"<key>\", \"reason\": \"<一句话>\"}，不要多余文字。"
)


async def classify_llm(case: CaseEval) -> Attribution:
    """纯 LLM 裁判归因（real 模式）。失败时回退到 fake 规则，保证 pipeline 不中断。"""
    try:
        from trpc_agent_sdk.agents import LlmAgent
        from trpc_agent_sdk.models import OpenAIModel
        from trpc_agent_sdk.runners import Runner
        from trpc_agent_sdk.sessions import InMemorySessionService
        from trpc_agent_sdk.types import Content, GenerateContentConfig, Part

        from agent.config import get_model_config

        api_key, base_url, model_name = get_model_config()
        judge = LlmAgent(
            name="attribution_judge",
            description="failure attribution judge",
            model=OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url),
            instruction=_JUDGE_INSTRUCTION,
            generate_content_config=GenerateContentConfig(temperature=0.0, max_output_tokens=256),
        )
        session_service = InMemorySessionService()
        runner = Runner(app_name="eol_attribution", agent=judge, session_service=session_service)
        session_id, user_id = str(uuid.uuid4()), "judge"
        await session_service.create_session(
            app_name="eol_attribution", user_id=user_id, session_id=session_id, state={},
        )
        prompt = (
            f"题面：{case.query}\n期望答案：{case.expected_text}\n"
            f"agent 实际答复：{case.actual_text or '(空)'}\n运行错误：{case.error or '无'}"
        )
        content = Content(role="user", parts=[Part.from_text(text=prompt)])
        out = ""
        async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=content):
            if event.is_final_response() and event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text and not part.thought:
                        out += part.text
        parsed = json.loads(re.search(r"\{.*\}", out, re.DOTALL).group(0))
        cat = parsed.get("category", "final_response_mismatch")
        if cat not in CATEGORIES:
            cat = "final_response_mismatch"
        reason = str(parsed.get("reason", "")).strip() or "(裁判未给出原因)"
        return Attribution(case.eval_id, cat, CATEGORIES[cat], reason, source="llm")
    except Exception as exc:  # noqa: BLE001 - 归因失败不应中断闭环
        fallback = classify_fake(case)
        fallback.reason = f"[LLM 裁判失败回退规则] {fallback.reason}（原因：{exc.__class__.__name__}）"
        return fallback


async def attribute_failures(set_eval, mode: str) -> dict:
    """对一个 SetEval 里的所有失败 case 归因，返回明细 + 类别聚类计数。"""
    attributions: dict[str, Attribution] = {}
    for eval_id, case in set_eval.cases.items():
        if case.passed:
            continue
        if mode == "real":
            attributions[eval_id] = await classify_llm(case)
        else:
            attributions[eval_id] = classify_fake(case)

    clusters: dict[str, int] = {}
    for attr in attributions.values():
        clusters[attr.category_label] = clusters.get(attr.category_label, 0) + 1

    return {"attributions": attributions, "clusters": clusters}
