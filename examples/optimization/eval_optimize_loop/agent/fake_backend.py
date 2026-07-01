# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""确定性 fake agent 后端：无 API Key 也能跑通完整 pipeline。

设计目标
--------
issue 要求 fake / trace 模式下 3 分钟内跑通闭环，并且能稳定复现三类场景：
可优化成功、优化无效、优化后退化（过拟合）。为此本模块提供一个**不依赖
任何真实 LLM** 的求解器：它只从当前 prompt 文件里解析「能力标记」（``@cap:``
注释行），据此决定这次能不能解题、格式对不对。

于是"改 prompt"这个动作被映射成"改能力集合"，从而让每条 case 的 pass/fail
可以随 prompt 候选确定性翻转——这正是演示评测→优化闭环所需要的可控信号，
同时又完全离线、可复现（固定 seed 无关，无随机）。

能力标记（写在 prompts/*.md 里的 ``<!-- @cap: X -->``）
------------------------------------------------------
- ``op-add`` / ``op-mul`` / ``op-discount`` : 求解器掌握的运算（一般放在 skill.md）
- ``fmt-answer-prefix``                      : 最终答复以「答案：」开头（system.md）
- ``fmt-unit-suffix``                        : 数字后带单位（system.md）
- ``route-ok``                               : 路由器能正确分流（router.md）
- ``assume-mul-default``                     : **过拟合副作用**——对含大操作数(>=10)
                                               的加法题过度使用乘法，故意制造回归

真实模式请改用 :mod:`agent.orchestrator`（真正的多 agent + LlmAgent）。
"""

from __future__ import annotations

import re
from pathlib import Path


_CAP_RE = re.compile(r"@cap:\s*([a-z0-9\-]+)", re.IGNORECASE)
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")

REFUSAL = "抱歉，我暂时无法解答这道题。"


def read_caps(*prompt_paths: Path) -> set[str]:
    """从若干 prompt 文件里解析全部能力标记，合成一个能力集合。"""
    caps: set[str] = set()
    for path in prompt_paths:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        caps.update(m.group(1).lower() for m in _CAP_RE.finditer(text))
    return caps


def _detect_operation(query: str) -> str:
    """从题面关键词判断运算类型：discount / mul / add。"""
    if "折" in query:
        return "discount"
    if "每" in query:  # “每小时”“每盒” 这类单价/速率题 → 乘法
        return "mul"
    return "add"


def _detect_unit(query: str) -> str:
    """从题面关键词判断单位。顺序敏感：先匹配更具体的单位。"""
    if "公里" in query:
        return "公里"
    if "元" in query:
        return "元"
    if any(k in query for k in ("人", "男生", "女生", "名")):
        return "人"
    return "个"


def _format_number(value: float) -> str:
    """整数值去掉小数尾巴：150.0 -> '150'。"""
    if value == int(value):
        return str(int(value))
    return str(value)


def solve(query: str, caps: set[str]) -> str:
    """确定性求解：根据能力集合返回最终答复文本。

    这是 fake agent 的全部"智能"。改动 prompt（即改动 ``caps``）会确定性地
    改变返回值，从而让评测分数随候选 prompt 翻转。
    """
    operation = _detect_operation(query)
    unit = _detect_unit(query)
    numbers = [float(n) for n in _NUM_RE.findall(query)]

    # 1) 能力缺失 → 如实拒答（映射到"知识召回不足"类失败）
    required_cap = {"add": "op-add", "mul": "op-mul", "discount": "op-discount"}[operation]
    if required_cap not in caps:
        return REFUSAL
    if len(numbers) < 2:
        return REFUSAL

    a, b = numbers[0], numbers[1]

    # 2) 计算数值
    if operation == "add":
        # 过拟合副作用：assume-mul-default 让求解器对含大操作数的加法题
        # 过度使用乘法 → 大数加法题被算错（制造验证集回归）。
        if "assume-mul-default" in caps and (a >= 10 or b >= 10):
            result = a * b
        else:
            result = a + b
    elif operation == "mul":
        result = a * b
    else:  # discount：原价 * 折数/10
        result = a * (b / 10.0)

    # 3) 套用格式
    body = _format_number(result)
    if "fmt-unit-suffix" in caps:
        body = f"{body} {unit}"
    if "fmt-answer-prefix" in caps:
        body = f"答案：{body}"
    return body


async def call_agent_fake(query: str, prompt_paths: dict[str, Path]) -> str:
    """框架回调（fake 版）：读当前 prompt 能力集合 → 确定性求解。

    与真实 ``call_agent`` 保持同签名（``query -> str``），由 pipeline 通过
    ``functools.partial`` 绑定 ``prompt_paths`` 后传入 AgentEvaluator。
    """
    caps = read_caps(*prompt_paths.values())
    return solve(query, caps)
