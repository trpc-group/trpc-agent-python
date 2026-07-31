# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Business tests for loading the local code-review Skill bundle."""

from __future__ import annotations

from pathlib import Path

import pytest

from examples.skills_code_review_agent.agent import SkillLoadError
from examples.skills_code_review_agent.agent import load_skill


def test_skill_loader_reads_name_and_description():
    skill = load_skill(_skill_root())

    assert skill.manifest.name == "code-review"
    assert "source-code changes" in skill.manifest.description


def test_skill_loader_discovers_rules_scripts_and_docs():
    skill = load_skill(_skill_root())

    assert skill.manifest.rules == [
        "rules/async-resource.md",
        "rules/runtime.md",
        "rules/security.md",
        "rules/testing-db.md",
    ]
    assert skill.manifest.scripts == ["scripts/rule_runner.py"]
    assert skill.manifest.docs == []


def test_skill_loader_resources_are_stably_ordered():
    skill = load_skill(_skill_root())

    assert list(skill.resources) == sorted(skill.resources)


def test_skill_loader_replaces_base_dir_placeholder(tmp_path: Path):
    skill_dir = _write_skill(tmp_path)
    (skill_dir / "README.md").write_text("root=__BASE_DIR__\n", encoding="utf-8")

    skill = load_skill(skill_dir)

    assert f"root={skill_dir.resolve()}" in skill.resources["README.md"]


def test_skill_loader_digest_changes_with_file_content(tmp_path: Path):
    skill_dir = _write_skill(tmp_path)
    first = load_skill(skill_dir).manifest.digest
    (skill_dir / "README.md").write_text("changed\n", encoding="utf-8")
    second = load_skill(skill_dir).manifest.digest

    assert first != second


def test_skill_loader_reports_missing_skill_markdown(tmp_path: Path):
    with pytest.raises(SkillLoadError, match="SKILL.md not found"):
        load_skill(tmp_path)


def test_skill_loader_rejects_invalid_frontmatter(tmp_path: Path):
    skill_dir = tmp_path / "bad"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: [\n---\n", encoding="utf-8")

    with pytest.raises(SkillLoadError, match="invalid SKILL.md YAML front matter"):
        load_skill(skill_dir)


def test_skill_loader_rejects_absolute_and_parent_paths(tmp_path: Path):
    skill_dir = _write_skill(tmp_path, extra_frontmatter="rules:\n  - /tmp/rule.md\n")

    with pytest.raises(SkillLoadError, match="unsafe path"):
        load_skill(skill_dir)

    skill_dir = _write_skill(tmp_path / "parent", extra_frontmatter="rules:\n  - ../rule.md\n")
    with pytest.raises(SkillLoadError, match="unsafe path"):
        load_skill(skill_dir)


def test_skill_loader_rejects_symlink_escape(tmp_path: Path):
    skill_dir = _write_skill(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("secret\n", encoding="utf-8")
    (skill_dir / "rules").mkdir()
    (skill_dir / "rules" / "escape.md").symlink_to(outside)

    with pytest.raises(SkillLoadError, match="escapes root"):
        load_skill(skill_dir)


def test_hidden_resources_are_not_in_manifest(tmp_path: Path):
    skill_dir = _write_skill(tmp_path)
    (skill_dir / ".git").mkdir()
    (skill_dir / ".git" / "config").write_text("ignored\n", encoding="utf-8")
    (skill_dir / ".secret.md").write_text("ignored\n", encoding="utf-8")

    skill = load_skill(skill_dir)

    assert ".secret.md" not in skill.resources
    assert all(not path.startswith(".git/") for path in skill.resources)


def _skill_root() -> Path:
    return Path(__file__).parents[1] / "skills" / "code-review"


def _write_skill(tmp_path: Path, extra_frontmatter: str = "") -> Path:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"""---
name: code-review
description: Test Skill
{extra_frontmatter}---

# Body
""",
        encoding="utf-8",
    )
    return skill_dir
