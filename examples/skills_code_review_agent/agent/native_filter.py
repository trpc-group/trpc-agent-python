"""tRPC-Agent BaseFilter adapter for code review governance policy."""

from __future__ import annotations

from typing import Any

from .filter_policy import ReviewFilterPolicy
from .filter_policy import SandboxRequest
from .models import DiffInput

try:
    from trpc_agent_sdk.filter import BaseFilter
    from trpc_agent_sdk.filter import FilterAsyncGenHandleType
    from trpc_agent_sdk.filter import FilterAsyncGenReturnType
    from trpc_agent_sdk.filter import register_agent_filter
except ModuleNotFoundError:

    class BaseFilter:  # type: ignore[no-redef]
        pass

    FilterAsyncGenHandleType = Any  # type: ignore[assignment]
    FilterAsyncGenReturnType = Any  # type: ignore[assignment]

    def register_agent_filter(_name):  # type: ignore[no-redef]

        def decorator(cls):
            return cls

        return decorator


@register_agent_filter("code_review_governance_filter")
class CodeReviewGovernanceFilter(BaseFilter):
    """Framework-native adapter for ReviewFilterPolicy."""

    def __init__(self, policy: ReviewFilterPolicy | None = None):
        super().__init__()
        self.policy = policy or ReviewFilterPolicy()

    def evaluate_sandbox_requests(self, diff: DiffInput, requests: list[SandboxRequest]):
        """Expose the review Filter decision engine for Agent/Tool integrations."""
        return self.policy.evaluate(diff, requests)

    async def run_stream(self, ctx: Any, req: Any, handle: FilterAsyncGenHandleType) -> FilterAsyncGenReturnType:
        """Pass through Agent streams while allowing registration in the Filter chain."""
        async for event in handle():
            yield event
            if not getattr(event, "is_continue", True):
                return


def create_review_filter(policy: ReviewFilterPolicy | None = None) -> CodeReviewGovernanceFilter:
    return CodeReviewGovernanceFilter(policy=policy)
