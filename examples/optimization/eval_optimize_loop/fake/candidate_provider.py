# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Pure deterministic candidate generation for the offline pipeline mode."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Mapping

from ..schemas import FakeCandidateProposal
from ..schemas import CandidateScenario
from .model import RULE_PREFIX


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
