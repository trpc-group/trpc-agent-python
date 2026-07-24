"""Minimal filter decision abstraction for the code review example."""

from __future__ import annotations

import re
from dataclasses import asdict
from dataclasses import dataclass

from .diff_parser import ParsedDiff


_HIGH_RISK_COMMAND_RE = re.compile(
    r"\b("
    r"rm\s+-rf\s+/|"
    r"curl\b[^|;&]*\|\s*(?:sh|bash)|"
    r"wget\b[^|;&]*\|\s*(?:sh|bash)|"
    r"chmod\s+777|"
    r"Invoke-WebRequest\b[^|;&]*\|\s*iex|"
    r"iwr\b[^|;&]*\|\s*iex"
    r")",
    re.IGNORECASE,
)
_MAX_DIFF_BYTES = 200_000
_MAX_CHANGED_LINES = 2_000


@dataclass(frozen=True)
class FilterDecision:
    """A minimal filter decision for review governance."""

    decision: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def evaluate_filter_decision(diff_text: str, parsed_diff: ParsedDiff) -> FilterDecision:
    """Evaluate whether the review can proceed automatically."""

    if len(diff_text.encode("utf-8")) > _MAX_DIFF_BYTES:
        return FilterDecision(
            decision="needs_human_review",
            reason=f"diff is larger than {_MAX_DIFF_BYTES} bytes",
        )
    if len(parsed_diff.changed_lines) > _MAX_CHANGED_LINES:
        return FilterDecision(
            decision="needs_human_review",
            reason=f"diff changes more than {_MAX_CHANGED_LINES} lines",
        )
    if _HIGH_RISK_COMMAND_RE.search(diff_text):
        return FilterDecision(
            decision="needs_human_review",
            reason="diff contains a high-risk command pattern",
        )
    return FilterDecision(decision="allow", reason="diff is within dry-run review limits")
