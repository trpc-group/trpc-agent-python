"""Resource abuse safety rule — detects potential resource exhaustion patterns.

Rule IDs:
- RES-001: Infinite loop / fork bomb pattern (HIGH)
- RES-002: Excessive resource consumption indicators (MEDIUM)
"""

from __future__ import annotations

import ast
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

# Bash patterns for resource abuse
_BASH_RESOURCE_PATTERNS: dict[str, str] = {
    "fork_bomb": r":\(\)\s*\{\s*:\|:&\s*\}\s*;:",
    "fork_bomb_alt": r"\.\(\)\s*\{\s*\.\|\s*\.&\s*\}\s*;\s*\.",
    "while_true": r"\bwhile\s+(true|1|:)\s*;?\s*do",
    "infinite_yes": r"\byes\s*\|",
    "dev_zero_fill": r"\bdd\s+.*if=/dev/zero",
    "dev_urandom_fill": r"\bdd\s+.*if=/dev/urandom",
    "memory_fill": r"\bhead\s+-c\s+\d+[gG]\s+/dev/",
}

# Python functions related to resource-intensive operations
_PYTHON_MULTIPROCESS_FUNCS: set[str] = {
    "os.fork",
    "multiprocessing.Process",
    "threading.Thread",
}


# ---------------------------------------------------------------------------
# Rule: RES-001 — Fork bomb / infinite loop
# ---------------------------------------------------------------------------


@register_rule
class ForkBombRule(BaseRule):
    """Detects fork bomb and infinite loop patterns."""

    rule_id = "RES-001"
    category = RiskCategory.RESOURCE
    severity = Severity.HIGH
    languages = [Language.PYTHON, Language.BASH]
    description = "Detects fork bomb, infinite loop, and resource exhaustion patterns."

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

        # Detect while True without break
        for node in ast.walk(tree):
            if isinstance(node, ast.While):
                # Check if condition is always True
                if self._is_always_true(node.test):
                    # Check if loop body has a break/return
                    has_exit = self._has_exit_statement(node)
                    if not has_exit:
                        findings.append(Finding(
                            rule_id=self.rule_id,
                            category=self.category,
                            severity=self.severity,
                            decision=Decision.NEEDS_HUMAN_REVIEW,
                            evidence="while True: (no break/return found)",
                            line_number=node.lineno,
                            description="Potential infinite loop: while True without break",
                            recommendation="Ensure the loop has a termination condition.",
                        ))

        # Detect os.fork
        fork_calls = python_scanner.find_function_calls(tree, {"os.fork"})
        for call in fork_calls:
            findings.append(Finding(
                rule_id=self.rule_id,
                category=self.category,
                severity=self.severity,
                decision=Decision.DENY,
                evidence="os.fork()",
                line_number=call.lineno,
                description="Process forking detected — potential fork bomb risk",
                recommendation="Avoid os.fork(). Use multiprocessing with controlled pool size.",
            ))

        return findings

    def _is_always_true(self, node: ast.expr) -> bool:
        """Check if an AST expression is always True."""
        if isinstance(node, ast.Constant):
            return bool(node.value)
        if isinstance(node, ast.NameConstant):  # Python 3.7 compat
            return bool(node.value)
        return False

    def _has_exit_statement(self, loop_node: ast.While) -> bool:
        """Check if a while loop body contains break or return."""
        for node in ast.walk(loop_node):
            if isinstance(node, (ast.Break, ast.Return)):
                return True
        return False

    def _scan_bash(self, ctx: "ScanContext") -> list[Finding]:
        findings: list[Finding] = []
        patterns = bash_scanner.CompiledPatternSet(_BASH_RESOURCE_PATTERNS)
        matches = bash_scanner.scan_lines(ctx.source_code, patterns)

        for m in matches:
            is_fork_bomb = "fork_bomb" in m.pattern_name
            findings.append(Finding(
                rule_id=self.rule_id,
                category=self.category,
                severity=Severity.HIGH,
                decision=Decision.DENY if is_fork_bomb else Decision.NEEDS_HUMAN_REVIEW,
                evidence=m.line_content,
                line_number=m.line_number,
                description=f"Resource exhaustion pattern: {m.pattern_name}",
                recommendation="Remove this pattern. It may cause system resource exhaustion.",
            ))

        return findings


# ---------------------------------------------------------------------------
# Rule: RES-002 — Excessive resource consumption
# ---------------------------------------------------------------------------


@register_rule
class ResourceConsumptionRule(BaseRule):
    """Detects patterns indicating excessive resource usage."""

    rule_id = "RES-002"
    category = RiskCategory.RESOURCE
    severity = Severity.MEDIUM
    languages = [Language.PYTHON, Language.BASH]
    description = "Detects patterns that may lead to excessive memory/disk/CPU usage."

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

        # Detect large allocation patterns
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
                # Detect patterns like "x" * 10000000 or [0] * huge_number
                if isinstance(node.right, ast.Constant) and isinstance(node.right.value, int):
                    if node.right.value > 10_000_000:
                        findings.append(Finding(
                            rule_id=self.rule_id,
                            category=self.category,
                            severity=self.severity,
                            decision=Decision.NEEDS_HUMAN_REVIEW,
                            evidence=f"multiplication with large constant: {node.right.value}",
                            line_number=node.lineno,
                            description="Potential excessive memory allocation",
                            recommendation="Verify this large allocation is intentional and bounded.",
                        ))

        # Detect multiprocessing without pool size limits
        mp_calls = python_scanner.find_function_calls(tree, _PYTHON_MULTIPROCESS_FUNCS)
        for call in mp_calls:
            call_name = python_scanner.get_call_name(call)
            findings.append(Finding(
                rule_id=self.rule_id,
                category=self.category,
                severity=Severity.LOW,
                decision=Decision.ALLOW,
                confidence=0.5,
                evidence=call_name,
                line_number=call.lineno,
                description=f"Process/thread creation: {call_name}",
                recommendation="Ensure bounded concurrency (use Pool with max_workers).",
            ))

        return findings

    def _scan_bash(self, ctx: "ScanContext") -> list[Finding]:
        findings: list[Finding] = []

        # Detect large file creation patterns
        large_file_patterns = bash_scanner.CompiledPatternSet({
            "dd_large": r"\bdd\s+.*bs=\d+[gGmM]",
            "fallocate": r"\bfallocate\s+.*-l\s+\d+[gG]",
            "truncate_large": r"\btruncate\s+.*-s\s+\d+[gG]",
        })
        matches = bash_scanner.scan_lines(ctx.source_code, large_file_patterns)

        for m in matches:
            findings.append(Finding(
                rule_id=self.rule_id,
                category=self.category,
                severity=self.severity,
                decision=Decision.NEEDS_HUMAN_REVIEW,
                evidence=m.line_content,
                line_number=m.line_number,
                description=f"Large resource allocation: {m.pattern_name}",
                recommendation="Verify the resource allocation size is reasonable.",
            ))

        return findings
