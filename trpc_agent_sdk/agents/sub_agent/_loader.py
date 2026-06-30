# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Load SubAgentArchetype definitions from Markdown files.

File format (YAML frontmatter + Markdown body)::

    ---
    name: my-researcher
    description: Use this agent for deep research tasks.
    tools:            # optional; defaults to inheriting all parent tools
      - Read
      - websearch
    ---

    You are a research specialist.  Your task is to …
    (this section becomes the sub-agent's system instruction)

Required frontmatter fields: ``name``, ``description``.
Optional: ``tools``.
Body (instruction) must be non-empty after stripping whitespace.

If ``tools`` is omitted, the archetype inherits the full tool surface of
the parent agent at spawn time (minus ``DynamicAgentTool``, which is always
stripped to prevent recursive spawning).

When specified, tools are referenced by their actual tool ``name``
(e.g. ``Read``, ``Bash``, ``websearch``).  Any name not in the whitelist
raises ``ValueError`` at load time (fail-fast).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from typing import List

import yaml

from ._archetype import SubAgentArchetype

# ---------------------------------------------------------------------------
# Built-in tool whitelist — maps actual tool name → factory class reference.
# Populated lazily to avoid import-time side effects; see _tool_whitelist().
# ---------------------------------------------------------------------------

# Maps tool.name → class reference, e.g. "Read" -> ReadTool, "Bash" -> BashTool
_WHITELIST_NAMES = {
    "Bash",
    "Edit",
    "Glob",
    "Grep",
    "Read",
    "webfetch",
    "websearch",
    "Write",
}


def _tool_whitelist() -> dict[str, Any]:
    from trpc_agent_sdk.tools import BashTool
    from trpc_agent_sdk.tools import EditTool
    from trpc_agent_sdk.tools import GlobTool
    from trpc_agent_sdk.tools import GrepTool
    from trpc_agent_sdk.tools import ReadTool
    from trpc_agent_sdk.tools import WebFetchTool
    from trpc_agent_sdk.tools import WebSearchTool
    from trpc_agent_sdk.tools import WriteTool

    return {
        "Bash": BashTool,
        "Edit": EditTool,
        "Glob": GlobTool,
        "Grep": GrepTool,
        "Read": ReadTool,
        "webfetch": WebFetchTool,
        "websearch": WebSearchTool,
        "Write": WriteTool,
    }


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

_FM_DELIMITER = "---"


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return ``(frontmatter_yaml, body)`` or ``("", text)`` if no frontmatter."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != _FM_DELIMITER:
        return "", text

    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == _FM_DELIMITER:
            end = i
            break

    if end is None:
        # Unclosed frontmatter — treat whole file as body (no frontmatter).
        return "", text

    fm_yaml = "".join(lines[1:end])
    body = "".join(lines[end + 1:])
    return fm_yaml, body


# ---------------------------------------------------------------------------
# Single-file loader
# ---------------------------------------------------------------------------


def load_archetype_from_file(path: Path, tool_mapping: dict[str, Any] | None = None) -> SubAgentArchetype:
    """Parse a single ``.md`` file and return a ``SubAgentArchetype``.

    Args:
        path: Path to the ``.md`` file.
        tool_mapping: Optional name-to-class mapping for resolving custom
            tool names referenced in the frontmatter. Merged with the
            built-in whitelist; custom entries take precedence.

    Raises ``ValueError`` with a path-prefixed message on any parse or
    validation error so callers get precise diagnostics.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"{path}: cannot read file: {exc}") from exc

    fm_yaml, body = _split_frontmatter(text)

    if not fm_yaml.strip():
        raise ValueError(f"{path}: missing YAML frontmatter. "
                         "File must start with '---' followed by at least 'name' and 'description'.")

    try:
        fm: dict = yaml.safe_load(fm_yaml) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: invalid YAML frontmatter: {exc}") from exc

    if not isinstance(fm, dict):
        raise ValueError(f"{path}: frontmatter must be a YAML mapping, got {type(fm).__name__}")

    # --- required fields ---
    name = fm.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{path}: frontmatter 'name' must be a non-empty string")

    description = fm.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"{path}: frontmatter 'description' must be a non-empty string")

    # --- instruction (body) ---
    instruction = body.strip()
    if not instruction:
        raise ValueError(f"{path}: instruction body (text after the closing '---') must be non-empty")

    # --- optional: tools ---
    raw_tools = fm.get("tools")
    if raw_tools is None:
        tools = None
    else:
        if not isinstance(raw_tools, list):
            raise ValueError(f"{path}: frontmatter 'tools' must be a YAML list, got {type(raw_tools).__name__}")
        whitelist = _tool_whitelist()
        if tool_mapping:
            whitelist = {**whitelist, **tool_mapping}
        resolved = []
        for item in raw_tools:
            if not isinstance(item, str):
                raise ValueError(f"{path}: each tool entry must be a string, got {type(item).__name__!r}")
            if item not in whitelist:
                allowed = sorted(set(_WHITELIST_NAMES) | set(tool_mapping or ()))
                raise ValueError(f"{path}: unknown tool {item!r}. "
                                 f"Allowed: {allowed}")
            resolved.append(whitelist[item])
        tools = tuple(resolved)

    return SubAgentArchetype(
        name=name.strip(),
        description=description.strip(),
        instruction=instruction,
        tools=tools,
    )


# ---------------------------------------------------------------------------
# Directory loader
# ---------------------------------------------------------------------------


def load_archetypes_from_dir(directory: os.PathLike,
                             tool_mapping: dict[str, Any] | None = None) -> List[SubAgentArchetype]:
    """Load all ``*.md`` files in *directory* as ``SubAgentArchetype`` objects.

    Files are sorted alphabetically so the registration order is deterministic.
    Raises ``ValueError`` if *directory* does not exist or if any file fails
    to parse (fail-fast; all errors are reported with full file paths).
    """
    dirpath = Path(directory)
    if not dirpath.exists():
        raise ValueError(f"agents_path does not exist: {dirpath}")
    if not dirpath.is_dir():
        raise ValueError(f"agents_path is not a directory: {dirpath}")

    md_files = sorted(dirpath.glob("*.md"))

    archetypes = []
    errors: list[str] = []
    for md_file in md_files:
        try:
            archetypes.append(load_archetype_from_file(md_file, tool_mapping=tool_mapping))
        except ValueError as exc:
            errors.append(str(exc))

    if errors:
        joined = "\n  ".join(errors)
        raise ValueError(f"Failed to load archetypes from {dirpath}:\n  {joined}")

    return archetypes


__all__ = ["load_archetype_from_file", "load_archetypes_from_dir"]
