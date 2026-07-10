from __future__ import annotations

import json

from trpc_agent_sdk.evaluation import TargetPrompt


def _json(route: str, tool: str, arguments: dict[str, str], answer: str) -> str:
    return json.dumps({"route": route, "tool": tool, "arguments": arguments, "answer": answer}, ensure_ascii=False, sort_keys=True)


class FakeSupportAgent:
    """Prompt-backed support router with no model or network dependency."""

    def __init__(self, target_prompt: TargetPrompt) -> None:
        self._target_prompt = target_prompt

    async def call_agent(self, query: str) -> str:
        prompts = await self._target_prompt.read_all()
        mode = prompts["system_prompt"].strip()
        general = mode == "GENERAL_FIX"
        overfit = mode == "OVERFIT"
        if query == "生成订单 A100 的 JSON 工单摘要":
            return _json("order_lookup", "lookup_order", {"order_id": "A100"}, "正在查询订单 A100。") if general or overfit else "订单 A100 正在查询"
        if query == "查询订单 A100":
            return _json("order_lookup", "lookup_order", {"order_id": "A100"}, "正在查询订单 A100。") if general or overfit else _json("order_lookup", "lookup_order", {}, "正在查询订单。")
        if query == "特殊退款政策是什么":
            return _json("knowledge_gap", "none", {}, "未提供该政策，不能猜测。") if overfit else _json("knowledge_gap", "none", {}, "我不确定。")
        if query == "生成订单 B200 的 JSON 工单摘要":
            return _json("order_lookup", "lookup_order", {"order_id": "B200"}, "正在查询订单 B200。") if general or overfit else "订单 B200 正在查询"
        if query == "退款订单 R900，金额 12 USD":
            if overfit:
                return _json("order_lookup", "lookup_order", {"order_id": "R900"}, "正在查询订单 R900。")
            return _json("refund", "refund_order", {"order_id": "R900", "currency": "USD", "amount": "12"}, "正在退款订单 R900。")
        if query == "如何查看订单状态":
            return _json("faq", "none", {}, "在订单详情页可查看订单状态。")
        raise ValueError(f"unknown fake query: {query}")
