"""Tests for the formal code-review skill package."""

from __future__ import annotations

import shlex
from pathlib import Path

from examples.skills_code_review_agent.agent.tools import build_skill_run_payload
from examples.skills_code_review_agent.agent.tools import build_skill_script_plan
from examples.skills_code_review_agent.agent.tools import create_skill_tool_set
from examples.skills_code_review_agent.agent.tools import resolve_code_review_skill_dir

SKILL_DIR = (
    Path(__file__).resolve().parents[3]
    / "skills"
    / "code-review"
)


def test_skill_markdown_contains_expected_workflow_sections() -> None:
    """The formal skill should document usage, outputs, and safety guidance."""

    skill_md = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

    assert "skill_load(skill=\"code-review\")" in skill_md
    assert "Available Docs" in skill_md
    assert "Safety Guidance" in skill_md
    assert "review_report.json" in skill_md


def test_build_skill_run_payload_points_to_skill_workspace_outputs(tmp_path: Path) -> None:
    """Payload builder should produce a stable `skill_run` contract."""

    diff_dir = tmp_path / "space dir"
    diff_dir.mkdir()
    diff_file = diff_dir / "sample.diff"
    diff_file.write_text("diff --git a/a.py b/a.py\n", encoding="utf-8")

    payload = build_skill_run_payload(
        diff_file=diff_file,
        script_name="run_linters.py",
    )

    assert payload["skill"] == "code-review"
    assert payload["cwd"] == "$SKILLS_DIR/code-review"
    assert "python scripts/run_linters.py --diff-file" in payload["command"]
    assert shlex.quote(str(diff_file)) in payload["command"]
    assert payload["output_files"] == ["out/run_linters.json"]


def test_resolve_code_review_skill_dir_prefers_repository_root() -> None:
    """The example should prefer the repository-level skill directory."""

    resolved = resolve_code_review_skill_dir()

    assert resolved == SKILL_DIR.resolve()


def test_build_skill_script_plan_uses_repository_root_skill_scripts(tmp_path: Path) -> None:
    """Sandbox plans should point at the canonical root skill scripts."""

    diff_file = tmp_path / "sample.diff"
    diff_file.write_text("diff --git a/a.py b/a.py\n", encoding="utf-8")

    plan = build_skill_script_plan(
        diff_file=diff_file,
        project_root=Path(__file__).resolve().parents[3],
    )

    assert len(plan) == 3
    assert all(invocation.script_path.is_file() for invocation in plan)
    assert all(SKILL_DIR.resolve() in invocation.script_path.resolve().parents for invocation in plan)


def test_skill_repository_indexes_root_code_review_skill() -> None:
    """SkillToolSet should expose the canonical root code-review skill."""

    _toolset, repository = create_skill_tool_set(
        workspace_runtime_type="local",
        use_cached_repository=False,
    )

    assert Path(repository.path("code-review")).resolve() == SKILL_DIR.resolve()
