"""Explicit loader for the example code-review Skill assets."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def load_code_review_skill(skill_dir: Path) -> dict[str, Any]:
    skill_md = skill_dir / "SKILL.md"
    docs_dir = skill_dir / "docs"
    scripts_dir = skill_dir / "scripts"
    if not skill_md.exists():
        raise FileNotFoundError(f"missing Skill file: {skill_md}")

    docs = [_asset_record(path, skill_dir) for path in sorted(docs_dir.glob("*.md"))]
    scripts = [_asset_record(path, skill_dir) for path in sorted(scripts_dir.glob("*.py"))]
    rule_manifest = _asset_record(skill_dir / "rules.json", skill_dir)
    filter_policy = _asset_record(skill_dir / "filter_policy.json", skill_dir)
    return {
        "name": "code-review",
        "skill_dir": _display_skill_dir(skill_dir),
        "skill_md": _asset_record(skill_md, skill_dir),
        "rule_manifest": rule_manifest,
        "filter_policy_manifest": filter_policy,
        "docs": docs,
        "scripts": scripts,
        "rules_loaded": [record["name"] for record in docs],
        "script_count": len(scripts),
    }


def _display_skill_dir(skill_dir: Path) -> str:
    try:
        return skill_dir.resolve().relative_to(skill_dir.parents[1].resolve()).as_posix()
    except ValueError:
        return skill_dir.as_posix()


def _asset_record(path: Path, base: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "name": path.relative_to(base).as_posix(),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest()[:16],
    }
