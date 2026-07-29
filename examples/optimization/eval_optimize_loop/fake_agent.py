#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2025 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Milestone A 的 fake agent。

行为**完全由 prompt 中公开、明确的 FAKE_CONTROLS 控制块**决定：

    <!-- FAKE_CONTROLS
    ADD_STEPS=true
    MEMORIZE_TRAIN=false
    -->

只有该块内、且值严格等于 ``true``（大小写敏感）才启用对应行为。
注释文字、文档叙述、示例代码里出现的同名关键字**不会**误触发。

决策矩阵（与 README 真值表一致）：

| 控制块                          | train case | val protected | val 其他 |
|---------------------------------|------------|---------------|----------|
| 全 false（baseline）            | 错         | 对            | 错       |
| ADD_STEPS=true                  | 对         | 对            | 对       |
| MEMORIZE_TRAIN=true             | 对         | 错            | 错       |

- "对" = 输出 evalset 中该 case 的 ``final_response`` 原文（满足 contains 评测）
- "错" = 输出固定错误文本

成功 case 的输出**直接来自 evalset 中 case.conversation[0].final_response**，
避免与 contains 评测再次漂移。

禁止行为（test_no_sha_switch 强制）：
  - 通过任何形式的哈希指纹选择预录答案
  - 通过身份关键字偏置结果
  - 读取 scenario 参数 / eval_id 内容决定答案
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# ---- FAKE_CONTROLS 控制块解析 ----
# 多行块：<!-- FAKE_CONTROLS ... -->  之间按 `KEY=value` 行解析
_FAKE_CONTROLS_RE = re.compile(
    r"<!--\s*FAKE_CONTROLS\b(.*?)-->",
    re.DOTALL | re.IGNORECASE,
)
_KV_RE = re.compile(r"^\s*([A-Z_]+)\s*=\s*(\S+)\s*$")


@dataclass(frozen=True)
class FakeBehavior:
    """根据 FAKE_CONTROLS 块推断出的行为集合。

    只有严格等于字符串 "true" 才视为启用，避免 "false" / "True" / 注释文字误触发。
    """

    add_steps: bool = False
    memorize_train: bool = False


def parse_behavior(prompt_text: str) -> FakeBehavior:
    """从 prompt 文本中提取 FAKE_CONTROLS 块并解析。

    - 不存在控制块 → baseline（全 false）
    - 存在控制块 → 仅接受 ``KEY=true``（严格小写 true）作为启用
    - 其他值（false / True / 注释叙述）一律视为 false

    >>> parse_behavior("no block here").add_steps
    False
    >>> parse_behavior("<!-- FAKE_CONTROLS\\nADD_STEPS=true\\n-->").add_steps
    True
    >>> parse_behavior("<!-- FAKE_CONTROLS\\nADD_STEPS=false\\n-->").add_steps
    False
    """
    add_steps = False
    memorize_train = False
    for match in _FAKE_CONTROLS_RE.finditer(prompt_text or ""):
        body = match.group(1)
        for line in body.splitlines():
            kv = _KV_RE.match(line)
            if not kv:
                continue
            key, val = kv.group(1), kv.group(2)
            enabled = val == "true"
            if key == "ADD_STEPS":
                add_steps = enabled or add_steps
            elif key == "MEMORIZE_TRAIN":
                memorize_train = enabled or memorize_train
    return FakeBehavior(add_steps=add_steps, memorize_train=memorize_train)


# 错误文本（与所有 expected_response 都不含的关键短语，确保 contains 失败）
_WRONG_TEXT = "抱歉，我无法回答这个问题。"


def _expected_text_from_case(case: dict) -> Optional[str]:
    """从 evalset 单个 case 中取出 reference final_response 的纯文本。"""
    conv = case.get("conversation") or []
    if not conv:
        return None
    final = conv[0].get("final_response") or {}
    parts = final.get("parts") or []
    texts = [p.get("text", "") for p in parts if p.get("text")]
    return "\n".join(texts) if texts else None


def _is_protected(case: dict) -> bool:
    state = (case.get("session_input") or {}).get("state") or {}
    return bool(state.get("protected", False))


def _is_train(case: dict) -> bool:
    state = (case.get("session_input") or {}).get("state") or {}
    return state.get("split") == "train"


def gen_final_response_for_case(
    prompt_text: str,
    *,
    case: dict,
) -> str:
    """根据当前 prompt 行为 + evalset 中的 case，返回最终回复文本。

    决策完全基于 FAKE_CONTROLS 块 + case 的 split/protected 字段；
    不读取 scenario 名、eval_id 内容、prompt hash、自然语言 query。
    """
    behavior = parse_behavior(prompt_text)
    expected = _expected_text_from_case(case) or ""
    is_train = _is_train(case)
    is_protected = _is_protected(case)

    if behavior.add_steps:
        return expected
    if behavior.memorize_train:
        return expected if is_train else _WRONG_TEXT
    # baseline
    return expected if is_protected else _WRONG_TEXT


def gen_actual_conversation(prompt_text: str, evalset_dict: dict) -> list:
    """对一个 evalset 中的每个 case 生成 actual_conversation，返回新的 eval_cases list."""
    new_cases = []
    for case in evalset_dict.get("eval_cases", []):
        eval_id = case["eval_id"]
        conv = case.get("conversation") or case.get("actual_conversation") or []
        if not conv:
            continue
        first_inv = conv[0]

        actual_text = gen_final_response_for_case(prompt_text, case=case)
        actual_invocation = {
            "invocation_id": first_inv.get("invocation_id", "act-1"),
            "user_content": first_inv["user_content"],
            "final_response": {
                "parts": [{"text": actual_text}],
                "role": "model",
            },
        }

        new_case = dict(case)
        new_case["eval_mode"] = "trace"
        new_case["actual_conversation"] = [actual_invocation]
        # trace 模式下保留 conversation 作为 reference
        if "conversation" not in new_case:
            new_case["conversation"] = conv
        new_cases.append(new_case)
    return new_cases


# scenario → candidate prompt 模板。
# 所有 candidate 通过显式 FAKE_CONTROLS 块声明行为，不靠自然语言文字。
SUCCESS_CANDIDATE = """# System Prompt (Candidate - Success)

你是一个严谨的数学问答助手。

## 输出格式
所有回答必须按"步骤：...\\n答案：..."的格式给出最终答案。

<!-- FAKE_CONTROLS
ADD_STEPS=true
MEMORIZE_TRAIN=false
-->
"""

NO_EFFECT_CANDIDATE = """# System Prompt (Candidate - NoEffect)

你是一个数学问答助手。本候选不启用任何 fake 改进标记，行为与初始版本一致。

<!-- FAKE_CONTROLS
ADD_STEPS=false
MEMORIZE_TRAIN=false
-->
"""

OVERFIT_CANDIDATE = """# System Prompt (Candidate - Overfit)

本候选只对训练集记忆答案，验证集（含受保护样本）会答错。

<!-- FAKE_CONTROLS
ADD_STEPS=false
MEMORIZE_TRAIN=true
-->
"""

CANDIDATES = {
    "success": SUCCESS_CANDIDATE,
    "no_effect": NO_EFFECT_CANDIDATE,
    "overfit": OVERFIT_CANDIDATE,
}


def build_candidate(scenario: str) -> str:
    """根据 scenario 名返回对应 candidate prompt 文本。"""
    if scenario not in CANDIDATES:
        raise ValueError(f"unknown scenario: {scenario}, expected one of {list(CANDIDATES)}")
    return CANDIDATES[scenario]
