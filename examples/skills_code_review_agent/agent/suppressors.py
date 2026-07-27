"""Context-aware suppression of findings the line-level rules over-report.

The regex rules in :mod:`rules_engine` and in the sandbox skill script look at a
single added line, which is the only thing a diff reliably gives them. That is
good for recall and bad for precision: a handle closed in a ``finally`` block, a
constant f-string, and parameterised SQL all look identical to a one-line match.

This pass re-examines each finding against the reconstructed post-image from
:mod:`context_analyzer` and drops the ones the surrounding code exonerates.
Every decision is returned as an audit record, so the report can show what was
suppressed and on what evidence rather than silently shrinking.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from .context_analyzer import FileContext
from .models import Finding

CLOSE_METHODS = {"close", "aclose", "shutdown", "disconnect", "dispose", "release"}
LIFECYCLE_CATEGORIES = {"async_resource", "resource_leak", "db_lifecycle"}
PLACEHOLDER_TOKENS = ("%s", "%d", "?", ":1")

ASSIGN_TARGET_RE = re.compile(r"^\s*(?:self\.)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=")
TEST_PATH_RE = re.compile(r"(^|/)(tests?|test)/|(^|/)test_[^/]+\.py$|_test\.py$")


@dataclass
class Suppression:
    """One recorded suppression decision."""

    rule_id: str
    file: str
    line: int | None
    category: str
    title: str
    reason: str
    evidence_tier: str
    action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "file": self.file,
            "line": self.line,
            "category": self.category,
            "title": self.title,
            "reason": self.reason,
            "evidence_tier": self.evidence_tier,
            "action": self.action,
        }


@dataclass
class Adjustment:
    """The verdict a suppressor reached for one finding."""

    rule_id: str
    reason: str
    evidence_tier: str
    drop: bool = False
    severity: str = ""
    disposition: str = ""
    confidence_delta: float = 0.0

    @property
    def action(self) -> str:
        return "dropped" if self.drop else "downgraded"


def apply_context_rules(findings: list[Finding],
                        contexts: dict[str, FileContext]) -> tuple[list[Finding], list[Suppression]]:
    """Re-score findings against reconstructed context, returning an audit trail."""
    kept: list[Finding] = []
    suppressions: list[Suppression] = []

    for finding in findings:
        context = _context_for(finding.file, contexts)
        adjustment = _evaluate(finding, context)
        if adjustment is None:
            kept.append(finding)
            continue

        suppressions.append(
            Suppression(
                rule_id=adjustment.rule_id,
                file=finding.file,
                line=finding.line,
                category=finding.category,
                title=finding.title,
                reason=adjustment.reason,
                evidence_tier=adjustment.evidence_tier,
                action=adjustment.action,
            ))
        if adjustment.drop:
            continue

        finding.severity = adjustment.severity or finding.severity
        finding.disposition = adjustment.disposition or finding.disposition
        finding.confidence = max(0.0, min(1.0, finding.confidence + adjustment.confidence_delta))
        finding.source = f"{finding.source}+{adjustment.rule_id}"
        kept.append(finding)

    return kept, suppressions


def _evaluate(finding: Finding, context: FileContext | None) -> Adjustment | None:
    for suppressor in _SUPPRESSORS:
        adjustment = suppressor(finding, context)
        if adjustment is not None:
            return adjustment
    return None


# --------------------------------------------------------------------------
# Individual suppressors
# --------------------------------------------------------------------------


def _resource_closed(finding: Finding, context: FileContext | None) -> Adjustment | None:
    """Drop lifecycle findings when the handle is demonstrably released."""
    if finding.category not in LIFECYCLE_CATEGORIES or finding.line is None:
        return None

    name = _assigned_name(finding, context)
    if not name:
        return None

    if context is not None:
        scope = context.enclosing_scope(finding.line)
        fragment = context.fragment_for(finding.line)
        if (scope is not None and fragment is not None
                and (_closes_in_scope(scope, name) or _is_scoped_by_with(scope, name))):
            return Adjustment(
                rule_id="ctx.resource_closed",
                reason=f"`{name}` is released in the same scope",
                evidence_tier=fragment.tier,
                drop=True,
            )

    window = context.window(finding.line) if context else []
    if window and _closes_in_text(window, name):
        return Adjustment(
            rule_id="ctx.resource_closed",
            reason=f"`{name}` is released within the same hunk",
            evidence_tier="window",
            drop=True,
        )
    return None


def _sql_is_safe(finding: Finding, context: FileContext | None) -> Adjustment | None:
    """Drop SQL findings for constant queries and parameterised statements."""
    if finding.category != "security" or finding.line is None:
        return None
    if "SQL" not in finding.title:
        return None

    if context is not None:
        fragment = context.fragment_for(finding.line)
        for node, frag in context.nodes_at(finding.line):
            if not isinstance(node, ast.Call) or not _is_execute_call(node):
                continue
            verdict = _classify_sql_call(node)
            if verdict:
                return Adjustment(rule_id="ctx.sql_safe", reason=verdict,
                                  evidence_tier=frag.tier, drop=True)
            return None
        if fragment is not None:
            # The line parsed, and no unsafe execute() call was found on it.
            return None

    line = context.source_lines.get(finding.line, "") if context else ""
    verdict = _classify_sql_text(line)
    if verdict:
        return Adjustment(rule_id="ctx.sql_safe", reason=verdict, evidence_tier="window", drop=True)
    return None


def _secret_is_reference(finding: Finding, context: FileContext | None) -> Adjustment | None:
    """Drop secret findings whose value is read from the environment or a call."""
    if finding.category != "sensitive_info" or finding.line is None or context is None:
        return None

    for node, fragment in context.nodes_at(finding.line):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if value is None:
            continue
        if isinstance(value, ast.Constant):
            return None
        if _is_env_lookup(value) or isinstance(value, (ast.Name, ast.Attribute, ast.Call)):
            return Adjustment(
                rule_id="ctx.secret_from_env",
                reason="value is read from the environment or another expression, not a literal",
                evidence_tier=fragment.tier,
                drop=True,
            )
    return None


def _task_is_retained(finding: Finding, context: FileContext | None) -> Adjustment | None:
    """Drop background-task findings when the task handle is kept."""
    if finding.category != "async_error" or finding.line is None or context is None:
        return None

    for node, fragment in context.nodes_at(finding.line):
        if (isinstance(node, (ast.Assign, ast.AnnAssign, ast.Return, ast.Await))
                and _contains_create_task(node)):
            return Adjustment(rule_id="ctx.task_retained",
                              reason="the task handle is retained or awaited",
                              evidence_tier=fragment.tier, drop=True)
        if isinstance(node, ast.Call) and _is_collecting_call(node) and _contains_create_task(node):
            return Adjustment(rule_id="ctx.task_retained",
                              reason="the task is stored in a collection",
                              evidence_tier=fragment.tier, drop=True)
    return None


def _call_has_timeout(finding: Finding, context: FileContext | None) -> Adjustment | None:
    """Drop missing-timeout findings when the call spans lines and does set one."""
    if finding.line is None or context is None:
        return None
    if not finding.source.startswith("rule:request-timeout"):
        return None

    for node, fragment in context.nodes_at(finding.line):
        if not isinstance(node, ast.Call):
            continue
        if any(keyword.arg == "timeout" for keyword in node.keywords):
            return Adjustment(rule_id="ctx.timeout_present",
                              reason="the call sets a timeout on a continuation line",
                              evidence_tier=fragment.tier, drop=True)
    return None


def _inside_test_file(finding: Finding, context: FileContext | None) -> Adjustment | None:
    """Downgrade production-risk findings that live in test code."""
    if finding.category in {"testing", "sensitive_info"}:
        return None
    if not TEST_PATH_RE.search(finding.file.replace("\\", "/")):
        return None
    if finding.severity in {"low", "info"}:
        return None
    return Adjustment(
        rule_id="ctx.test_file",
        reason="finding is inside test code, where the production risk does not apply",
        evidence_tier="path",
        severity="low",
        disposition="needs_human_review",
        confidence_delta=-0.15,
    )


_SUPPRESSORS: list[Callable[[Finding, FileContext | None], Adjustment | None]] = [
    _resource_closed,
    _sql_is_safe,
    _secret_is_reference,
    _task_is_retained,
    _call_has_timeout,
    _inside_test_file,
]


# --------------------------------------------------------------------------
# AST helpers
# --------------------------------------------------------------------------


def _assigned_name(finding: Finding, context: FileContext | None) -> str:
    """Return the variable the flagged line binds, via AST when possible."""
    if context is not None and finding.line is not None:
        for node, _ in context.nodes_at(finding.line):
            if isinstance(node, ast.Assign) and node.targets:
                target = node.targets[0]
                if isinstance(target, ast.Name):
                    return target.id
                if isinstance(target, ast.Attribute):
                    return target.attr
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                return node.target.id

    source = ""
    if context is not None and finding.line is not None:
        source = context.source_lines.get(finding.line, "")
    source = source or finding.evidence
    match = ASSIGN_TARGET_RE.match(source)
    return match.group("name") if match else ""


def _closes_in_scope(scope: ast.AST, name: str) -> bool:
    for node in ast.walk(scope):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in CLOSE_METHODS:
            continue
        target = func.value
        if isinstance(target, ast.Name) and target.id == name:
            return True
        if isinstance(target, ast.Attribute) and target.attr == name:
            return True
    return False


def _is_scoped_by_with(scope: ast.AST, name: str) -> bool:
    for node in ast.walk(scope):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        for item in node.items:
            var = item.optional_vars
            if isinstance(var, ast.Name) and var.id == name:
                return True
    return False


def _closes_in_text(window: list[str], name: str) -> bool:
    pattern = re.compile(r"\b" + re.escape(name) + r"\s*\.\s*(" + "|".join(CLOSE_METHODS) + r")\s*\(")
    return any(pattern.search(line) for line in window)


def _is_execute_call(node: ast.Call) -> bool:
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr in {"execute", "executemany"}


def _classify_sql_call(node: ast.Call) -> str:
    """Return a reason when an execute() call is safe, or an empty string."""
    if not node.args:
        return ""
    first = node.args[0]

    if (len(node.args) >= 2 and isinstance(first, (ast.Constant, ast.JoinedStr))
            and not _has_interpolation(first)):
        return "query is parameterised; values are passed separately"

    if isinstance(first, ast.Constant):
        return "query is a constant string"

    if isinstance(first, ast.JoinedStr) and not _has_interpolation(first):
        return "f-string interpolates nothing, so the query is constant"

    return ""


def _has_interpolation(node: ast.AST) -> bool:
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(value, ast.FormattedValue) for value in node.values)
    return False


def _classify_sql_text(line: str) -> str:
    """Textual fallback for the safe-SQL check when no AST is available."""
    match = re.search(r"execute(?:many)?\s*\(\s*(?P<prefix>[a-zA-Z]*)(?P<quote>[\"'])(?P<body>.*?)(?P=quote)"
                      r"(?P<rest>.*)$", line)
    if not match:
        return ""
    body = match.group("body")
    rest = match.group("rest")
    is_fstring = "f" in match.group("prefix").lower()

    if is_fstring and "{" not in body:
        return "f-string interpolates nothing, so the query is constant"
    if not is_fstring and rest.lstrip().startswith(",") and any(token in body for token in PLACEHOLDER_TOKENS):
        return "query is parameterised; values are passed separately"
    return ""


def _is_env_lookup(node: ast.AST) -> bool:
    if isinstance(node, ast.Subscript):
        return _dotted_name(node.value).endswith("os.environ")
    if isinstance(node, ast.Call):
        name = _dotted_name(node.func)
        return name.endswith(("os.getenv", "os.environ.get"))
    return False


def _contains_create_task(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and _dotted_name(child.func).endswith("create_task"):
            return True
    return False


def _is_collecting_call(node: ast.Call) -> bool:
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr in {"append", "add", "extend", "put_nowait"}


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _context_for(path: str, contexts: dict[str, FileContext]) -> FileContext | None:
    if path in contexts:
        return contexts[path]
    normalized = str(PurePosixPath(path.replace("\\", "/")))
    for key, value in contexts.items():
        if str(PurePosixPath(key.replace("\\", "/"))) == normalized:
            return value
    return None
