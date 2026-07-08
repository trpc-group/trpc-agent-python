"""Python AST-assisted review checks for changed hunks."""

from __future__ import annotations

import ast
import textwrap
from dataclasses import dataclass

from .models import ChangedLine
from .models import Hunk


@dataclass(slots=True)
class AstFinding:
    line: ChangedLine
    severity: str
    category: str
    title: str
    evidence: str
    recommendation: str
    confidence: float
    source: str
    rule_id: str


class PythonAstAnalyzer:
    """Small AST pass for high-signal Python sink checks."""

    def analyze_hunk(self, hunk: Hunk) -> list[AstFinding]:
        if not hunk.file.endswith(".py"):
            return []
        if not any(line.kind == "add" for line in hunk.lines):
            return []

        parsed = _parse_hunk_source(hunk)
        if parsed is None:
            return []
        tree, line_map = parsed
        analyzer = _AstVisitor(line_map)
        analyzer.visit(tree)
        return analyzer.findings


class _AstVisitor(ast.NodeVisitor):

    def __init__(self, line_map: dict[int, ChangedLine]):
        self.line_map = line_map
        self.tainted_names: set[str] = set()
        self.derived_tainted_names: set[str] = set()
        self.findings: list[AstFinding] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        previous_tainted = set(self.tainted_names)
        previous_derived = set(self.derived_tainted_names)
        self.tainted_names.update(arg.arg for arg in node.args.args)
        self.generic_visit(node)
        self.tainted_names = previous_tainted
        self.derived_tainted_names = previous_derived

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self.visit_FunctionDef(node)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        if _expr_is_tainted(node.value, self.tainted_names) or _expr_reads_external_input(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.tainted_names.add(target.id)
                    self.derived_tainted_names.add(target.id)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        line = self.line_map.get(getattr(node, "lineno", 1))
        evidence = line.content if line is not None else ""
        if _is_subprocess_call(node) and _keyword_is_true(node, "shell") and _subprocess_args_need_taint_finding(
                node, self.tainted_names, self.derived_tainted_names, evidence):
            self._add(
                node,
                severity="high",
                category="security",
                title="Tainted value reaches shell command execution",
                recommendation=(
                    "Avoid shell=True and pass a fixed argument list; validate or reject untrusted command fragments."
                ),
                confidence=0.95,
                source="ast:taint",
                rule_id="security.subprocess.tainted-shell",
            )
        if _is_execute_call(node) and _execute_args_need_taint_finding(
                node, self.tainted_names, self.derived_tainted_names, evidence):
            self._add(
                node,
                severity="high",
                category="security",
                title="Tainted value reaches database execute",
                recommendation="Use parameterized SQL and pass untrusted values through bind parameters.",
                confidence=0.9,
                source="ast:taint",
                rule_id="security.sql-tainted-execute",
            )
        self.generic_visit(node)

    def _add(
        self,
        node: ast.AST,
        *,
        severity: str,
        category: str,
        title: str,
        recommendation: str,
        confidence: float,
        source: str,
        rule_id: str,
    ) -> None:
        line = self.line_map.get(getattr(node, "lineno", 1))
        if line is None:
            return
        self.findings.append(
            AstFinding(
                line=line,
                severity=severity,
                category=category,
                title=title,
                evidence=line.content.strip(),
                recommendation=recommendation,
                confidence=confidence,
                source=source,
                rule_id=rule_id,
            ))


def _is_subprocess_call(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Attribute) and isinstance(node.func.value,
                                                               ast.Name) and node.func.value.id == "subprocess"


def _parse_hunk_source(hunk: Hunk) -> tuple[ast.AST, dict[int, ChangedLine]] | None:
    candidates = [
        [line for line in hunk.lines if line.kind != "delete"],
        [line for line in hunk.lines if line.kind == "add"],
    ]
    for lines in candidates:
        if not lines or not any(line.kind == "add" for line in lines):
            continue
        source = textwrap.dedent("\n".join(line.content for line in lines))
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        line_map = {index: line for index, line in enumerate(lines, start=1) if line.kind == "add"}
        return tree, line_map
    return None


def _is_execute_call(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Attribute) and node.func.attr == "execute"


def _keyword_is_true(node: ast.Call, name: str) -> bool:
    return any(keyword.arg == name and isinstance(keyword.value, ast.Constant) and keyword.value.value is True
               for keyword in node.keywords)


def _subprocess_args_need_taint_finding(
    node: ast.Call,
    tainted_names: set[str],
    derived_tainted_names: set[str],
    evidence: str,
) -> bool:
    for arg in node.args:
        if isinstance(arg, ast.Name) and arg.id in derived_tainted_names:
            return True
        if _expr_reads_external_input(arg) or _expr_uses_derived_taint(arg, derived_tainted_names):
            return True
        if "shell=True" not in evidence and _expr_is_tainted(arg, tainted_names):
            return True
    return False


def _execute_args_need_taint_finding(
    node: ast.Call,
    tainted_names: set[str],
    derived_tainted_names: set[str],
    evidence: str,
) -> bool:
    if _line_has_direct_sql_interpolation(evidence):
        return False
    return any(
        _expr_reads_external_input(arg)
        or _expr_uses_derived_taint(arg, derived_tainted_names)
        or (isinstance(arg, ast.Name) and arg.id in derived_tainted_names)
        for arg in node.args
    )


def _expr_uses_derived_taint(node: ast.AST, derived_tainted_names: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in derived_tainted_names
    if isinstance(node, ast.Attribute):
        return _expr_uses_derived_taint(node.value, derived_tainted_names)
    if isinstance(node, ast.Subscript):
        return _expr_uses_derived_taint(node.value, derived_tainted_names)
    if isinstance(node, ast.BinOp):
        return _expr_uses_derived_taint(node.left, derived_tainted_names) or _expr_uses_derived_taint(
            node.right, derived_tainted_names)
    if isinstance(node, ast.JoinedStr):
        return any(_expr_uses_derived_taint(value, derived_tainted_names) for value in node.values)
    if isinstance(node, ast.FormattedValue):
        return _expr_uses_derived_taint(node.value, derived_tainted_names)
    if isinstance(node, ast.Call):
        return any(_expr_uses_derived_taint(arg, derived_tainted_names) for arg in node.args)
    return False


def _line_has_direct_sql_interpolation(evidence: str) -> bool:
    lowered = evidence.lower()
    if ".execute(" not in lowered:
        return False
    return any(token in evidence for token in ("f\"", "f'", "%", " + ", ".format("))


def _expr_is_tainted(node: ast.AST, tainted_names: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in tainted_names or _name_looks_external(node.id)
    if isinstance(node, ast.Attribute):
        return _expr_is_tainted(node.value, tainted_names) or node.attr in tainted_names or _name_looks_external(
            node.attr)
    if isinstance(node, ast.Subscript):
        return _expr_is_tainted(node.value, tainted_names)
    if isinstance(node, ast.BinOp):
        return _expr_is_tainted(node.left, tainted_names) or _expr_is_tainted(node.right, tainted_names)
    if isinstance(node, ast.JoinedStr):
        return any(_expr_is_tainted(value, tainted_names) for value in node.values)
    if isinstance(node, ast.FormattedValue):
        return _expr_is_tainted(node.value, tainted_names)
    if isinstance(node, ast.Call):
        return _expr_reads_external_input(node) or any(_expr_is_tainted(arg, tainted_names) for arg in node.args)
    return False


def _expr_reads_external_input(node: ast.AST) -> bool:
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in {"input"}:
            return True
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"get", "get_json", "json", "form"}:
            return _expr_is_tainted(node.func.value, {"request", "args", "params", "environ", "env"})
    if isinstance(node, ast.Subscript):
        return _expr_is_tainted(node.value, {"request", "args", "params", "environ", "env"})
    return False


def _name_looks_external(name: str) -> bool:
    lowered = name.lower()
    return lowered in {"request", "args", "params", "payload", "body", "environ", "env"} or lowered.startswith("user_")
