"""Tests for the formal code-review skill package."""

from __future__ import annotations

from pathlib import Path

from examples.skills_code_review_agent.agent.tools import build_skill_run_payload

SKILL_DIR = (
    Path(__file__).resolve().parent.parent
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

    diff_file = tmp_path / "sample.diff"
    diff_file.write_text("diff --git a/a.py b/a.py\n", encoding="utf-8")

    payload = build_skill_run_payload(
        diff_file=diff_file,
        script_name="run_linters.py",
    )

    assert payload["skill"] == "code-review"
    assert payload["cwd"] == "$SKILLS_DIR/code-review"
    assert "python scripts/run_linters.py --diff-file" in payload["command"]
    assert payload["output_files"] == ["out/run_linters.json"]
