# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Tests for _loader.py — MD-based archetype loading."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from trpc_agent_sdk.agents.sub_agent._loader import load_archetype_from_file
from trpc_agent_sdk.agents.sub_agent._loader import load_archetypes_from_dir
from trpc_agent_sdk.agents.sub_agent._loader import _split_frontmatter
from trpc_agent_sdk.agents.sub_agent._loader import _WHITELIST_NAMES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(tmp_path: Path, filename: str, content: str) -> Path:
    p = tmp_path / filename
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _split_frontmatter
# ---------------------------------------------------------------------------

def test_split_frontmatter_empty_text():
    fm, body = _split_frontmatter("")
    assert fm == ""
    assert body == ""


def test_split_frontmatter_no_delimiter():
    fm, body = _split_frontmatter("plain text")
    assert fm == ""
    assert body == "plain text"


def test_split_frontmatter_unclosed_delimiter():
    fm, body = _split_frontmatter("---\nsome content\n")
    assert fm == ""
    assert body == "---\nsome content\n"


def test_split_frontmatter_normal():
    fm, body = _split_frontmatter("---\nname: test\ndescription: desc\n---\nBody text.\n")
    assert "name: test" in fm
    assert "description: desc" in fm
    assert "Body text." in body


# ---------------------------------------------------------------------------
# load_archetype_from_file — happy path
# ---------------------------------------------------------------------------

def test_minimal_md(tmp_path):
    p = _write(tmp_path, "researcher.md", """\
        ---
        name: researcher
        description: Use for research tasks.
        ---

        You are a researcher.
    """)
    a = load_archetype_from_file(p)
    assert a.name == "researcher"
    assert a.description == "Use for research tasks."
    assert "You are a researcher." in a.instruction
    assert a.model is None
    # no tools specified — inherits all parent tools
    assert a.tools is None


def test_explicit_tools(tmp_path):
    p = _write(tmp_path, "reader.md", """\
        ---
        name: reader
        description: Only reads files.
        tools:
          - Read
          - Grep
        ---

        You read files.
    """)
    a = load_archetype_from_file(p)
    tool_names = {t().name for t in a.tools}
    assert tool_names == {"Read", "Grep"}


def test_instruction_multiline(tmp_path):
    p = _write(tmp_path, "multi.md", """\
        ---
        name: multi
        description: Multi-line instruction test.
        ---

        Line one.
        Line two.
        Line three.
    """)
    a = load_archetype_from_file(p)
    assert "Line one." in a.instruction
    assert "Line three." in a.instruction


# ---------------------------------------------------------------------------
# load_archetype_from_file — error cases
# ---------------------------------------------------------------------------

def test_file_read_oserror_raises(tmp_path):
    """Passing a directory path triggers IsADirectoryError (OSError subclass)."""
    with pytest.raises(ValueError, match="cannot read file"):
        load_archetype_from_file(tmp_path)


def test_missing_frontmatter_raises(tmp_path):
    p = _write(tmp_path, "bad.md", "Just a plain body, no frontmatter.\n")
    with pytest.raises(ValueError, match="missing YAML frontmatter"):
        load_archetype_from_file(p)


def test_missing_name_raises(tmp_path):
    p = _write(tmp_path, "bad.md", """\
        ---
        description: Something.
        ---
        Body.
    """)
    with pytest.raises(ValueError, match="'name'"):
        load_archetype_from_file(p)


def test_missing_description_raises(tmp_path):
    p = _write(tmp_path, "bad.md", """\
        ---
        name: my-agent
        ---
        Body.
    """)
    with pytest.raises(ValueError, match="'description'"):
        load_archetype_from_file(p)


def test_empty_body_raises(tmp_path):
    p = _write(tmp_path, "bad.md", """\
        ---
        name: empty-body
        description: Something.
        ---

    """)
    with pytest.raises(ValueError, match="instruction body"):
        load_archetype_from_file(p)


def test_unknown_tool_raises(tmp_path):
    p = _write(tmp_path, "bad.md", """\
        ---
        name: unknown-tool
        description: Something.
        tools:
          - Read
          - NotARealTool
        ---
        Body.
    """)
    with pytest.raises(ValueError, match="unknown tool.*NotARealTool"):
        load_archetype_from_file(p)


def test_tools_not_list_raises(tmp_path):
    p = _write(tmp_path, "bad.md", """\
        ---
        name: bad-tools
        description: Something.
        tools: Read
        ---
        Body.
    """)
    with pytest.raises(ValueError, match="'tools' must be a YAML list"):
        load_archetype_from_file(p)


def test_tool_entry_not_string_raises(tmp_path):
    p = _write(tmp_path, "bad.md", """\
        ---
        name: bad-tools
        description: Something.
        tools:
          - Read
          - 123
        ---
        Body.
    """)
    with pytest.raises(ValueError, match="each tool entry must be a string"):
        load_archetype_from_file(p)


def test_invalid_yaml_raises(tmp_path):
    p = tmp_path / "bad.md"
    p.write_text("---\nname: [unclosed\n---\nBody.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid YAML"):
        load_archetype_from_file(p)


def test_invalid_name_raises(tmp_path):
    p = _write(tmp_path, "bad.md", """\
        ---
        name: "123-invalid"
        description: Something.
        ---
        Body.
    """)
    with pytest.raises(ValueError):
        load_archetype_from_file(p)


# ---------------------------------------------------------------------------
# load_archetypes_from_dir
# ---------------------------------------------------------------------------

def test_load_dir_empty(tmp_path):
    result = load_archetypes_from_dir(tmp_path)
    assert result == []


def test_load_dir_multiple_sorted(tmp_path):
    _write(tmp_path, "z-agent.md", """\
        ---
        name: z-agent
        description: Last.
        ---
        Z body.
    """)
    _write(tmp_path, "a-agent.md", """\
        ---
        name: a-agent
        description: First.
        ---
        A body.
    """)
    result = load_archetypes_from_dir(tmp_path)
    assert [a.name for a in result] == ["a-agent", "z-agent"]


def test_load_dir_nonexistent_raises():
    with pytest.raises(ValueError, match="does not exist"):
        load_archetypes_from_dir("/nonexistent/path/xyz")


def test_load_dir_not_a_directory_raises(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("hello")
    with pytest.raises(ValueError, match="not a directory"):
        load_archetypes_from_dir(f)


def test_load_dir_bad_file_raises(tmp_path):
    _write(tmp_path, "good.md", """\
        ---
        name: good
        description: Good one.
        ---
        Good body.
    """)
    _write(tmp_path, "bad.md", "No frontmatter here.\n")
    with pytest.raises(ValueError, match="missing YAML frontmatter"):
        load_archetypes_from_dir(tmp_path)


def test_load_dir_ignores_non_md(tmp_path):
    (tmp_path / "notes.txt").write_text("ignore me")
    _write(tmp_path, "valid.md", """\
        ---
        name: valid
        description: Valid.
        ---
        Valid body.
    """)
    result = load_archetypes_from_dir(tmp_path)
    assert len(result) == 1
    assert result[0].name == "valid"


# ---------------------------------------------------------------------------
# SpawnSubAgentTool integration
# ---------------------------------------------------------------------------

def test_archetype_tool_agent_paths(tmp_path):
    _write(tmp_path, "custom.md", """\
        ---
        name: custom
        description: Custom agent.
        tools:
          - Read
        ---
        You are custom.
    """)

    from trpc_agent_sdk.agents.sub_agent import SpawnSubAgentTool
    tool = SpawnSubAgentTool(agent_paths=[tmp_path])
    assert tool.registry.names() == ["default", "custom"]


def test_archetype_tool_agent_paths_duplicate_raises(tmp_path):
    _write(tmp_path, "aaa.md", """\
        ---
        name: dup
        description: First.
        ---
        Aaa.
    """)
    _write(tmp_path, "zzz.md", """\
        ---
        name: dup
        description: Second.
        ---
        Zzz.
    """)

    from trpc_agent_sdk.agents.sub_agent import SpawnSubAgentTool
    with pytest.raises(ValueError, match="collides"):
        SpawnSubAgentTool(agent_paths=[tmp_path])


# ---------------------------------------------------------------------------
# Whitelist completeness smoke-test
# ---------------------------------------------------------------------------

def test_whitelist_names_all_importable():
    from trpc_agent_sdk.agents.sub_agent._loader import _tool_whitelist
    wl = _tool_whitelist()
    assert set(wl.keys()) == _WHITELIST_NAMES


# ---------------------------------------------------------------------------
# tool_mapping
# ---------------------------------------------------------------------------


def test_tool_mapping_resolves_custom_tool(tmp_path):
    from trpc_agent_sdk.tools import ReadTool
    p = _write(tmp_path, "custom.md", """\
        ---
        name: custom
        description: Custom tool test.
        tools:
          - Read
          - MyTool
        ---
        You are custom.
    """)
    a = load_archetype_from_file(p, tool_mapping={"MyTool": ReadTool})
    tool_names = {t().name for t in a.tools}
    assert len(a.tools) == 2
    assert tool_names == {"Read"}


def test_tool_mapping_overrides_builtin(tmp_path):
    from trpc_agent_sdk.tools import GrepTool
    p = _write(tmp_path, "custom.md", """\
        ---
        name: custom
        description: Override Read.
        tools:
          - Read
        ---
        You are custom.
    """)
    a = load_archetype_from_file(p, tool_mapping={"Read": GrepTool})
    tool_names = {t().name for t in a.tools}
    assert tool_names == {"Grep"}


def test_tool_mapping_unknown_still_errors(tmp_path):
    p = _write(tmp_path, "bad.md", """\
        ---
        name: bad
        description: Unknown tool.
        tools:
          - NotARealTool
        ---
        Body.
    """)
    with pytest.raises(ValueError, match="unknown tool.*NotARealTool"):
        load_archetype_from_file(p, tool_mapping={"MyTool": type})


def test_frontmatter_non_dict_raises(tmp_path):
    """YAML frontmatter that parses to a list raises ValueError."""
    p = _write(tmp_path, "list.md", """\
        ---
        - item1
        - item2
        ---
        Body text here.
    """)
    with pytest.raises(ValueError, match="must be a YAML mapping"):
        load_archetype_from_file(p)


def test_frontmatter_scalar_raises(tmp_path):
    """YAML frontmatter that parses to a scalar raises ValueError."""
    p = _write(tmp_path, "scalar.md", """\
        ---
        just a string
        ---
        Body text here.
    """)
    with pytest.raises(ValueError, match="must be a YAML mapping"):
        load_archetype_from_file(p)


def test_tool_mapping_error_message_includes_custom(tmp_path):
    """Error message should include custom tool names from tool_mapping."""
    p = _write(tmp_path, "bad.md", """\
        ---
        name: bad
        description: Unknown tool.
        tools:
          - MyTool
          - NotReal
        ---
        Body.
    """)
    with pytest.raises(ValueError) as exc_info:
        load_archetype_from_file(p, tool_mapping={"MyTool": type})
    msg = str(exc_info.value)
    assert "MyTool" in msg
    assert "NotReal" in msg
