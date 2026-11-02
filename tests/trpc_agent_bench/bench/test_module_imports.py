"""Generic modules must not import agent implementations."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[3] / "trpc_agent_bench"
FORBIDDEN = (
    "trpc_harness",
    "trpc_agent_sdk.agents",
    "trpc_agent_sdk.models",
    "trpc_agent_sdk.runners",
    "trpc_agent_sdk.sessions",
)


def _generic_files() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_generic_modules_do_not_import_agent_implementations() -> None:
    offenders = []
    for path in _generic_files():
        imports = _imports(path)
        if any(name == forbidden or name.startswith(f"{forbidden}.") for name in imports for forbidden in FORBIDDEN):
            offenders.append(path.name)
    assert not offenders, f"generic modules import agent implementations: {offenders}"
