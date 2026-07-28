"""Compatibility facade for the layered governance package."""

from __future__ import annotations

from collections.abc import Callable

from ..governance import FilterEngine
from ..governance.policy import GovernancePolicy, load_policy
from .models import ExecutionBudget, ExecutionRequest, FilterDecision


def evaluate_execution_policy(
    request: ExecutionRequest,
    *,
    workspace_root: str,
    budget: ExecutionBudget | None = None,
    allowed_network_targets: set[str] | None = None,
    allowed_commands: set[str] | None = None,
) -> FilterDecision:
    """Evaluate one request; optional overrides preserve the original public API."""
    policy = load_policy()
    if allowed_network_targets is not None:
        policy.allowed_network_targets = allowed_network_targets
    if allowed_commands is not None:
        policy.allowed_commands = allowed_commands
    return FilterEngine(workspace_root, policy=policy, budget=budget).check(request)


class ReviewExecutionFilter:
    """Application-facing governance filter."""

    def __init__(
        self,
        workspace_root: str,
        *,
        budget: ExecutionBudget | None = None,
        decision_sink: Callable[[FilterDecision], None] | None = None,
        policy: GovernancePolicy | None = None,
    ) -> None:
        self.engine = FilterEngine(
            workspace_root,
            policy=policy,
            budget=budget,
            decision_sink=decision_sink,
        )
        self.budget = self.engine.budget

    def run(self, request: ExecutionRequest) -> FilterDecision:
        return self.engine.check(request)
