# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Syntax-aware (L2) scanner for Python scripts.

Where the L1 regex layer matches text, this layer parses the script into an AST
and reasons about *structure*. That defeats the trivial obfuscations that break
pure pattern matching (the weakness the Hermes community flagged):

- ``import subprocess as sp`` / ``from os import system as run`` — alias tracking
- ``getattr(os, "sys" + "tem")`` style indirection
- ``eval`` / ``exec`` on dynamic input
- ``open("~/.ssh/id_rsa")`` against policy ``forbidden_paths``
- ``while True:`` with no ``break`` — a resource-abuse loop

Each finding carries a stable ``AST*`` rule id and the ``layer="ast"`` marker so
reports can distinguish it from regex hits.
"""

from __future__ import annotations

import ast
from typing import Optional

from ._policy import SafetyPolicy
from ._types import RiskCategory
from ._types import RiskLevel
from ._types import RuleHit

# Dotted names whose invocation spawns a process / shell.
_PROCESS_CALLS = {
    "os.system",
    "os.popen",
    "subprocess.run",
    "subprocess.call",
    "subprocess.Popen",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.getoutput",
    "subprocess.getstatusoutput",
}

# Root module names whose invocation indicates network egress capability.
_NETWORK_ROOTS = {"socket", "requests", "urllib", "aiohttp", "httpx"}

# Callables that recursively remove files.
_DESTRUCTIVE_CALLS = {"shutil.rmtree", "os.removedirs"}


class _PythonSafetyVisitor(ast.NodeVisitor):
    """Walk a Python AST collecting :class:`RuleHit` findings."""

    def __init__(self, policy: SafetyPolicy) -> None:
        self._policy = policy
        self.hits: list[RuleHit] = []
        # alias -> canonical dotted name, e.g. {"sp": "subprocess", "run": "os.system"}
        self._aliases: dict[str, str] = {}

    # -- import alias tracking -------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            local = alias.asname or alias.name
            self._aliases[local] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        module = node.module or ""
        for alias in node.names:
            local = alias.asname or alias.name
            canonical = f"{module}.{alias.name}" if module else alias.name
            self._aliases[local] = canonical
        self.generic_visit(node)

    # -- call analysis ---------------------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        dotted = self._resolve_dotted(node.func)
        canonical = self._canonicalize(dotted) if dotted else None

        if canonical in _PROCESS_CALLS:
            self._add(
                "AST001",
                RiskCategory.PROCESS_SYSTEM_COMMAND,
                RiskLevel.HIGH,
                "Process/shell spawn from Python",
                canonical or dotted or "",
                node.lineno,
                "Spawning shells/processes from Python is high-risk; ensure inputs are trusted.",
            )
            if canonical == "os.system" or self._has_dynamic_arg(node):
                # Dynamic command construction cannot be judged statically.
                self._add(
                    "AST008",
                    RiskCategory.PROCESS_SYSTEM_COMMAND,
                    RiskLevel.MEDIUM,
                    "Dynamically constructed system command",
                    canonical or dotted or "",
                    node.lineno,
                    "Command built from non-literal input; requires human review.",
                )

        if dotted in ("eval", "exec") or canonical in ("builtins.eval", "builtins.exec"):
            self._add(
                "AST002",
                RiskCategory.PROCESS_SYSTEM_COMMAND,
                RiskLevel.HIGH,
                "Dynamic code execution (eval/exec)",
                dotted or "",
                node.lineno,
                "eval/exec on dynamic input enables arbitrary code execution.",
            )

        if dotted == "getattr" and self._getattr_is_obfuscated(node):
            self._add(
                "AST007",
                RiskCategory.PROCESS_SYSTEM_COMMAND,
                RiskLevel.MEDIUM,
                "Obfuscated attribute access via getattr",
                "getattr(...)",
                node.lineno,
                "Dynamic getattr with a computed name can hide dangerous calls; review.",
            )

        if canonical in _DESTRUCTIVE_CALLS:
            self._add(
                "AST006",
                RiskCategory.DANGEROUS_FILE_OP,
                RiskLevel.HIGH,
                "Recursive directory removal",
                canonical or "",
                node.lineno,
                "Recursive deletion can destroy data; scope it to the workspace.",
            )

        if dotted == "open" or canonical == "builtins.open":
            self._check_open_path(node)

        # Network client: module.method(...) where module resolves to a net lib.
        if canonical:
            root = canonical.split(".")[0]
            if root in _NETWORK_ROOTS:
                self._add(
                    "AST004",
                    RiskCategory.NETWORK_EXFILTRATION,
                    RiskLevel.HIGH,
                    "Network client invocation",
                    canonical,
                    node.lineno,
                    "Network egress must target only whitelisted domains; verify the destination.",
                )

        self.generic_visit(node)

    # -- infinite loop detection ----------------------------------------------
    def visit_While(self, node: ast.While) -> None:  # noqa: N802
        is_true = (isinstance(node.test, ast.Constant) and bool(node.test.value)) or (isinstance(
            node.test, ast.NameConstant) if hasattr(ast, "NameConstant") else False)
        if is_true and not _contains_break(node):
            self._add(
                "AST005",
                RiskCategory.RESOURCE_ABUSE,
                RiskLevel.MEDIUM,
                "Infinite loop without break",
                "while True: ...",
                node.lineno,
                "Unbounded loops can exhaust CPU; add a termination condition.",
            )
        self.generic_visit(node)

    # -- helpers ---------------------------------------------------------------
    def _check_open_path(self, node: ast.Call) -> None:
        """Flag ``open()`` on a forbidden path literal."""
        if not node.args:
            return
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            path = first.value
            for forbidden in self._policy.forbidden_paths:
                if forbidden.lower() in path.lower():
                    self._add(
                        "AST003",
                        RiskCategory.SENSITIVE_INFO_LEAK,
                        RiskLevel.CRITICAL,
                        "Access to a forbidden/sensitive path",
                        path,
                        node.lineno,
                        f"Path matches forbidden entry '{forbidden}'; remove the access.",
                    )
                    return

    def _resolve_dotted(self, func: ast.AST) -> Optional[str]:
        """Return the dotted call name, e.g. 'subprocess.Popen' or 'open'."""
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            parts: list[str] = [func.attr]
            cur: ast.AST = func.value
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
                return ".".join(reversed(parts))
        return None

    def _canonicalize(self, dotted: str) -> str:
        """Rewrite the leading segment through the alias map.

        ``sp.Popen`` with ``import subprocess as sp`` becomes
        ``subprocess.Popen``; ``run(...)`` with ``from os import system as run``
        becomes ``os.system``.
        """
        head, _, tail = dotted.partition(".")
        canonical_head = self._aliases.get(head, head)
        if not tail:
            return canonical_head
        return f"{canonical_head}.{tail}"

    @staticmethod
    def _has_dynamic_arg(node: ast.Call) -> bool:
        """Whether the call has a non-constant first positional argument."""
        if not node.args:
            return False
        first = node.args[0]
        return not (isinstance(first, ast.Constant) and isinstance(first.value, str))

    @staticmethod
    def _getattr_is_obfuscated(node: ast.Call) -> bool:
        """Whether ``getattr`` is called with a computed (non-literal) name."""
        if len(node.args) < 2:
            return False
        name_arg = node.args[1]
        return not (isinstance(name_arg, ast.Constant) and isinstance(name_arg.value, str))

    def _add(self, rule_id: str, category: RiskCategory, level: RiskLevel, title: str, evidence: str,
             line: Optional[int], recommendation: str) -> None:
        self.hits.append(
            RuleHit(
                rule_id=rule_id,
                category=category,
                risk_level=level,
                title=title,
                evidence=evidence,
                line=line,
                recommendation=recommendation,
                layer="ast",
            ))


def _contains_break(node: ast.While) -> bool:
    """Whether the while body contains a ``break`` not nested in an inner loop."""
    for child in node.body:
        if _stmt_has_break(child):
            return True
    return False


def _stmt_has_break(node: ast.AST) -> bool:
    """Recursively search for a ``break`` while skipping nested loops."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.Break):
            return True
        if isinstance(child, (ast.For, ast.While)):
            # A break inside a nested loop does not terminate the outer loop.
            continue
        if _stmt_has_break(child):
            return True
    return False


def scan_python(script: str, policy: SafetyPolicy) -> list[RuleHit]:
    """Run the AST layer over a Python script.

    Args:
        script: The Python source to analyse.
        policy: Active safety policy (used for forbidden-path checks).

    Returns:
        A list of :class:`RuleHit`. On syntax error the list contains a single
        ``AST000`` medium hit so that unparseable scripts are never silently
        allowed.
    """
    try:
        tree = ast.parse(script)
    except SyntaxError as exc:
        return [
            RuleHit(
                rule_id="AST000",
                category=RiskCategory.PROCESS_SYSTEM_COMMAND,
                risk_level=RiskLevel.MEDIUM,
                title="Unparseable Python script",
                evidence=str(exc).splitlines()[0] if str(exc) else "syntax error",
                line=getattr(exc, "lineno", None),
                recommendation="Script could not be parsed for analysis; requires human review.",
                layer="ast",
            )
        ]
    visitor = _PythonSafetyVisitor(policy)
    visitor.visit(tree)
    return visitor.hits
