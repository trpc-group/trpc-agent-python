"""Python AST parsing utilities for safety rule scanning.

Provides safe AST parsing and node extraction helpers that rules can use
to analyze Python scripts without each rule re-implementing AST traversal.
"""

from __future__ import annotations

import ast
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def safe_parse(source: str) -> Optional[ast.Module]:
    """Safely parse Python source code into an AST.

    Returns None if parsing fails (syntax error, encoding issue, etc.).
    Does not raise exceptions.
    """
    try:
        return ast.parse(source, type_comments=False)
    except (SyntaxError, ValueError, TypeError, MemoryError) as e:
        logger.debug("AST parse failed: %s", e)
        return None


def extract_calls(tree: ast.Module) -> list[ast.Call]:
    """Extract all Call nodes from an AST tree."""
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def extract_imports(tree: ast.Module) -> list[tuple[str, Optional[str]]]:
    """Extract all imports as (module_name, alias_or_name) tuples.

    For `import os` → ("os", None)
    For `import os as operating_system` → ("os", "operating_system")
    For `from os import path` → ("os.path", None)
    For `from os import path as p` → ("os.path", "p")
    """
    results: list[tuple[str, Optional[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                results.append((alias.name, alias.asname))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                full_name = f"{module}.{alias.name}" if module else alias.name
                results.append((full_name, alias.asname))
    return results


def get_call_name(call: ast.Call) -> str:
    """Extract the full dotted name of a function call.

    Examples:
        os.system(...)     → "os.system"
        subprocess.run(...) → "subprocess.run"
        open(...)          → "open"
        obj.method(...)    → "obj.method"

    Returns empty string if the call form is too complex to resolve statically.
    """
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    elif isinstance(func, ast.Attribute):
        parts = []
        node = func
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value  # type: ignore
        if isinstance(node, ast.Name):
            parts.append(node.id)
            return ".".join(reversed(parts))
    return ""


def get_string_args(call: ast.Call) -> list[str]:
    """Extract string literal arguments from a Call node.

    Only returns values that are statically determinable string constants.
    Skips non-literal arguments (variables, f-strings, etc.).
    """
    results: list[str] = []
    for arg in call.args:
        value = _extract_string_value(arg)
        if value is not None:
            results.append(value)
    for kw in call.keywords:
        value = _extract_string_value(kw.value)
        if value is not None:
            results.append(value)
    return results


def get_string_value(node: ast.expr) -> Optional[str]:
    """Extract a string value from an AST expression node, if it's a constant."""
    return _extract_string_value(node)


def _extract_string_value(node: ast.expr) -> Optional[str]:
    """Internal: extract string from Constant or JoinedStr."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def find_function_calls(tree: ast.Module, func_names: set[str]) -> list[ast.Call]:
    """Find all calls to specific function names in the AST.

    Args:
        tree: Parsed AST module.
        func_names: Set of dotted names to match (e.g. {"os.system", "subprocess.run"}).

    Returns:
        List of matching Call nodes.
    """
    matches: list[ast.Call] = []
    for call in extract_calls(tree):
        name = get_call_name(call)
        if name in func_names:
            matches.append(call)
    return matches


def find_string_assignments(tree: ast.Module) -> dict[str, str]:
    """Find simple variable assignments where the value is a string literal.

    Returns a dict of {variable_name: string_value}.
    Only captures single Name targets with Constant string values.
    """
    assignments: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
                if isinstance(node.value.value, str):
                    assignments[target.id] = node.value.value
    return assignments
