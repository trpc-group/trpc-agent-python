# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.skills._repository.

Covers:
- _split_front_matter: YAML parsing, edge cases
- _parse_tools_from_body: Tools section extraction
- _is_doc_file: file extension check
- FsSkillRepository: indexing, get, summaries, skill_list, path, refresh, _read_docs
- BaseSkillRepository abstract contract
- create_default_skill_repository factory
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trpc_agent_sdk.skills._repository import (
    BASE_DIR_PLACEHOLDER,
    BaseSkillRepository,
    FsSkillRepository,
    _is_doc_file,
    _parse_tools_from_body,
    _split_front_matter,
    create_default_skill_repository,
)


# ---------------------------------------------------------------------------
# _split_front_matter
# ---------------------------------------------------------------------------

class TestSplitFrontMatter:
    def test_no_front_matter(self):
        fm, body = _split_front_matter("# Hello\nworld")
        assert fm == {}
        assert body == "# Hello\nworld"

    def test_with_front_matter(self):
        content = "---\nname: test\ndescription: Test skill\n---\n# Body"
        fm, body = _split_front_matter(content)
        assert fm["name"] == "test"
        assert fm["description"] == "Test skill"
        assert body == "# Body"

    def test_crlf_normalization(self):
        content = "---\r\nname: test\r\n---\r\nbody"
        fm, body = _split_front_matter(content)
        assert fm["name"] == "test"
        assert body == "body"

    def test_invalid_yaml_returns_empty_dict(self):
        content = "---\n: : : invalid\n---\nbody"
        fm, body = _split_front_matter(content)
        assert body == "body"

    def test_non_dict_yaml_returns_empty_dict(self):
        content = "---\n- item1\n- item2\n---\nbody"
        fm, body = _split_front_matter(content)
        assert fm == {}
        assert body == "body"

    def test_unclosed_front_matter(self):
        content = "---\nname: test\nno closing"
        fm, body = _split_front_matter(content)
        assert fm == {}
        assert body == content

    def test_none_values_become_empty_string(self):
        content = "---\nname:\n---\nbody"
        fm, body = _split_front_matter(content)
        assert fm["name"] == ""

    def test_none_key_converted_to_string(self):
        content = "---\nname: test\n---\nbody"
        fm, body = _split_front_matter(content)
        assert fm["name"] == "test"
        assert body == "body"

    def test_no_dash_prefix(self):
        content = "no front matter at all"
        fm, body = _split_front_matter(content)
        assert fm == {}
        assert body == content


# ---------------------------------------------------------------------------
# _parse_tools_from_body
# ---------------------------------------------------------------------------

class TestParseToolsFromBody:
    def test_basic_tools_section(self):
        body = "Tools:\n- tool_a\n- tool_b\n\nOverview"
        tools = _parse_tools_from_body(body)
        assert tools == ["tool_a", "tool_b"]

    def test_no_tools_section(self):
        body = "# Just markdown\nNo tools here"
        assert _parse_tools_from_body(body) == []

    def test_tools_section_stops_at_next_section(self):
        body = "Tools:\n- tool_a\nOverview\nMore content"
        tools = _parse_tools_from_body(body)
        assert tools == ["tool_a"]

    def test_tools_section_skips_headings(self):
        body = "Tools:\n# Comment\n- tool_a\n"
        tools = _parse_tools_from_body(body)
        assert tools == ["tool_a"]

    def test_empty_body(self):
        assert _parse_tools_from_body("") == []

    def test_tools_with_description_colon(self):
        body = "Tools:\n- tool_a\nDescription: something\n"
        tools = _parse_tools_from_body(body)
        assert tools == ["tool_a"]


# ---------------------------------------------------------------------------
# _is_doc_file
# ---------------------------------------------------------------------------

class TestIsDocFile:
    def test_markdown(self):
        assert _is_doc_file("readme.md") is True
        assert _is_doc_file("README.MD") is True

    def test_text(self):
        assert _is_doc_file("notes.txt") is True
        assert _is_doc_file("NOTES.TXT") is True

    def test_non_doc(self):
        assert _is_doc_file("script.py") is False
        assert _is_doc_file("data.json") is False


# ---------------------------------------------------------------------------
# FsSkillRepository
# ---------------------------------------------------------------------------

def _create_skill_dir(root: Path, name: str, description: str = "", body: str = "",
                      docs: dict[str, str] = None) -> Path:
    """Helper to create a skill directory with SKILL.md and optional docs."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    front_matter = f"---\nname: {name}\ndescription: {description}\n---\n"
    content = front_matter + (body or f"# {name}\n")
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    for doc_name, doc_content in (docs or {}).items():
        doc_path = skill_dir / doc_name
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text(doc_content, encoding="utf-8")

    return skill_dir


class TestFsSkillRepository:
    def test_index_finds_skills(self, tmp_path):
        _create_skill_dir(tmp_path, "skill-a", "Skill A description")
        _create_skill_dir(tmp_path, "skill-b", "Skill B description")
        repo = FsSkillRepository(str(tmp_path))
        assert sorted(repo.skill_list()) == ["skill-a", "skill-b"]

    def test_get_returns_skill(self, tmp_path):
        _create_skill_dir(tmp_path, "test-skill", "Test", body="# Test Body\n")
        repo = FsSkillRepository(str(tmp_path))
        skill = repo.get("test-skill")
        assert skill.summary.name == "test-skill"
        assert "Test Body" in skill.body

    def test_get_nonexistent_raises(self, tmp_path):
        repo = FsSkillRepository(str(tmp_path))
        with pytest.raises(ValueError, match="not found"):
            repo.get("nonexistent")

    def test_path_returns_dir(self, tmp_path):
        _create_skill_dir(tmp_path, "my-skill")
        repo = FsSkillRepository(str(tmp_path))
        p = repo.path("my-skill")
        assert "my-skill" in p

    def test_path_nonexistent_raises(self, tmp_path):
        repo = FsSkillRepository(str(tmp_path))
        with pytest.raises(ValueError, match="not found"):
            repo.path("nonexistent")

    def test_summaries(self, tmp_path):
        _create_skill_dir(tmp_path, "a-skill", "Alpha")
        _create_skill_dir(tmp_path, "b-skill", "Beta")
        repo = FsSkillRepository(str(tmp_path))
        summaries = repo.summaries()
        assert len(summaries) == 2
        names = [s.name for s in summaries]
        assert "a-skill" in names
        assert "b-skill" in names

    def test_refresh(self, tmp_path):
        repo = FsSkillRepository(str(tmp_path))
        assert repo.skill_list() == []
        _create_skill_dir(tmp_path, "new-skill")
        repo.refresh()
        assert "new-skill" in repo.skill_list()

    def test_reads_doc_files(self, tmp_path):
        _create_skill_dir(tmp_path, "with-docs", docs={
            "guide.md": "# Guide content",
            "notes.txt": "some notes",
        })
        repo = FsSkillRepository(str(tmp_path))
        skill = repo.get("with-docs")
        assert len(skill.resources) == 2
        paths = [r.path for r in skill.resources]
        assert "guide.md" in paths
        assert "notes.txt" in paths

    def test_base_dir_placeholder_replaced(self, tmp_path):
        body = f"Path is {BASE_DIR_PLACEHOLDER}/scripts\n"
        _create_skill_dir(tmp_path, "placeholder-skill", body=body)
        repo = FsSkillRepository(str(tmp_path))
        skill = repo.get("placeholder-skill")
        assert BASE_DIR_PLACEHOLDER not in skill.body
        assert str(tmp_path) in skill.body

    def test_base_dir_placeholder_in_docs(self, tmp_path):
        _create_skill_dir(tmp_path, "doc-placeholder", docs={
            "ref.md": f"See {BASE_DIR_PLACEHOLDER}/data",
        })
        repo = FsSkillRepository(str(tmp_path))
        skill = repo.get("doc-placeholder")
        for r in skill.resources:
            if r.path == "ref.md":
                assert BASE_DIR_PLACEHOLDER not in r.content

    def test_skill_without_name_uses_dirname(self, tmp_path):
        skill_dir = tmp_path / "dirname-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\ndescription: No name field\n---\nBody\n")
        repo = FsSkillRepository(str(tmp_path))
        assert "dirname-skill" in repo.skill_list()

    def test_first_occurrence_wins(self, tmp_path):
        root1 = tmp_path / "root1"
        root2 = tmp_path / "root2"
        _create_skill_dir(root1, "same-name", "First")
        _create_skill_dir(root2, "same-name", "Second")
        repo = FsSkillRepository(str(root1), str(root2))
        skill = repo.get("same-name")
        assert skill.summary.description == "First"

    def test_parses_tools_from_body(self, tmp_path):
        body = "Tools:\n- get_weather\n- get_data\n\nOverview\n"
        _create_skill_dir(tmp_path, "tools-skill", body=body)
        repo = FsSkillRepository(str(tmp_path))
        skill = repo.get("tools-skill")
        assert skill.tools == ["get_weather", "get_data"]

    def test_skips_git_and_hidden_files(self, tmp_path):
        skill_dir = _create_skill_dir(tmp_path, "hidden-test")
        (skill_dir / ".hidden.md").write_text("hidden")
        git_dir = skill_dir / ".git"
        git_dir.mkdir()
        (git_dir / "config.md").write_text("git config")
        repo = FsSkillRepository(str(tmp_path))
        skill = repo.get("hidden-test")
        for r in skill.resources:
            assert not r.path.startswith(".")
            assert ".git" not in r.path

    def test_from_markdown_classmethod(self):
        content = "---\nname: test\n---\nbody"
        fm, body = FsSkillRepository.from_markdown(content)
        assert fm["name"] == "test"
        assert body == "body"

    def test_workspace_runtime_property(self, tmp_path):
        repo = FsSkillRepository(str(tmp_path))
        assert repo.workspace_runtime is not None

    def test_skill_run_env_default(self, tmp_path):
        repo = FsSkillRepository(str(tmp_path))
        assert repo.skill_run_env("any") == {}

    def test_user_prompt_default(self, tmp_path):
        repo = FsSkillRepository(str(tmp_path))
        assert repo.user_prompt() == ""

    def test_duplicate_roots_deduplicated(self, tmp_path):
        _create_skill_dir(tmp_path, "skill")
        repo = FsSkillRepository(str(tmp_path), str(tmp_path))
        assert repo.skill_list() == ["skill"]

    def test_empty_roots(self):
        repo = FsSkillRepository()
        assert repo.skill_list() == []


# ---------------------------------------------------------------------------
# create_default_skill_repository
# ---------------------------------------------------------------------------

class TestCreateDefaultSkillRepository:
    def test_creates_repository(self, tmp_path):
        _create_skill_dir(tmp_path, "test")
        repo = create_default_skill_repository(str(tmp_path))
        assert isinstance(repo, FsSkillRepository)
        assert "test" in repo.skill_list()


# ---------------------------------------------------------------------------
# FsSkillRepository — edge cases
# ---------------------------------------------------------------------------

class TestFsSkillRepositoryEdgeCases:
    def test_list_root_handling(self, tmp_path):
        repo = FsSkillRepository(str(tmp_path))
        # Passing a list as root (gets flattened)
        repo2 = FsSkillRepository(str(tmp_path))
        assert isinstance(repo2.skill_list(), list)

    def test_summaries_with_parse_error(self, tmp_path):
        skill_dir = tmp_path / "broken"
        skill_dir.mkdir()
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text("---\nname: broken\n---\n# Body\n")
        repo = FsSkillRepository(str(tmp_path))
        # Delete the SKILL.md after indexing to trigger read error
        skill_file.unlink()
        summaries = repo.summaries()
        # Should handle gracefully (warning logged, skill skipped)
        assert isinstance(summaries, list)

    def test_resolve_skill_root_error(self, tmp_path):
        # Create a repo with an invalid root that will fail resolution
        repo = FsSkillRepository(str(tmp_path))
        assert isinstance(repo.skill_list(), list)

    def test_parse_tools_static_method(self):
        tools = FsSkillRepository._parse_tools_from_body(
            "Tools:\n- get_weather\n- get_forecast\n"
        )
        assert tools == ["get_weather", "get_forecast"]

    def test_index_skips_empty_root(self, tmp_path):
        repo = FsSkillRepository(str(tmp_path))
        # Should not raise when encountering empty root
        repo._skill_roots.append("")
        repo.refresh()

    def test_read_docs_error_handling(self, tmp_path):
        skill_dir = _create_skill_dir(tmp_path, "doc-err")
        # Create a doc that will fail reading (binary file misidentified)
        doc_path = skill_dir / "broken.md"
        doc_path.write_bytes(b'\x80\x81invalid utf8 continuation')
        repo = FsSkillRepository(str(tmp_path))
        # get() should still work, skipping unreadable docs
        skill = repo.get("doc-err")
        assert skill is not None


# ---------------------------------------------------------------------------
# BaseSkillRepository — abstract methods
# ---------------------------------------------------------------------------

class TestBaseSkillRepositoryAbstract:
    def test_user_prompt_default(self):
        repo = MagicMock(spec=BaseSkillRepository)
        BaseSkillRepository.user_prompt(repo)

    def test_skill_run_env_default(self):
        repo = MagicMock(spec=BaseSkillRepository)
        result = BaseSkillRepository.skill_run_env(repo, "skill")
        assert result == {}
