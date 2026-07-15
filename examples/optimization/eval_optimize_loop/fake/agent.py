# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Prompt-sensitive deterministic agent used by the offline pipeline mode."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from trpc_agent_sdk.evaluation import TargetPrompt


RULE_PREFIX = "deterministic-fake-rule"
_RULE_RE = re.compile(
    rf"<!--\s*{RULE_PREFIX}\s+([a-z_]+)\s*=\s*([^>]*?)\s*-->",
    re.IGNORECASE,
)
_ORDER_ID_RE = re.compile(r"\border\s+([A-Za-z0-9][A-Za-z0-9-]*)", re.IGNORECASE)
_CUSTOMER_ID_RE = re.compile(r"\bcustomer\s+([A-Za-z0-9][A-Za-z0-9-]*)", re.IGNORECASE)


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
    values: dict[str, str] = {}
    for key, value in _RULE_RE.findall(prompt_text):
        values[key.lower()] = value.strip()

    account_terms = frozenset(
        term.strip().lower()
        for term in values.get("account_terms", "email").split(",")
        if term.strip()
    )
    return _RoutingPolicy(
        account_terms=account_terms,
        order_lookup=_parse_bool(values.get("order_lookup", "false"), default=False),
        shipping_policy=_parse_bool(values.get("shipping_policy", "false"), default=False),
        refund_route=_parse_bool(values.get("refund_route", "true"), default=True),
    )


def _compact_response(route: str, message: str) -> str:
    # Keep route before message: the example intentionally uses exact text matching.
    return json.dumps({"route": route, "message": message}, ensure_ascii=False, separators=(",", ":"))


class DeterministicFakeAgent:
    """A black-box ``call_agent`` implementation whose behavior follows prompt rules.

    The agent deliberately receives only the user query. It cannot inspect an
    eval id, expected response, scenario name, call count, or external state.
    Every call rereads ``target_prompt`` so a candidate written between the
    baseline and candidate evaluations takes effect immediately.
    """

    def __init__(self, target_prompt: TargetPrompt) -> None:
        self._target_prompt = target_prompt

    async def call_agent(self, query: str) -> str:
        if not isinstance(query, str):
            raise TypeError("query must be a string")

        prompt_values = await self._target_prompt.read_all()
        policy = _parse_policy("\n".join(prompt_values.values()))
        normalized = " ".join(query.casefold().split())

        if policy.refund_route and (
            "charged twice" in normalized
            or ("duplicate" in normalized and ("payment" in normalized or "charge" in normalized))
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

        if (
            policy.order_lookup
            and "order" in normalized
            and (order_match := _ORDER_ID_RE.search(query)) is not None
        ):
            order_id = order_match.group(1)
            customer_match = _CUSTOMER_ID_RE.search(query)
            if customer_match is not None:
                message = f"Checking order {order_id} for customer {customer_match.group(1)}."
            else:
                message = f"Checking order {order_id}."
            return _compact_response("order_lookup", message)

        account_term = next((term for term in sorted(policy.account_terms) if term in normalized), None)
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
