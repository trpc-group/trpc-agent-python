#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Python import/public-API maintenance tool.

Subcommands (all support ``--dry-run``):

1) sort-imports
   - Normalize top import blocks in ``.py`` files.
   - Split multi-import lines into one import per line.
   - Group/order imports: stdlib > third-party > project > local-relative.
   - In-group sort key: ``import ...`` first, then ``from ... import ...``,
     then lexicographical order.
   - Keep ``from __future__ import annotations`` at the very top of imports.

Examples:
  python3 format.py sort-imports --root . --dry-run
  python3 format.py sort-imports --root .
  python3 format.py check-chinese --root . --dry-run
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


EXCLUDED_DIRS = {
    "venv",
    ".venv",
    "env",
    ".env",
    "__pycache__",
    ".git",
    ".hg",
    ".svn",
    "build",
    "dist",
    ".mypy_cache",
    "examples",
    "tests",
    ".pytest_cache",
}

STDLIB_COMPANION_MODULES = {"typing_extensions"}
AUTO_EXPORT_BEGIN = "# <auto_exports>"
AUTO_EXPORT_END = "# </auto_exports>"
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")


@dataclass(frozen=True)
class ImportLine:
    text: str
    group: int
    kind: int  # 0=import, 1=from-import
    rel_level: int = 0  # relative import level; larger means farther (.. > .)


@dataclass
class ScopeState:
    imports: dict[str, int]
    assigned: dict[str, int]
    used: set[str]
    params: set[str]


@dataclass
class CheckIssue:
    path: Path
    line: int
    category: str
    detail: str


def iter_python_files(root: Path) -> Iterable[Path]:
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        for name in files:
            if name.endswith(".py"):
                yield Path(base) / name


def discover_project_packages(root: Path) -> set[str]:
    package_names: set[str] = set()
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if child.name in EXCLUDED_DIRS or child.name.startswith("."):
            continue
        if (child / "__init__.py").exists():
            package_names.add(child.name)
    return package_names


def iter_package_dirs(root: Path) -> Iterable[Path]:
    for base, dirs, _files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        base_path = Path(base)
        if (base_path / "__init__.py").exists():
            yield base_path


def iter_python_source_dirs(root: Path) -> Iterable[Path]:
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        if any(name.endswith(".py") for name in files):
            yield Path(base)


def ensure_init_files(root: Path, apply: bool) -> list[Path]:
    """Ensure Python source subdirectories contain __init__.py."""
    created: list[Path] = []
    for src_dir in sorted(iter_python_source_dirs(root)):
        if src_dir == root:
            continue
        init_path = src_dir / "__init__.py"
        if init_path.exists():
            continue
        created.append(init_path)
        if apply:
            init_path.write_text("", encoding="utf-8")
    return created


def read_optional_group_tokens(pyproject_path: Path) -> set[str]:
    if not pyproject_path.exists():
        return set()
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    optional = (
        data.get("project", {}).get("optional-dependencies", {})
        if isinstance(data, dict)
        else {}
    )
    tokens: set[str] = set()
    for group_name in optional.keys():
        if not isinstance(group_name, str):
            continue
        tokens.add(group_name.lower().replace("-", "_"))
    return tokens


def is_optional_module(module_path: Path, optional_tokens: set[str]) -> bool:
    if not optional_tokens:
        return False
    normalized_parts = [p.lower().replace("-", "_") for p in module_path.parts]
    stem = module_path.stem.lower().replace("-", "_")

    # Ignore umbrella/non-feature groups to avoid false positives.
    ignore_tokens = {"all", "dev", "eval"}
    checked_tokens = {t for t in optional_tokens if t not in ignore_tokens}

    for token in checked_tokens:
        # Exact and prefix match against module stem (e.g. mem0_*).
        if stem == token or stem.startswith(f"{token}_"):
            return True
        # Exact and prefix match against each path component.
        for part in normalized_parts:
            if part == token or part.startswith(f"{token}_"):
                return True
    return False


def collect_public_symbols(module_file: Path) -> list[str]:
    try:
        tree = ast.parse(module_file.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []

    symbols: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                symbols.append(node.name)
    return sorted(set(symbols))


def strip_auto_export_markers(source: str) -> str:
    """Remove legacy marker comments, keep actual code lines."""
    lines = source.splitlines()
    kept = [line for line in lines if line.strip() not in {AUTO_EXPORT_BEGIN, AUTO_EXPORT_END}]
    return "\n".join(kept) + ("\n" if source.endswith("\n") and kept else "")


def collect_existing_init_symbols(init_source: str) -> set[str]:
    """Collect already-imported symbols in __init__.py to avoid duplicates."""
    symbols: set[str] = set()
    clean_source = strip_auto_export_markers(init_source)
    try:
        tree = ast.parse(clean_source)
    except SyntaxError:
        return symbols

    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and node.level >= 1:
            for alias in node.names:
                if alias.name == "*":
                    continue
                symbols.add(alias.asname or alias.name)
    return symbols


def build_init_auto_exports(
    package_dir: Path,
    optional_tokens: set[str],
    existing_symbols: set[str],
) -> str:
    exports: list[tuple[str, str]] = []
    for py_file in sorted(package_dir.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        if is_optional_module(py_file, optional_tokens):
            # Avoid importing optional-feature modules from package __init__.
            continue
        symbols = collect_public_symbols(py_file)
        if not symbols:
            continue
        module_name = py_file.stem
        for sym in symbols:
            if sym in existing_symbols:
                continue
            exports.append((module_name, sym))

    exports = sorted(set(exports), key=lambda x: (x[0], x[1]))
    if not exports:
        return ""

    lines: list[str] = []
    for module_name, sym in exports:
        lines.append(f"from .{module_name} import {sym}")
    lines.append("")
    lines.append("__all__ = [")
    for _module_name, sym in exports:
        lines.append(f'    "{sym}",')
    lines.append("]")
    return "\n".join(lines) + "\n"


def upsert_auto_export_block(init_path: Path, block: str, apply: bool) -> bool:
    original_source = init_path.read_text(encoding="utf-8") if init_path.exists() else ""
    source = strip_auto_export_markers(original_source)
    if not block:
        # No new auto-exports to add; only marker cleanup (if any) already applied.
        new_source = source
    else:
        new_source = insert_auto_export_block(source, block)

    if new_source == original_source:
        return False
    if apply:
        init_path.write_text(new_source, encoding="utf-8")
    return True


def find_first_all_assignment_line(source: str) -> int | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    all_lines: list[int] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            has_all_target = any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
            if has_all_target:
                all_lines.append(node.lineno - 1)
    return min(all_lines) if all_lines else None


def insert_auto_export_block(source: str, block: str) -> str:
    block_lines = block.rstrip("\n").splitlines()
    lines = source.splitlines()
    insert_idx = find_first_all_assignment_line(source)
    if insert_idx is None:
        insert_idx = len(lines)

    prefix = lines[:insert_idx]
    suffix = lines[insert_idx:]

    if prefix and prefix[-1] != "":
        prefix.append("")
    if suffix and suffix[0] != "":
        block_lines.append("")

    merged = prefix + block_lines + suffix
    return "\n".join(merged) + ("\n" if source.endswith("\n") or bool(merged) else "")


def rewrite_init_imports_by_local_filenames(package_dir: Path, init_path: Path, apply: bool) -> bool:
    """Fix __init__.py relative imports based on actual local module filenames.

    Example:
        from .chain_agent import ChainAgent
    becomes:
        from ._chain_agent import ChainAgent
    when ``chain_agent.py`` is missing but ``_chain_agent.py`` exists.
    """
    if not init_path.exists():
        return False

    source = init_path.read_text(encoding="utf-8")
    local_modules = {
        p.stem for p in package_dir.glob("*.py") if p.name != "__init__.py"
    }
    local_packages = {
        p.name for p in package_dir.iterdir() if p.is_dir() and (p / "__init__.py").exists()
    }

    changed = False
    new_lines: list[str] = []
    for line in source.splitlines():
        m = re.match(r"^(\s*from\s+\.)([A-Za-z_]\w*)(\.[\w\.]+)?(\s+import\s+.+)$", line)
        if not m:
            new_lines.append(line)
            continue
        prefix, first_module, rest, suffix = m.groups()

        if first_module in local_modules or first_module in local_packages:
            new_lines.append(line)
            continue

        underscored = f"_{first_module}"
        if underscored in local_modules:
            changed = True
            new_lines.append(f"{prefix}{underscored}{rest or ''}{suffix}")
            continue

        new_lines.append(line)

    if not changed:
        return False

    new_source = "\n".join(new_lines) + ("\n" if source.endswith("\n") else "")
    if apply:
        init_path.write_text(new_source, encoding="utf-8")
    return True


def build_package_api_exports(root: Path, apply: bool) -> list[Path]:
    optional_tokens = read_optional_group_tokens(root / "pyproject.toml")
    changed_init_files: list[Path] = []
    for package_dir in iter_package_dirs(root):
        init_path = package_dir / "__init__.py"
        fixed_local_imports = rewrite_init_imports_by_local_filenames(package_dir, init_path, apply=apply)
        source = init_path.read_text(encoding="utf-8") if init_path.exists() else ""
        existing_symbols = collect_existing_init_symbols(source)
        block = build_init_auto_exports(package_dir, optional_tokens, existing_symbols)
        updated = upsert_auto_export_block(init_path, block, apply)
        merged = merge_init_all_exports(init_path, apply=apply)
        if fixed_local_imports or updated or merged:
            changed_init_files.append(init_path)
    return changed_init_files


def collect_private_module_rename_candidates(root: Path) -> list[Path]:
    optional_tokens = read_optional_group_tokens(root / "pyproject.toml")
    candidates: list[Path] = []
    for package_dir in iter_package_dirs(root):
        for py_file in sorted(package_dir.glob("*.py")):
            if py_file.name == "__init__.py":
                continue
            if py_file.stem.startswith("_"):
                continue
            if is_optional_module(py_file, optional_tokens):
                continue
            candidates.append(py_file)
    return candidates


def _extract_all_names_from_value(node: ast.AST) -> list[str]:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return []
    out: list[str] = []
    for elt in node.elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            out.append(elt.value)
    return out


def _merge_init_all_exports_source(source: str) -> str | None:
    """Return merged __all__ source for __init__.py, or None when unchanged."""
    original_source = source
    source = strip_auto_export_markers(source)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    all_nodes: list[ast.Assign] = []
    legacy_all_names: list[str] = []
    import_symbol_order: list[str] = []
    seen_imports: set[str] = set()

    for node in tree.body:
        if isinstance(node, ast.Assign):
            has_all_target = any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
            if has_all_target:
                all_nodes.append(node)
                legacy_all_names.extend(_extract_all_names_from_value(node.value))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                sym = alias.asname or alias.name.split(".", 1)[0]
                if sym not in seen_imports:
                    seen_imports.add(sym)
                    import_symbol_order.append(sym)
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                sym = alias.asname or alias.name
                if sym not in seen_imports:
                    seen_imports.add(sym)
                    import_symbol_order.append(sym)

    if len(all_nodes) == 0:
        return None

    final_names: list[str] = []
    seen_final: set[str] = set()
    for sym in import_symbol_order + legacy_all_names:
        if sym not in seen_final:
            seen_final.add(sym)
            final_names.append(sym)

    lines = source.splitlines()
    all_ranges: list[tuple[int, int]] = []
    for node in all_nodes:
        start = node.lineno - 1
        end = (node.end_lineno or node.lineno) - 1
        all_ranges.append((start, end))
    drop_line_idx: set[int] = set()
    for start, end in all_ranges:
        for i in range(start, end + 1):
            drop_line_idx.add(i)

    kept_lines = [line for idx, line in enumerate(lines) if idx not in drop_line_idx]

    all_block = ["__all__ = ["]
    for name in final_names:
        all_block.append(f'    "{name}",')
    all_block.append("]")
    kept_source = "\n".join(kept_lines) + ("\n" if source.endswith("\n") else "")
    try:
        kept_tree = ast.parse(kept_source)
    except SyntaxError:
        return None
    last_import_end = 0
    for node in kept_tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last_import_end = max(last_import_end, node.end_lineno or node.lineno)

    insert_idx = min(last_import_end, len(kept_lines))
    merged_lines = kept_lines[:insert_idx] + ([""] if insert_idx > 0 and kept_lines[insert_idx - 1] != "" else []) + all_block
    if insert_idx < len(kept_lines) and kept_lines[insert_idx] != "":
        merged_lines.append("")
    merged_lines.extend(kept_lines[insert_idx:])
    new_source = "\n".join(merged_lines) + ("\n" if source.endswith("\n") else "")

    if new_source == original_source:
        return None
    return new_source


def merge_init_all_exports(init_path: Path, apply: bool) -> bool:
    """Merge multiple top-level __all__ blocks in __init__.py into one."""
    if not init_path.exists():
        return False
    original_source = init_path.read_text(encoding="utf-8")
    new_source = _merge_init_all_exports_source(original_source)
    if new_source is None:
        return False
    if apply:
        init_path.write_text(new_source, encoding="utf-8")
    return True


def path_to_module(path: Path, root: Path, project_packages: set[str]) -> str | None:
    rel = path.resolve().relative_to(root.resolve())
    if not rel.parts:
        return None
    if rel.parts[0] not in project_packages:
        return None
    if path.suffix != ".py":
        return None
    mod_parts = list(rel.parts)
    mod_parts[-1] = path.stem
    return ".".join(mod_parts)


def build_private_module_rename_map(root: Path, project_packages: set[str]) -> dict[Path, Path]:
    rename_map: dict[Path, Path] = {}
    for old_path in collect_private_module_rename_candidates(root):
        new_path = old_path.with_name(f"_{old_path.name}")
        rename_map[old_path] = new_path
    return rename_map


def resolve_relative_module(module: str, current_package: str) -> tuple[list[str], list[str]] | None:
    dots = len(module) - len(module.lstrip("."))
    tail = module[dots:]
    if dots <= 0:
        return None
    pkg_parts = current_package.split(".") if current_package else []
    if dots - 1 > len(pkg_parts):
        return None
    anchor = pkg_parts[: len(pkg_parts) - (dots - 1)]
    resolved = anchor + ([p for p in tail.split(".") if p] if tail else [])
    return anchor, resolved


def rewrite_import_line(
    line: str,
    abs_module_map: dict[str, str],
    module_to_package_map: dict[str, str],
    current_package: str,
) -> str:
    import_match = re.match(r"^(\s*import\s+)(.+?)(\s*)$", line)
    if import_match:
        prefix, imports_part, suffix = import_match.groups()
        chunks = [x.strip() for x in imports_part.split(",")]
        rewritten: list[str] = []
        changed = False
        for chunk in chunks:
            as_match = re.match(r"^([A-Za-z_][\w\.]*)(\s+as\s+\w+)?$", chunk)
            if not as_match:
                rewritten.append(chunk)
                continue
            module_name, alias = as_match.groups()
            new_module_name = abs_module_map.get(module_name, module_name)
            if new_module_name != module_name:
                changed = True
            rewritten.append(f"{new_module_name}{alias or ''}")
        if changed:
            return f"{prefix}{', '.join(rewritten)}{suffix}"
        return line

    from_match = re.match(r"^(\s*from\s+)([.\w]+)(\s+import\s+.+)$", line)
    if not from_match:
        return line

    prefix, module_name, tail = from_match.groups()
    # Absolute imports.
    if not module_name.startswith("."):
        # Prefer package-level imports over file-module imports.
        package_module_name = module_to_package_map.get(module_name)
        if package_module_name:
            return f"{prefix}{package_module_name}{tail}"
        new_module_name = abs_module_map.get(module_name, module_name)
        if new_module_name != module_name:
            return f"{prefix}{new_module_name}{tail}"
        return line

    # Relative imports: resolve to absolute and map if needed.
    resolved = resolve_relative_module(module_name, current_package)
    if not resolved:
        return line
    anchor, resolved_parts = resolved
    resolved_abs = ".".join(resolved_parts)
    package_abs = module_to_package_map.get(resolved_abs)
    if package_abs:
        new_parts = package_abs.split(".")
        if new_parts[: len(anchor)] == anchor:
            rel_tail_parts = new_parts[len(anchor):]
            dot_count = len(module_name) - len(module_name.lstrip("."))
            new_module_name = "." * dot_count + ".".join(rel_tail_parts)
            return f"{prefix}{new_module_name}{tail}"
    new_abs = abs_module_map.get(resolved_abs)
    if not new_abs:
        return line
    new_parts = new_abs.split(".")
    if new_parts[: len(anchor)] != anchor:
        return line
    rel_tail_parts = new_parts[len(anchor):]
    dot_count = len(module_name) - len(module_name.lstrip("."))
    new_module_name = "." * dot_count + ".".join(rel_tail_parts)
    return f"{prefix}{new_module_name}{tail}"


def rewrite_imports_for_renamed_modules(
    root: Path,
    py_files: list[Path],
    project_packages: set[str],
    rename_map: dict[Path, Path],
    apply: bool,
) -> list[Path]:
    abs_module_map: dict[str, str] = {}
    module_to_package_map: dict[str, str] = {}
    for old_path, new_path in rename_map.items():
        old_mod = path_to_module(old_path, root, project_packages)
        new_mod = path_to_module(new_path, root, project_packages)
        if old_mod and new_mod:
            abs_module_map[old_mod] = new_mod
            pkg_mod = old_mod.rsplit(".", 1)[0]
            module_to_package_map[old_mod] = pkg_mod
            module_to_package_map[new_mod] = pkg_mod

    changed: list[Path] = []
    for path in py_files:
        if path.name == "__init__.py":
            # __init__.py is handled by package API generation/fix logic.
            continue
        current_mod = path_to_module(path, root, project_packages)
        current_package = ".".join(current_mod.split(".")[:-1]) if current_mod else ""
        try:
            source = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        lines = source.splitlines()
        new_lines = [
            rewrite_import_line(
                line,
                abs_module_map=abs_module_map,
                module_to_package_map=module_to_package_map,
                current_package=current_package,
            )
            for line in lines
        ]
        new_source = "\n".join(new_lines) + ("\n" if source.endswith("\n") else "")
        if new_source != source:
            changed.append(path)
            if apply:
                path.write_text(new_source, encoding="utf-8")
    return changed


def apply_private_module_renames(rename_map: dict[Path, Path], apply: bool) -> list[tuple[Path, Path]]:
    renamed: list[tuple[Path, Path]] = []
    for old_path, new_path in sorted(rename_map.items(), key=lambda x: str(x[0])):
        if new_path.exists():
            continue
        renamed.append((old_path, new_path))
        if apply:
            old_path.rename(new_path)
    return renamed


def classify_absolute_import(root_name: str, stdlib_names: set[str], project_packages: set[str]) -> int:
    if root_name in stdlib_names or root_name in STDLIB_COMPANION_MODULES:
        return 1
    if root_name in project_packages:
        return 3
    return 2


def classify_from_import(
    node: ast.ImportFrom,
    stdlib_names: set[str],
    project_packages: set[str],
) -> int:
    # Any relative import should be placed after absolute imports.
    if node.level >= 1:
        return 4
    module = node.module or ""
    root_name = module.split(".", 1)[0] if module else ""
    return classify_absolute_import(root_name, stdlib_names, project_packages)


def extract_leading_import_nodes(tree: ast.Module) -> list[ast.stmt]:
    body = tree.body
    idx = 0
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(getattr(body[0], "value", None), ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        idx = 1

    imports: list[ast.stmt] = []
    for node in body[idx:]:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(node)
        else:
            break
    return imports


def normalize_import_block(
    nodes: list[ast.stmt],
    stdlib_names: set[str],
    project_packages: set[str],
) -> str:
    normalized: list[ImportLine] = []
    future_annotations: list[str] = []
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                text = f"import {alias.name}"
                if alias.asname:
                    text += f" as {alias.asname}"
                group = classify_absolute_import(
                    alias.name.split(".", 1)[0],
                    stdlib_names,
                    project_packages,
                )
                normalized.append(ImportLine(text=text, group=group, kind=0))
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            if node.level == 0 and (node.module or "") == "__future__":
                for alias in node.names:
                    if alias.name == "annotations":
                        future_annotations.append("from __future__ import annotations")
                continue
            group = classify_from_import(node, stdlib_names, project_packages)
            for alias in node.names:
                text = f"from {module} import {alias.name}"
                if alias.asname:
                    text += f" as {alias.asname}"
                normalized.append(ImportLine(text=text, group=group, kind=1, rel_level=node.level))

    groups: dict[int, list[ImportLine]] = {1: [], 2: [], 3: [], 4: []}
    for item in normalized:
        groups[item.group].append(item)

    # De-duplicate and sort:
    # 1) plain imports first
    # 2) then from-imports
    # 3) lexicographical order inside each kind
    # 4) for relative imports, farther levels first: .. > .
    for k in groups:
        uniq = {(item.kind, item.text): item for item in groups[k]}
        if k == 4:
            groups[k] = sorted(uniq.values(), key=lambda x: (x.kind, -x.rel_level, x.text))
        else:
            groups[k] = sorted(uniq.values(), key=lambda x: (x.kind, x.text))

    out: list[str] = []
    if future_annotations:
        out.extend(sorted(set(future_annotations)))
    for k in (1, 2, 3, 4):
        if not groups[k]:
            continue
        if out:
            out.append("")
        out.extend(item.text for item in groups[k])
    return "\n".join(out) + "\n"


def find_missing_relative_import_targets(init_path: Path, source: str) -> list[tuple[int, str]]:
    """Check relative import module anchors in __init__.py.

    For statements like ``from .a import b``, validate that ``a`` exists as
    either ``a.py`` or directory ``a/`` at the resolved relative location.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    missing: list[tuple[int, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level < 1 or not node.module:
            continue
        first_seg = node.module.split(".", 1)[0].strip()
        if not first_seg:
            continue

        anchor = init_path.parent
        for _ in range(max(node.level - 1, 0)):
            anchor = anchor.parent
        file_candidate = anchor / f"{first_seg}.py"
        dir_candidate = anchor / first_seg
        if not (file_candidate.exists() or dir_candidate.exists()):
            target = "." * node.level + node.module
            missing.append((node.lineno, target))
    return missing


def run_yapf_on_python_files(root: Path) -> tuple[int, list[str]]:
    """Run yapf -i for all Python files under root."""
    formatted = 0
    errors: list[str] = []
    for py_file in sorted(iter_python_files(root)):
        try:
            result = subprocess.run(
                ["yapf", "-i", str(py_file)],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            errors.append("yapf command not found")
            break
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit={result.returncode}"
            errors.append(f"{py_file}: {detail}")
            continue
        formatted += 1
    return formatted, errors


def process_file(
    path: Path,
    stdlib_names: set[str],
    project_packages: set[str],
    apply: bool,
) -> bool:
    """Return whether file content was modified."""
    source = path.read_text(encoding="utf-8")

    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Skip files that cannot be parsed.
        return False

    import_nodes = extract_leading_import_nodes(tree)
    if not import_nodes:
        return False

    start = import_nodes[0].lineno - 1
    end = import_nodes[-1].end_lineno or import_nodes[-1].lineno
    lines = source.splitlines()

    new_block = normalize_import_block(import_nodes, stdlib_names, project_packages)
    new_block_lines = new_block.rstrip("\n").splitlines()

    new_lines = lines[:start] + new_block_lines
    if end < len(lines):
        tail_lines = lines[end:]
        leading_blank_count = 0
        for line in tail_lines:
            if line == "":
                leading_blank_count += 1
            else:
                break

        # Keep exactly two blank lines between imports and following content.
        keep_blanks = 2
        new_lines.extend([""] * keep_blanks)
        new_lines.extend(tail_lines[leading_blank_count:])

    new_source = "\n".join(new_lines) + ("\n" if source.endswith("\n") else "")
    if path.name == "__init__.py":
        merged_source = _merge_init_all_exports_source(new_source)
        if merged_source is not None:
            new_source = merged_source

    if new_source == source:
        return False

    if apply:
        path.write_text(new_source, encoding="utf-8")
    return True


def _names_from_target(target: ast.AST) -> list[str]:
    names: list[str] = []
    if isinstance(target, ast.Name):
        names.append(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            names.extend(_names_from_target(elt))
    return names


class _UsageAnalyzer(ast.NodeVisitor):
    def __init__(self) -> None:
        self.scopes: list[ScopeState] = [ScopeState(imports={}, assigned={}, used=set(), params=set())]
        self.issues: list[tuple[int, str, str]] = []

    def _scope(self) -> ScopeState:
        return self.scopes[-1]

    def _mark_assigned(self, name: str, lineno: int) -> None:
        if name in self._scope().params:
            return
        self._scope().assigned.setdefault(name, lineno)

    def _mark_import(self, name: str, lineno: int) -> None:
        self._scope().imports.setdefault(name, lineno)

    def _enter_scope(self, params: set[str]) -> None:
        self.scopes.append(ScopeState(imports={}, assigned={}, used=set(), params=params))

    def _exit_scope(self) -> None:
        scope = self.scopes.pop()
        for name, lineno in scope.imports.items():
            if name not in scope.used:
                self.issues.append((lineno, "unused_import", f"import '{name}' is not used"))
        for name, lineno in scope.assigned.items():
            if name not in scope.used:
                self.issues.append((lineno, "unused_variable", f"variable '{name}' is assigned but never used"))

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if isinstance(node.ctx, ast.Load):
            self._scope().used.add(node.id)
        elif isinstance(node.ctx, ast.Store):
            self._mark_assigned(node.id, node.lineno)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            name = alias.asname or alias.name.split(".", 1)[0]
            self._mark_import(name, node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        for alias in node.names:
            if alias.name == "*":
                continue
            name = alias.asname or alias.name
            self._mark_import(name, node.lineno)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        for t in node.targets:
            for name in _names_from_target(t):
                self._mark_assigned(name, node.lineno)
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        for name in _names_from_target(node.target):
            self._mark_assigned(name, node.lineno)
        if node.value:
            self.visit(node.value)
        self.visit(node.annotation)

    def visit_For(self, node: ast.For) -> None:  # noqa: N802
        for name in _names_from_target(node.target):
            self._mark_assigned(name, node.lineno)
        self.visit(node.iter)
        for s in node.body:
            self.visit(s)
        for s in node.orelse:
            self.visit(s)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:  # noqa: N802
        self.visit_For(node)

    def visit_With(self, node: ast.With) -> None:  # noqa: N802
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars:
                for name in _names_from_target(item.optional_vars):
                    self._mark_assigned(name, node.lineno)
        for s in node.body:
            self.visit(s)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:  # noqa: N802
        self.visit_With(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:  # noqa: N802
        if node.name:
            self._mark_assigned(node.name, node.lineno)
        for s in node.body:
            self.visit(s)

    def visit_ListComp(self, node: ast.ListComp) -> None:  # noqa: N802
        self.visit(node.elt)
        for gen in node.generators:
            self.visit(gen.iter)
            for name in _names_from_target(gen.target):
                self._mark_assigned(name, node.lineno)
            for if_clause in gen.ifs:
                self.visit(if_clause)

    def visit_SetComp(self, node: ast.SetComp) -> None:  # noqa: N802
        self.visit_ListComp(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:  # noqa: N802
        self.visit(node.key)
        self.visit(node.value)
        for gen in node.generators:
            self.visit(gen.iter)
            for name in _names_from_target(gen.target):
                self._mark_assigned(name, node.lineno)
            for if_clause in gen.ifs:
                self.visit(if_clause)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:  # noqa: N802
        self.visit_ListComp(node)

    def _visit_function_like(self, node: ast.AST, args: ast.arguments, body: list[ast.stmt]) -> None:
        # Visit decorators/returns in outer scope.
        for d in getattr(node, "decorator_list", []):
            self.visit(d)
        returns = getattr(node, "returns", None)
        if returns is not None:
            self.visit(returns)
        for default in args.defaults + args.kw_defaults:
            if default is not None:
                self.visit(default)
        for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
            if a.annotation is not None:
                self.visit(a.annotation)
        if args.vararg and args.vararg.annotation is not None:
            self.visit(args.vararg.annotation)
        if args.kwarg and args.kwarg.annotation is not None:
            self.visit(args.kwarg.annotation)

        params: set[str] = {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs}
        if args.vararg:
            params.add(args.vararg.arg)
        if args.kwarg:
            params.add(args.kwarg.arg)
        self._enter_scope(params=params)
        for stmt in body:
            self.visit(stmt)
        self._exit_scope()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_function_like(node, node.args, node.body)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_function_like(node, node.args, node.body)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        for b in node.bases:
            self.visit(b)
        for k in node.keywords:
            self.visit(k.value)
        for d in node.decorator_list:
            self.visit(d)
        self._enter_scope(params=set())
        for stmt in node.body:
            self.visit(stmt)
        self._exit_scope()

    def finalize(self) -> list[tuple[int, str, str]]:
        while len(self.scopes) > 1:
            self._exit_scope()
        self._exit_scope()
        return self.issues


def _has_private_segment(module_name: str) -> bool:
    parts = [p for p in module_name.split(".") if p]
    if len(parts) <= 1:
        return False
    return any(p.startswith("_") for p in parts[1:])


def detect_code_issues(path: Path) -> list[CheckIssue]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        return []

    issues: list[CheckIssue] = []

    # Rule 1: absolute imports that reference private modules (ignore relative imports).
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _has_private_segment(alias.name):
                    issues.append(
                        CheckIssue(
                            path=path,
                            line=node.lineno,
                            category="private_absolute_import",
                            detail=f"absolute import references private module: '{alias.name}'",
                        ))
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0 or not node.module:
                continue
            if _has_private_segment(node.module):
                issues.append(
                    CheckIssue(
                        path=path,
                        line=node.lineno,
                        category="private_absolute_import",
                        detail=f"absolute import-from references private module: '{node.module}'",
                    ))

    # Rule 2/3: unused imports and unused variable definitions (excluding function params).
    analyzer = _UsageAnalyzer()
    analyzer.visit(tree)
    for line, category, detail in analyzer.finalize():
        issues.append(CheckIssue(path=path, line=line, category=category, detail=detail))

    return sorted(issues, key=lambda x: (x.path.as_posix(), x.line, x.category))


def _validate_root(root_arg: str) -> Path:
    root = Path(root_arg).resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Invalid root directory: {root}")
    return root


def run_sort_imports(root: Path, dry_run: bool) -> int:
    stdlib_names = set(getattr(sys, "stdlib_module_names", set()))
    project_packages = discover_project_packages(root)
    py_files = sorted(iter_python_files(root))
    modified_files: list[Path] = []
    missing_relative_targets: list[tuple[Path, int, str]] = []

    for path in py_files:
        source_for_check = path.read_text(encoding="utf-8")
        if path.name == "__init__.py":
            for lineno, target in find_missing_relative_import_targets(path, source_for_check):
                missing_relative_targets.append((path, lineno, target))
        modified = process_file(
            path=path,
            stdlib_names=stdlib_names,
            project_packages=project_packages,
            apply=not dry_run,
        )
        if modified:
            modified_files.append(path)

    mode = "DRY_RUN" if dry_run else "APPLY"
    print(f"[{mode}] sort-imports scanned: {len(py_files)}, modified: {len(modified_files)}")
    for p in modified_files:
        print(str(p))
    if missing_relative_targets:
        print("Missing relative import module targets in __init__.py:")
        for path, lineno, target in missing_relative_targets:
            print(f"{path}:{lineno} [missing_relative_import_target] {target}")

    if not dry_run:
        formatted_count, yapf_errors = run_yapf_on_python_files(root)
        print(f"[APPLY] yapf formatted: {formatted_count}")
        for err in yapf_errors:
            print(f"[yapf-error] {err}")
        if yapf_errors:
            return 1

    return 1 if missing_relative_targets else 0


def run_report_private_candidates(root: Path, dry_run: bool) -> int:
    private_module_candidates = collect_private_module_rename_candidates(root)
    mode = "DRY_RUN" if dry_run else "APPLY"
    print(f"[{mode}] report-private-candidates")
    print("Private module rename candidates (foo.py -> _foo.py):")
    for p in private_module_candidates:
        print(str(p))
    return 0


def run_detect_issues(root: Path, dry_run: bool) -> int:
    py_files = sorted(iter_python_files(root))
    all_issues: list[CheckIssue] = []
    for path in py_files:
        all_issues.extend(detect_code_issues(path))

    mode = "DRY_RUN" if dry_run else "APPLY"
    print(f"[{mode}] detect-issues scanned: {len(py_files)}, issues: {len(all_issues)}")
    if not all_issues:
        return 0
    for issue in all_issues:
        print(f"{issue.path}:{issue.line} [{issue.category}] {issue.detail}")
    return 0


def run_check_chinese(root: Path, dry_run: bool) -> int:
    py_files = sorted(iter_python_files(root))
    findings: list[tuple[Path, int, str]] = []
    for path in py_files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for idx, line in enumerate(lines, start=1):
            if CHINESE_RE.search(line):
                findings.append((path, idx, line.strip()))

    mode = "DRY_RUN" if dry_run else "APPLY"
    print(f"[{mode}] check-chinese scanned: {len(py_files)}, findings: {len(findings)}")
    for path, lineno, line in findings:
        print(f"{path}:{lineno} [chinese_text] {line}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Python import/public-API maintenance tool.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_sort = subparsers.add_parser("sort-imports", help="Normalize top import blocks.")
    p_sort.add_argument("--root", default=".", help="Project root directory.")
    p_sort.add_argument("--dry-run", action="store_true", help="Preview changes without writing files.")

    p_report = subparsers.add_parser("report-private-candidates", help="List foo.py -> _foo.py candidates.")
    p_report.add_argument("--root", default=".", help="Project root directory.")
    p_report.add_argument("--dry-run", action="store_true", help="Read-only mode (same output).")

    p_detect = subparsers.add_parser(
        "detect-issues",
        help=(
            "Detect: (1) absolute imports to private modules, "
            "(2) unused imports, (3) unused variable definitions."
        ),
    )
    p_detect.add_argument("--root", default=".", help="Project root directory.")
    p_detect.add_argument("--dry-run", action="store_true", help="Read-only mode (same output).")

    p_check_chinese = subparsers.add_parser(
        "check-chinese",
        help="Check Python files for Chinese characters.",
    )
    p_check_chinese.add_argument("--root", default=".", help="Project root directory.")
    p_check_chinese.add_argument("--dry-run", action="store_true", help="Read-only mode (same output).")

    args = parser.parse_args()
    try:
        root = _validate_root(args.root)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.command == "sort-imports":
        return run_sort_imports(root, args.dry_run)
    if args.command == "report-private-candidates":
        return run_report_private_candidates(root, args.dry_run)
    if args.command == "detect-issues":
        return run_detect_issues(root, args.dry_run)
    if args.command == "check-chinese":
        return run_check_chinese(root, args.dry_run)

    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
