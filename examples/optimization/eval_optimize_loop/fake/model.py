# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""由 Prompt 与用户输入驱动的确定性离线模型。"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


RULE_PREFIX = "deterministic-fake-rule"
_RULE_RE = re.compile(
    rf"<!--\s*{RULE_PREFIX}\s+([a-z_]+)\s*=\s*([^>]*?)\s*-->",
    re.IGNORECASE,
)
_ORDER_ID_RE = re.compile(
    r"\border\s+([A-Za-z0-9][A-Za-z0-9-]*)",
    re.IGNORECASE,
)
_CUSTOMER_ID_RE = re.compile(
    r"\bcustomer\s+([A-Za-z0-9][A-Za-z0-9-]*)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _RoutingPolicy:
    account_terms: frozenset[str] = frozenset({"email"})
    order_lookup: bool = False
    shipping_policy: bool = False
    refund_route: bool = True


def _parse_bool(value: str, *, default: bool) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "yes", "1", "enabled"}:
        return True
    if normalized in {"false", "no", "0", "disabled"}:
        return False
    return default


def _parse_policy(prompt_text: str) -> _RoutingPolicy:
    values = {
        key.lower(): value.strip()
        for key, value in _RULE_RE.findall(prompt_text)
    }
    account_terms = frozenset(
        term.strip().lower()
        for term in values.get("account_terms", "email").split(",")
        if term.strip()
    )
    return _RoutingPolicy(
        account_terms=account_terms,
        order_lookup=_parse_bool(
            values.get("order_lookup", "false"),
            default=False,
        ),
        shipping_policy=_parse_bool(
            values.get("shipping_policy", "false"),
            default=False,
        ),
        refund_route=_parse_bool(
            values.get("refund_route", "true"),
            default=True,
        ),
    )


def _compact_response(route: str, message: str) -> str:
    return json.dumps(
        {"route": route, "message": message},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def deterministic_response(instruction: str, user_text: str) -> str:
    """仅根据 Prompt instruction 和用户文本生成稳定响应。"""
    if not isinstance(instruction, str):
        raise TypeError("instruction must be a string")
    if not isinstance(user_text, str):
        raise TypeError("user text must be a string")

    policy = _parse_policy(instruction)
    normalized = " ".join(user_text.casefold().split())

    if policy.refund_route and (
        "charged twice" in normalized
        or (
            "duplicate" in normalized
            and ("payment" in normalized or "charge" in normalized)
        )
    ):
        return _compact_response(
            "billing_refund",
            "I will route this duplicate charge for refund review.",
        )

    if policy.shipping_policy and "shipping" in normalized and (
        "standard" in normalized or "how long" in normalized
    ):
        return _compact_response(
            "shipping_policy",
            "Standard shipping normally takes 3-5 business days.",
        )

    order_match = _ORDER_ID_RE.search(user_text)
    if policy.order_lookup and "order" in normalized and order_match is not None:
        order_id = order_match.group(1)
        customer_match = _CUSTOMER_ID_RE.search(user_text)
        message = f"Checking order {order_id}."
        if customer_match is not None:
            message = (
                f"Checking order {order_id} for customer "
                f"{customer_match.group(1)}."
            )
        return _compact_response("order_lookup", message)

    account_term = next(
        (
            term
            for term in sorted(policy.account_terms)
            if term in normalized
        ),
        None,
    )
    if account_term and ("update" in normalized or "change" in normalized):
        attribute = "email" if "email" in normalized else "address"
        return _compact_response(
            "account",
            f"Open profile settings to update your {attribute}.",
        )

    return _compact_response(
        "general_support",
        "Please provide more details so I can route your request.",
    )


def _last_user_text(request: LlmRequest) -> str:
    for content in reversed(request.contents):
        if content.role != "user" or not content.parts:
            continue
        text = "".join(part.text or "" for part in content.parts).strip()
        if text:
            return text
    raise ValueError("LLM request must contain non-empty user text")


class DeterministicFakeModel(LLMModel):
    """通过 SDK Model 接口提供不访问网络的确定性响应。"""

    def __init__(self) -> None:
        super().__init__(model_name="deterministic-fake-model")

    @classmethod
    def supported_models(cls) -> list[str]:
        return ["deterministic-fake-model"]

    async def _generate_async_impl(
        self,
        request: LlmRequest,
        stream: bool = False,
        ctx: InvocationContext | None = None,
    ) -> AsyncGenerator[LlmResponse, None]:
        del stream, ctx
        instruction = ""
        if request.config is not None and request.config.system_instruction:
            instruction = str(request.config.system_instruction)
        response = deterministic_response(instruction, _last_user_text(request))
        yield LlmResponse(
            content=Content(
                role="model",
                parts=[Part.from_text(text=response)],
            )
        )
