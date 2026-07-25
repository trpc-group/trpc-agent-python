"""Pre-execution command governance for the review workflow."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FilterDecision:
    decision: str
    reason: str


class ReviewPolicyEngine:
    """Allow only bounded static checks; reject or escalate risky actions."""

    def decide(self, command: list[str], timeout_seconds: int = 30) -> FilterDecision:
        if not command or command[0] not in {"python", "pytest", "ruff"}:
            return FilterDecision("deny", "command executable is outside the review allowlist")
        text = " ".join(command).lower()
        if command[:2] == ["python", "-c"] and any(token in text for token in ("subprocess", "os.system", "socket")):
            return FilterDecision("needs_human_review", "dynamic Python code requests process or network access")
        if timeout_seconds > 120:
            return FilterDecision("deny", "command exceeds the 120 second review budget")
        if any(token in text for token in ("curl ", "wget ", "pip install", "npm install")):
            return FilterDecision("deny", "network or dependency installation is not allowed in review sandboxes")
        if any(token in text for token in (".git/", "/root/", "~/.ssh")):
            return FilterDecision("deny", "command targets a forbidden path")
        return FilterDecision("allow", "command is within the static-review allowlist")


def evaluate_command(command: list[str], timeout_seconds: int) -> FilterDecision:
    """Compatibility helper for direct policy checks in tests and integrations."""
    return ReviewPolicyEngine().decide(command, timeout_seconds)
