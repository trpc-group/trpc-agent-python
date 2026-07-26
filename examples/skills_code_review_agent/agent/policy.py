"""Pre-execution command governance for the review workflow."""
from __future__ import annotations

from dataclasses import dataclass
import shlex


@dataclass(frozen=True)
class FilterDecision:
    decision: str
    reason: str


class ReviewPolicyEngine:
    """Allow only bounded static checks; reject or escalate risky actions."""

    def decide(self, command: list[str] | str, timeout_seconds: int = 30) -> FilterDecision:
        if isinstance(command, str):
            if any(marker in command for marker in (";", "|", "&", "`", "$", "<", ">", "\n", "\r")):
                return FilterDecision("deny", "shell control syntax is not allowed in review commands")
            try:
                command = shlex.split(command, posix=True)
            except ValueError:
                return FilterDecision("deny", "command quoting could not be parsed safely")
        if any(any(marker in token for marker in (";", "|", "&", "`", "$", "<", ">", "\n", "\r")) for token in command):
            return FilterDecision("deny", "shell control syntax is not allowed in review commands")
        if not command or command[0] not in {"python", "pytest", "ruff"}:
            return FilterDecision("deny", "command executable is outside the review allowlist")
        text = " ".join(command).lower()
        if command[0] == "python" and any(token == "-c" or token.startswith("-c") for token in command[1:]):
            return FilterDecision("needs_human_review", "dynamic Python code is not permitted in review commands")
        if timeout_seconds > 120:
            return FilterDecision("deny", "command exceeds the 120 second review budget")
        if any(token in text for token in ("curl ", "wget ", "pip install", "npm install")):
            return FilterDecision("deny", "network or dependency installation is not allowed in review sandboxes")
        if any(token in text for token in (".git/", "/root/", "~/.ssh")):
            return FilterDecision("deny", "command targets a forbidden path")
        return FilterDecision("allow", "command is within the static-review allowlist")


def evaluate_command(command: list[str] | str, timeout_seconds: int) -> FilterDecision:
    """Compatibility helper for direct policy checks in tests and integrations."""
    return ReviewPolicyEngine().decide(command, timeout_seconds)
