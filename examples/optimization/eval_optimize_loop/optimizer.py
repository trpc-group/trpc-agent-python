# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Rule-based optimizer: 与 GEPA 等价的、确定性的"扩展机制"。

为什么不用内置 GEPA
------------------
AgentOptimizer.optimize 内置的 gepa_reflective 算法依赖真实 reflection_lm
（LLM）来"反思失败、改写 prompt"，没有 API Key 跑不了。本模块提供等价能力：
基于失败归因，从一个候选 prompt 池中选出待评估候选（候选设计对应归因发现的
能力缺口，例如天气工具缺失 -> 注入"调用 weather"指令），并支持把最佳候选写回
TargetPrompt 源文件（审计/回写）。

整个搜索是确定性的、可在 3 分钟内跑完、无需任何网络调用。
"""

from __future__ import annotations

from typing import Optional

from trpc_agent_sdk.evaluation import TargetPrompt


class RuleBasedOptimizer:
    """确定性候选生成器 + 写回器。"""

    def __init__(
        self,
        baseline_text: str,
        candidate_texts: list[str],
        target_prompt: Optional[TargetPrompt] = None,
    ) -> None:
        self.baseline_text = baseline_text
        self.candidate_texts = list(candidate_texts)
        self.target_prompt = target_prompt

    def propose(self) -> list[tuple[str, str]]:
        """返回待评估候选列表 [(label, prompt_text), ...]。

        第一个永远是 baseline（作为对照），其后是配置里的候选池。
        """
        items = [("baseline", self.baseline_text)]
        for i, text in enumerate(self.candidate_texts, start=1):
            items.append((f"candidate_{i}", text))
        return items

    async def commit(self, prompt_text: str) -> None:
        """把选中的最佳候选写回 TargetPrompt 源（默认不回写，由 pipeline 控制）。"""
        if self.target_prompt is None:
            return
        names = self.target_prompt.names()
        if not names:
            return
        await self.target_prompt.write_all({name: prompt_text for name in names})
