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
from hashlib import sha256
from typing import Mapping

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from ..data.schemas import CandidateScenario
from ..data.schemas import FakeCandidateProposal


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


_SCENARIO_BLOCKS: dict[CandidateScenario, tuple[str, str]] = {
    "improve": (
        "Generalize routing across account synonyms, order lookup, shipping policy, and refunds.",
        "\n".join(
            [
                "<!-- deterministic-fake-candidate:start -->",
                "Apply general customer-support routing rules across equivalent user phrasings.",
                f"<!-- {RULE_PREFIX} account_terms=email,address -->",
                f"<!-- {RULE_PREFIX} order_lookup=true -->",
                f"<!-- {RULE_PREFIX} shipping_policy=true -->",
                f"<!-- {RULE_PREFIX} refund_route=true -->",
                "<!-- deterministic-fake-candidate:end -->",
            ]
        ),
    ),
    "no_improvement": (
        "Add an auditable wording-only change that leaves routing behavior unchanged.",
        "\n".join(
            [
                "<!-- deterministic-fake-candidate:start -->",
                "Keep responses concise, direct, and suitable for customer support.",
                "<!-- deterministic-fake-candidate:end -->",
            ]
        ),
    ),
    "overfit": (
        "Narrow routing to email changes and order lookups while disabling unseen intents.",
        "\n".join(
            [
                "<!-- deterministic-fake-candidate:start -->",
                "Handle only email profile changes and order lookups; use general support otherwise.",
                f"<!-- {RULE_PREFIX} account_terms=email -->",
                f"<!-- {RULE_PREFIX} order_lookup=true -->",
                f"<!-- {RULE_PREFIX} shipping_policy=false -->",
                f"<!-- {RULE_PREFIX} refund_route=false -->",
                "<!-- deterministic-fake-candidate:end -->",
            ]
        ),
    ),
}


def _prompt_mapping_sha256(prompts: Mapping[str, str]) -> str:
    canonical = json.dumps(
        dict(prompts),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


class DeterministicFakeCandidateProvider:
    """Generate one structured candidate without performing I/O or mutation."""

    def __init__(self, target_field: str = "system_prompt") -> None:
        if not target_field:
            raise ValueError("target_field must not be empty")
        self._target_field = target_field

    def propose(
        self,
        current_prompts: Mapping[str, str],
        *,
        scenario: CandidateScenario,
        seed: int,
    ) -> FakeCandidateProposal:
        if self._target_field not in current_prompts:
            raise ValueError(f"fake candidate target field is missing: {self._target_field}")
        if scenario not in _SCENARIO_BLOCKS:
            raise ValueError(f"unknown fake candidate scenario: {scenario}")
        if any(not isinstance(name, str) or not isinstance(value, str) for name, value in current_prompts.items()):
            raise TypeError("current_prompts must map string field names to string values")

        rationale, rule_block = _SCENARIO_BLOCKS[scenario]
        prompts = dict(current_prompts)
        baseline = prompts[self._target_field].rstrip()
        prompts[self._target_field] = f"{baseline}\n\n{rule_block}\n"

        parent_hash = _prompt_mapping_sha256(current_prompts)
        candidate_hash = _prompt_mapping_sha256(prompts)
        changed_fields = [name for name in current_prompts if current_prompts[name] != prompts[name]]
        return FakeCandidateProposal(
            scenario=scenario,
            prompts=prompts,
            changed_fields=changed_fields,
            rationale=rationale,
            seed=seed,
            parent_prompt_sha256=parent_hash,
            candidate_prompt_sha256=candidate_hash,
            candidate_id=f"fake-{scenario}-{candidate_hash[:12]}",
        )
