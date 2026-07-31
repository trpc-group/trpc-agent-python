#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2025 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""验证审计回调对改写 query 的容错、事件文本拼接的健壮性与 GateConfig 边界。"""

from __future__ import annotations

import pytest

import gates
import live_agent
import pipeline


async def _echo(query: str) -> str:
    return f"reply:{query}"


@pytest.mark.asyncio
async def test_audited_call_agent_matches_trimmed_query() -> None:
    """优化器对 query 做 trim/换行重排后仍应命中登记的上下文。"""

    contexts = {pipeline._normalized_query("小明有 3 个苹果"): [{"eval_id": "case_1"}]}
    audit: list[dict] = []
    call_agent = pipeline._audited_call_agent(_echo, contexts, audit)

    await call_agent("  小明有 3 个苹果 \n")

    assert audit[0]["context_match"] == "matched"
    assert audit[0]["eval_contexts"] == [{"eval_id": "case_1"}]
    assert audit[0]["status"] == "ok"


@pytest.mark.asyncio
async def test_audited_call_agent_degrades_on_unknown_query() -> None:
    """未登记的 query 不应抛错中断优化流程，而是空上下文继续并标记。"""

    audit: list[dict] = []
    call_agent = pipeline._audited_call_agent(_echo, {}, audit)

    response = await call_agent("完全陌生的模板包裹 query")

    assert response == "reply:完全陌生的模板包裹 query"
    assert audit[0]["context_match"] == "unmatched"
    assert audit[0]["eval_contexts"] == []


class _PartNoThought:
    """模拟不带 thought 属性的 provider Part。"""

    def __init__(self, text: str):
        self.text = text


class _PartWithThought:
    def __init__(self, text: str, thought: bool):
        self.text = text
        self.thought = thought


class _Content:
    def __init__(self, parts):
        self.parts = parts


class _Event:
    def __init__(self, parts, final: bool = True):
        self.content = _Content(parts)
        self._final = final

    def is_final_response(self) -> bool:
        return self._final


def test_response_text_tolerates_parts_without_thought_attr() -> None:
    """Part 完全没有 thought 属性时不应抛 AttributeError。"""

    event = _Event([_PartNoThought("你好"), _PartNoThought("世界")])
    assert live_agent.response_text_from_event(event) == "你好世界"


def test_response_text_skips_thought_parts_and_non_final_events() -> None:
    event = _Event([_PartWithThought("内心推理", True), _PartWithThought("答案：10", False)])
    assert live_agent.response_text_from_event(event) == "答案：10"
    assert live_agent.response_text_from_event(_Event([_PartNoThought("忽略")], final=False)) == ""


def test_gate_config_rejects_nonpositive_budget() -> None:
    """budget_tokens<=0 会让 G6 把任意 measured 成本误判为超预算，必须拒绝。"""

    with pytest.raises(ValueError, match="budget_tokens"):
        gates.GateConfig(budget_tokens=0)
    with pytest.raises(ValueError, match="budget_usd"):
        gates.GateConfig(budget_usd=-1.0)
    assert gates.GateConfig().budget_tokens == 100_000
