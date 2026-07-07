"""Filter chain — pre-execution safety checks before sandbox runs.

Integrates with tRPC-Agent's Filter framework when available, with
a standalone fallback for standalone usage.
"""

import re

from .types import FilterDecision


class SafetyFilter:
    """A single safety filter rule."""

    def __init__(self, name: str, pattern: str, reason: str,
                 action: str = "deny"):
        self.name = name
        self.pattern = re.compile(pattern, re.IGNORECASE)
        self.reason = reason
        self.action = action  # "deny" or "needs_human_review"

    def check(self, content: str) -> FilterDecision | None:
        """Check if content matches the denied pattern.

        Returns FilterDecision if matched, None if clean.
        """
        if self.pattern.search(content):
            return FilterDecision(
                action=self.action,
                reason=self.reason,
                filter_name=self.name,
            )
        return None


# Built-in safety filters
_DEFAULT_FILTERS = [
    SafetyFilter(
        "dangerous_commands",
        r"rm\s+-rf\s+/|mkfs\.|dd\s+if=|:\s*\(\)\s*\{.*:\|:.*\}|fork\s+bomb",
        "High-risk shell command detected — potential system destruction",
        "deny",
    ),
    SafetyFilter(
        "network_exfil",
        r"curl.*\|\s*(?:ba)?sh|wget.*-O\s*-.*\|.*sh|nc\s+-[lL].*-[eE]",
        "Potential data exfiltration or reverse shell",
        "deny",
    ),
    SafetyFilter(
        "sandbox_escape",
        r"/proc/|/sys/|/dev/mem|chroot|unshare|nsenter",
        "Sandbox escape attempt detected",
        "deny",
    ),
    SafetyFilter(
        "sudo_escalation",
        r"sudo\s+|su\s+-|pkexec",
        "Privilege escalation attempt",
        "deny",
    ),
]


class FilterChain:
    """Chain of safety filters executed in order.

    Returns the first non-allow decision (deny or needs_human_review),
    or allow if all filters pass.
    """

    def __init__(self, filters: list[SafetyFilter] | None = None,
                 extra_patterns: list[str] | None = None):
        self.filters = list(filters or _DEFAULT_FILTERS)
        if extra_patterns:
            for i, pat in enumerate(extra_patterns):
                self.filters.append(SafetyFilter(
                    name=f"custom_rule_{i}",
                    pattern=pat,
                    reason=f"Matched custom deny pattern: {pat}",
                    action="deny",
                ))

    def evaluate(self, diff_text: str, *extra_contexts: str) -> FilterDecision:
        """Run all filters against the diff and any extra context.

        Args:
            diff_text: The raw diff content.
            *extra_contexts: Additional content to scan (e.g., script outputs).

        Returns:
            FilterDecision: allow, deny, or needs_human_review.
        """
        combined = diff_text + "\n".join(extra_contexts)
        for f in self.filters:
            result = f.check(combined)
            if result is not None:
                return result
        return FilterDecision(action="allow", reason="All safety checks passed")

    def get_filters_summary(self) -> dict:
        """Return summary of all active filters."""
        return {
            "total_filters": len(self.filters),
            "filters": [
                {"name": f.name, "pattern": f.pattern.pattern, "action": f.action}
                for f in self.filters
            ],
        }


# ── SDK integration (attempt to use tRPC-Agent Filter framework) ──

try:
    from trpc_agent_sdk.filter import BaseFilter, FilterType

    class CodeReviewAgentFilter(BaseFilter):
        """tRPC-Agent framework filter for code review safety checks."""

        type = FilterType.AGENT
        name = "code_review_safety"

        async def _before(self, ctx, req, rsp):
            """Intercept before agent execution."""
            # This would be used in a full agent setup
            pass

    _HAS_SDK_FILTER = True
except ImportError:
    _HAS_SDK_FILTER = False


def has_sdk_filter_integration() -> bool:
    """Check if tRPC-Agent Filter framework is available."""
    return _HAS_SDK_FILTER
