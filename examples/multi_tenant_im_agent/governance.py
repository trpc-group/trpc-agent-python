"""Tenant-scoped inbound policy checks."""

from __future__ import annotations

from dataclasses import dataclass

from .domain import InboundMessage, TenantConfig


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    estimated_tokens: int


class TenantPolicy:
    """Fail-closed policy applied before the model sees user content."""

    def evaluate(
        self,
        tenant: TenantConfig,
        message: InboundMessage,
        *,
        monthly_tokens_used: int = 0,
    ) -> PolicyDecision:
        estimated_tokens = max(1, (len(message.text) + 3) // 4)
        if not message.text.strip():
            return PolicyDecision(False, "empty_message", estimated_tokens)
        if len(message.text) > tenant.max_input_chars:
            return PolicyDecision(False, "input_too_long", estimated_tokens)
        if estimated_tokens > tenant.request_token_budget:
            return PolicyDecision(
                False, "request_token_budget_exceeded", estimated_tokens
            )
        if monthly_tokens_used + estimated_tokens > tenant.monthly_token_budget:
            return PolicyDecision(
                False, "monthly_token_budget_exceeded", estimated_tokens
            )
        if tenant.allowed_user_ids and message.user_id not in tenant.allowed_user_ids:
            return PolicyDecision(False, "user_not_allowed", estimated_tokens)
        return PolicyDecision(True, "allowed", estimated_tokens)
