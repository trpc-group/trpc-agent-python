"""Filter policy helpers for sandbox governance decisions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .review_types import FilterDecisionRecord, FilterDecisionType, ParsedDiff

_FORBIDDEN_PATH_PARTS = (".git/", ".env", "secrets/", "id_rsa", ".pem")
_NETWORK_TOKENS = ("http://", "https://", "curl", "wget", "Invoke-WebRequest", "requests.get(")
_DANGEROUS_TOKENS = ("rm -rf", "del /f", "format ", "shutdown ", "mkfs")


@dataclass(slots=True, frozen=True)
class SkillScriptInvocation:
    """Planned sandboxed skill-script execution."""

    name: str
    script_path: Path
    command: list[str]
    target: str


def evaluate_invocations(
    *,
    parsed_diff: ParsedDiff,
    runtime: str,
    invocations: list[SkillScriptInvocation],
    max_changed_files: int = 50,
    max_added_lines: int = 2000,
) -> list[tuple[SkillScriptInvocation, FilterDecisionRecord]]:
    """Evaluate sandbox invocations and return a decision for each one."""

    paired: list[tuple[SkillScriptInvocation, FilterDecisionRecord]] = []
    over_budget = (
        parsed_diff.changed_files_count > max_changed_files
        or parsed_diff.added_lines_count > max_added_lines
    )

    for invocation in invocations:
        decision = FilterDecisionRecord(
            decision=FilterDecisionType.ALLOW,
            target=invocation.target,
            reason_code="allow",
            reason="Invocation allowed by default policy.",
        )

        if _contains_forbidden_path(parsed_diff):
            decision = FilterDecisionRecord(
                decision=FilterDecisionType.DENY,
                target=invocation.target,
                reason_code="forbidden_path",
                reason="Diff touches a forbidden path and cannot enter sandbox execution.",
            )
        elif _contains_token(invocation.command, _DANGEROUS_TOKENS):
            decision = FilterDecisionRecord(
                decision=FilterDecisionType.DENY,
                target=invocation.target,
                reason_code="dangerous_command",
                reason="Invocation contains a dangerous command pattern.",
            )
        elif _contains_token(invocation.command, _NETWORK_TOKENS):
            decision = FilterDecisionRecord(
                decision=FilterDecisionType.DENY,
                target=invocation.target,
                reason_code="network_not_allowed",
                reason="Network access is not permitted for sandbox scripts by default.",
            )
        elif over_budget:
            decision = FilterDecisionRecord(
                decision=FilterDecisionType.NEEDS_HUMAN_REVIEW,
                target=invocation.target,
                reason_code="over_budget",
                reason="Diff size exceeds sandbox budget and requires manual approval.",
                requires_human_review=True,
            )
        elif runtime == "local":
            decision = FilterDecisionRecord(
                decision=FilterDecisionType.NEEDS_HUMAN_REVIEW,
                target=invocation.target,
                reason_code="local_runtime_fallback",
                reason="Local runtime is a development fallback and should not be treated as a production-safe sandbox.",
                requires_human_review=True,
            )

        paired.append((invocation, decision))
    return paired


def _contains_forbidden_path(parsed_diff: ParsedDiff) -> bool:
    """Return whether the diff touches a path blocked by policy."""

    for path in parsed_diff.changed_paths:
        normalized = path.replace("\\", "/")
        if any(part in normalized for part in _FORBIDDEN_PATH_PARTS):
            return True
    return False


def _contains_token(command: list[str], tokens: tuple[str, ...]) -> bool:
    """Return whether a command contains any blocked token."""

    command_text = " ".join(command)
    return any(token in command_text for token in tokens)
