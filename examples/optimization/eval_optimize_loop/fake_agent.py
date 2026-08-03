# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Deterministic Fake Model / Fake Agent.

Why this exists
---------------
在没有 API Key 的情况下，pipeline 仍需要一个"模型"把 (system_prompt, query)
映射成回复。真实场景这个映射由 LLM 完成；这里用确定性规则替代，使整个
"评测 - 归因 - 优化 - 回归 - 审计"闭环可复现、可审计、3 分钟内跑完。

How the feedback loop works (the key idea)
------------------------------------------
优化器通过失败归因知道"哪个 case 缺什么能力"，于是在候选 prompt 中注入
自然语言指令（如"回答天气类问题时必须调用 weather 工具" / "用 JSON 格式回复"）。
本 fake agent 解析这些指令关键词来改变自身行为。于是：

    baseline prompt（无指令）  -> 行为弱 -> 某些 case 失败
    候选 prompt（注入指令）    -> 行为强 -> 这些 case 通过

这就是"优化提升"的确定性反馈信号，完全不需要 LLM。

Response protocol
-----------------
回复是结构化文本，便于 metric 匹配与失败归因解析：
    [FINAL] <最终回复文本>
    [TOOL]  <json 数组：工具调用声明，可空>
    [FMT]   <json|text>

框架的 response_match_score 对整个回复文本做精确匹配，因此"工具调用正确/
错误、格式正确/错误、回复正确/错误"会直接体现在 0/1 分数上；归因模块再解析
[FINAL]/[TOOL]/[FMT] 给出可解释原因（6 大失败类型之一）。
"""

from __future__ import annotations

import json
import re
from typing import Optional

# 候选 prompt 命中这些指令时才"授予"对应能力
WEATHER_TOOL = "weather"


def _prompt_allows_tool(prompt: str, tool: str) -> bool:
    """prompt 显式要求调用某工具时才授予权限（确定性关键词匹配）。"""
    return bool(re.search(rf"(must call|call the|调用|must use|use the)\s+{re.escape(tool)}", prompt, re.I))


def _prompt_wants_json(prompt: str) -> bool:
    """prompt 要求 JSON 格式回复时启用结构化输出。"""
    return "json" in prompt.lower() and re.search(r"respond|回复|format|格式|output", prompt, re.I) is not None


def decide(query: str, prompt: str) -> dict:
    """确定性决策：给定 query 与当前 system prompt，返回结构化决策。"""
    q = query.lower()
    tool_calls: list[dict] = []
    fmt = "text"

    # 过度优化信号：prompt 要求"对每一个/所有问题都调用 weather"，则强制走天气分支。
    # 这正是用来制造"训练集提升但验证集退化"的过拟合场景。
    force_weather = bool(re.search(r"每一个问题|所有问题|always|每次", prompt, re.I))
    is_weather = ("天气" in query) or ("weather" in q) or force_weather

    if is_weather:
        if _prompt_allows_tool(prompt, WEATHER_TOOL) or force_weather:
            tool_calls.append({"name": WEATHER_TOOL, "args": {"city": "北京"}})
            final = "北京今天晴，25 摄氏度，适宜外出。"
        else:
            # 未授权工具 -> 知识召回不足 / 工具调用缺失类失败
            final = "抱歉，我暂时无法获取天气信息。"
    elif "格式" in query or "json" in q:
        final = "这是一份示例结构化数据。"
    else:
        final = "我已收到您的请求。"

    if _prompt_wants_json(prompt):
        fmt = "json"
        final = json.dumps({"answer": final, "tools": tool_calls}, ensure_ascii=False)

    return {"final": final, "tool_calls": tool_calls, "fmt": fmt}


def render(decision: dict) -> str:
    """把决策渲染成协议文本。"""
    block = f"[FINAL] {decision['final']}\n"
    if decision["tool_calls"]:
        block += f"[TOOL] {json.dumps(decision['tool_calls'], ensure_ascii=False)}\n"
    block += f"[FMT] {decision['fmt']}\n"
    return block


# 模块级"当前生效 prompt"，由 pipeline 在每轮优化时更新。
_CURRENT_PROMPT = [""]


def set_prompt(text: str) -> None:
    """pipeline 写入本轮候选 prompt，使后续 call_agent 调用使用它。"""
    _CURRENT_PROMPT[0] = text or ""


def get_prompt() -> str:
    return _CURRENT_PROMPT[0]


async def call_agent(query: str) -> str:
    """框架回调：黑盒 agent。prompt 取自模块级当前 prompt。

    签名 (query: str) -> str 直接对接 AgentEvaluator / AgentOptimizer 的
    call_agent 协议，无需改动框架本身。
    """
    return render(decide(query, _CURRENT_PROMPT[0]))
