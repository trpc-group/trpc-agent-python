"""File operations safety rule — detects risky file system access.

Rule IDs:
- FS-001: Access to forbidden paths (HIGH)
- FS-002: Destructive file operations (MEDIUM)
"""

from __future__ import annotations

import ast
import fnmatch
import os
from typing import TYPE_CHECKING

from trpc_agent_sdk.tools.safety.models import (
    Decision,
    Finding,
    Language,
    RiskCategory,
    Severity,
)
from trpc_agent_sdk.tools.safety.rules._base import BaseRule, register_rule
from trpc_agent_sdk.tools.safety.scanner import bash_scanner, python_scanner

if TYPE_CHECKING:
    from trpc_agent_sdk.tools.safety.models import ScanContext
    from trpc_agent_sdk.tools.safety.policy import PolicyConfig

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Python functions that perform file operations
_PYTHON_FILE_FUNCS: set[str] = {
    "open",
    "os.remove",
    "os.unlink",
    "os.rmdir",
    "os.removedirs",
    "os.rename",
    "os.replace",
    "shutil.rmtree",
    "shutil.move",
    "shutil.copy",
    "shutil.copy2",
    "shutil.copytree",
    "pathlib.Path.unlink",
    "pathlib.Path.rmdir",
    "pathlib.Path.write_text",
    "pathlib.Path.write_bytes",
}

# Python functions that are destructive (delete/overwrite)
_PYTHON_DESTRUCTIVE_FUNCS: set[str] = {
    "os.remove",
    "os.unlink",
    "os.rmdir",
    "os.removedirs",
    "shutil.rmtree",
    "pathlib.Path.unlink",
    "pathlib.Path.rmdir",
}

# Bash patterns for file operations
_BASH_FILE_PATTERNS: dict[str, str] = {
    "rm": r"\brm\s+",
    "rmdir": r"\brmdir\s+",
    "mv": r"\bmv\s+",
    "dd": r"\bdd\s+",
    "truncate": r"\btruncate\s+",
    "shred": r"\bshred\s+",
}

# Bash patterns specifically for destructive operations
_BASH_DESTRUCTIVE_PATTERNS: dict[str, str] = {
    "rm_recursive": r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+|--recursive|--force)",
    "rm_root": r"\brm\s+.*\s+/\s*$",
    "dd_of": r"\bdd\s+.*of=/dev/",
    "mkfs": r"\bmkfs\b",
    "format": r"\bformat\b",
}


def _expand_path(path: str) -> str:
    """Expand ~ and environment variables in a path."""
    return os.path.expandvars(os.path.expanduser(path))


def _path_matches_forbidden(file_path: str, forbidden_paths: list[str]) -> str | None:
    """Check if a file path matches any forbidden path pattern.

    Returns the matched forbidden pattern or None.
    """
    expanded = _expand_path(file_path)
    for forbidden in forbidden_paths:
        forbidden_expanded = _expand_path(forbidden)
        # Check prefix match (forbidden path is a directory prefix)
        if expanded.startswith(forbidden_expanded):
            return forbidden
        # Check glob pattern match
        if fnmatch.fnmatch(expanded, forbidden_expanded):
            return forbidden
    return None


# ---------------------------------------------------------------------------
# Rule: FS-001 — Forbidden path access
# ---------------------------------------------------------------------------


@register_rule
class ForbiddenPathRule(BaseRule):
    """Detects file operations targeting forbidden paths."""

    rule_id = "FS-001"
    category = RiskCategory.FILE_OPERATIONS
    severity = Severity.HIGH
    languages = [Language.PYTHON, Language.BASH]
    description = "Detects file access to forbidden/sensitive paths."

    def scan(self, ctx: "ScanContext", policy: "PolicyConfig | None" = None) -> list[Finding]:
        findings: list[Finding] = []
        if policy is None:
            return findings

        forbidden_paths = policy.file_operations.forbidden_paths
        if not forbidden_paths:
            return findings

        if ctx.language == Language.PYTHON and ctx.ast_tree is not None:
            findings.extend(self._scan_python(ctx, forbidden_paths))
        elif ctx.language == Language.BASH:
            findings.extend(self._scan_bash(ctx, forbidden_paths))

        return findings

    def _scan_python(self, ctx: "ScanContext", forbidden_paths: list[str]) -> list[Finding]:
        findings: list[Finding] = []
        tree = ctx.ast_tree

        # Find all file operation calls
        calls = python_scanner.find_function_calls(tree, _PYTHON_FILE_FUNCS)
        for call in calls:
            str_args = python_scanner.get_string_args(call)
            for arg in str_args:
                matched = _path_matches_forbidden(arg, forbidden_paths)
                if matched:
                    findings.append(
                        Finding(
                            rule_id=self.rule_id,
                            category=self.category,
                            severity=self.severity,
                            decision=Decision.DENY,
                            evidence=f"{python_scanner.get_call_name(call)}({arg!r})",
                            line_number=call.lineno,
                            description=f"File operation targets forbidden path: {matched}",
                            recommendation="Remove or change the file path to a permitted location.",
                        ))
        return findings

    def _scan_bash(self, ctx: "ScanContext", forbidden_paths: list[str]) -> list[Finding]:
        findings: list[Finding] = []
        for line_num, line in enumerate(ctx.lines, start=1):
            if bash_scanner.is_comment_line(line):
                continue
            effective = bash_scanner.strip_inline_comment(line).strip()
            if not effective:
                continue
            # Check if line references any forbidden path
            for forbidden in forbidden_paths:
                expanded = _expand_path(forbidden)
                if expanded in effective or forbidden in effective:
                    findings.append(
                        Finding(
                            rule_id=self.rule_id,
                            category=self.category,
                            severity=self.severity,
                            decision=Decision.DENY,
                            evidence=effective,
                            line_number=line_num,
                            description=f"Script references forbidden path: {forbidden}",
                            recommendation="Remove or change the file path to a permitted location.",
                        ))
                    break  # One finding per line
        return findings


# ---------------------------------------------------------------------------
# Rule: FS-002 — Destructive file operations
# ---------------------------------------------------------------------------


@register_rule
class DestructiveFileOpRule(BaseRule):
    """Detects destructive file operations (delete, overwrite)."""

    rule_id = "FS-002"
    category = RiskCategory.FILE_OPERATIONS
    severity = Severity.MEDIUM
    languages = [Language.PYTHON, Language.BASH]
    description = "Detects destructive file operations such as recursive delete."

    def scan(self, ctx: "ScanContext", policy: "PolicyConfig | None" = None) -> list[Finding]:
        findings: list[Finding] = []

        if ctx.language == Language.PYTHON and ctx.ast_tree is not None:
            findings.extend(self._scan_python(ctx))
        elif ctx.language == Language.BASH:
            findings.extend(self._scan_bash(ctx))

        return findings

    def _scan_python(self, ctx: "ScanContext") -> list[Finding]:
        findings: list[Finding] = []
        tree = ctx.ast_tree

        calls = python_scanner.find_function_calls(tree, _PYTHON_DESTRUCTIVE_FUNCS)
        for call in calls:
            call_name = python_scanner.get_call_name(call)
            str_args = python_scanner.get_string_args(call)
            evidence = f"{call_name}({', '.join(repr(a) for a in str_args)})" if str_args else call_name
            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    category=self.category,
                    severity=self.severity,
                    decision=Decision.NEEDS_HUMAN_REVIEW,
                    evidence=evidence,
                    line_number=call.lineno,
                    description=f"Destructive file operation: {call_name}",
                    recommendation="Ensure this deletion is intentional and targets the correct path.",
                ))
        return findings

    def _scan_bash(self, ctx: "ScanContext") -> list[Finding]:
        findings: list[Finding] = []
        patterns = bash_scanner.CompiledPatternSet(_BASH_DESTRUCTIVE_PATTERNS)
        matches = bash_scanner.scan_lines(ctx.source_code, patterns)

        for m in matches:
            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    category=self.category,
                    severity=Severity.HIGH if m.pattern_name in ("rm_root", "dd_of", "mkfs") else self.severity,
                    decision=Decision.DENY if m.pattern_name in ("rm_root", "dd_of",
                                                                 "mkfs") else Decision.NEEDS_HUMAN_REVIEW,
                    evidence=m.line_content,
                    line_number=m.line_number,
                    description=f"Destructive file operation detected ({m.pattern_name})",
                    recommendation="Verify this operation is intentional and will not cause data loss.",
                ))
        return findings
