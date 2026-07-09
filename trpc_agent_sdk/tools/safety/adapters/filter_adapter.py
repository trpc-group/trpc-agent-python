"""ScriptSafetyFilter — Tool filter adapter for the Script Safety Guard.

This filter integrates with the TRPC Agent filter chain to intercept tool
execution requests. It extracts script content from tool arguments, runs
the safety check, and blocks execution when the decision is DENY.

Usage:
    # The filter is auto-registered on import. Tools opt-in by declaring:
    tool = MyTool(filters_name=["script_safety"])

Registration:
    Importing this module registers the filter under FilterType.TOOL with
    name "script_safety". No manual registration is needed.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from trpc_agent_sdk.abc import FilterResult, FilterType
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.filter import BaseFilter, register_tool_filter
from trpc_agent_sdk.tools.safety.guard import ScriptSafetyGuard
from trpc_agent_sdk.tools.safety.models import (
    Decision,
    Language,
    SafetyCheckInput,
    SafetyCheckResult,
    ToolMetadata,
)
from trpc_agent_sdk.tools.safety.policy import PolicyConfig

logger = logging.getLogger(__name__)

# Well-known argument keys that may contain script content
_SCRIPT_KEYS = ("script_content", "script", "code", "source_code", "source")
# Well-known argument keys for language
_LANGUAGE_KEYS = ("language", "lang", "script_language")


@register_tool_filter("script_safety")
class ScriptSafetyFilter(BaseFilter):
    """Tool filter that performs safety checks on script content before execution.

    Integration behavior:
    - In _before(): extracts script from tool args → runs guard.check()
    - If decision is DENY: sets rsp.is_continue = False (blocks tool execution)
    - If decision is NEEDS_HUMAN_REVIEW: allows by default (configurable)
    - If decision is ALLOW: passes through normally

    The filter is registered under the name "script_safety". Tools that want
    safety checking must declare `filters_name=["script_safety"]`.
    """

    def __init__(
        self,
        policy: Optional[PolicyConfig] = None,
        block_on_review: bool = False,
    ) -> None:
        """Initialize the safety filter.

        Args:
            policy: Optional policy config. If None, built-in defaults are used.
            block_on_review: If True, NEEDS_HUMAN_REVIEW decisions also block execution.
                           Defaults to False (only DENY blocks).
        """
        super().__init__()
        self._guard = ScriptSafetyGuard(policy=policy)
        self._block_on_review = block_on_review

    @property
    def guard(self) -> ScriptSafetyGuard:
        """Access the underlying guard instance."""
        return self._guard

    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult) -> None:
        """Pre-execution hook: extract script and run safety check.

        Args:
            ctx: Agent context (contains invocation metadata).
            req: Tool arguments dictionary.
            rsp: Filter result container — set is_continue=False to block.
        """
        # req is the tool's args dict
        if not isinstance(req, dict):
            return

        # Extract script content from args
        script_content = _extract_script(req)
        if not script_content:
            # No script content found in args — nothing to check
            return

        # Detect language
        language = _extract_language(req)

        # Build tool metadata from context
        tool_metadata = _build_tool_metadata(ctx, req)

        # Run safety check
        check_input = SafetyCheckInput(
            script_content=script_content,
            language=language,
            tool_metadata=tool_metadata,
        )

        try:
            result = self._guard.check(check_input)
        except Exception as e:
            # Guard itself should never raise, but be defensive
            logger.error("ScriptSafetyFilter: guard.check() raised: %s", e, exc_info=True)
            return  # Fail-open: allow execution if guard crashes

        # Decision handling
        if result.decision == Decision.DENY:
            rsp.is_continue = False
            rsp.error = _make_blocked_error(result)
            logger.warning(
                "ScriptSafetyFilter BLOCKED tool execution: %s findings, max_severity=%s",
                len(result.findings),
                result.max_severity,
            )
        elif result.decision == Decision.NEEDS_HUMAN_REVIEW and self._block_on_review:
            rsp.is_continue = False
            rsp.error = _make_review_error(result)
            logger.warning(
                "ScriptSafetyFilter BLOCKED (review required): %s findings",
                len(result.findings),
            )

    async def _after(self, ctx: AgentContext, req: Any, rsp: FilterResult) -> None:
        """Post-execution hook. Currently no-op."""
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_script(args: dict[str, Any]) -> str:
    """Extract script content from tool arguments.

    Searches known keys in priority order.
    """
    for key in _SCRIPT_KEYS:
        value = args.get(key)
        if value and isinstance(value, str):
            return value
    return ""


def _extract_language(args: dict[str, Any]) -> Language:
    """Extract language from tool arguments, defaulting to Python."""
    for key in _LANGUAGE_KEYS:
        value = args.get(key)
        if value and isinstance(value, str):
            lang_lower = value.lower().strip()
            if lang_lower in ("bash", "sh", "shell"):
                return Language.BASH
            if lang_lower in ("python", "python3", "py"):
                return Language.PYTHON
    return Language.PYTHON


def _build_tool_metadata(ctx: AgentContext, args: dict[str, Any]) -> ToolMetadata:
    """Build ToolMetadata from AgentContext and args."""
    tool_name = ""
    invocation_id = ""
    agent_name = ""
    user_id = ""

    # Try to extract from context if available
    if hasattr(ctx, "tool_name"):
        tool_name = getattr(ctx, "tool_name", "") or ""
    if hasattr(ctx, "invocation_id"):
        invocation_id = getattr(ctx, "invocation_id", "") or ""
    if hasattr(ctx, "agent_name"):
        agent_name = getattr(ctx, "agent_name", "") or ""
    if hasattr(ctx, "user_id"):
        user_id = getattr(ctx, "user_id", "") or ""

    return ToolMetadata(
        tool_name=tool_name,
        invocation_id=invocation_id,
        agent_name=agent_name,
        user_id=user_id,
    )


class SafetyCheckBlockedError(Exception):
    """Raised when a safety check blocks tool execution."""

    def __init__(self, result: SafetyCheckResult) -> None:
        self.result = result
        findings_desc = "; ".join(f"[{f.rule_id}] {f.description}" for f in result.findings[:3])
        super().__init__(f"Safety check blocked execution (decision={result.decision.value}, "
                         f"findings={len(result.findings)}): {findings_desc}")


def _make_blocked_error(result: SafetyCheckResult) -> SafetyCheckBlockedError:
    """Create a blocked error from a safety check result."""
    return SafetyCheckBlockedError(result)


def _make_review_error(result: SafetyCheckResult) -> SafetyCheckBlockedError:
    """Create a review-required error from a safety check result."""
    return SafetyCheckBlockedError(result)
