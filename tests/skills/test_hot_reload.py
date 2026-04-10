# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from pathlib import Path

from trpc_agent_sdk.skills._hot_reload import SkillHotReloadTracker


def test_mark_changed_path_only_tracks_skill_file(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = root / "demo"
    skill_dir.mkdir(parents=True)
    tracker = SkillHotReloadTracker("SKILL.md")

    tracker.mark_changed_path(str(skill_dir / "notes.txt"), is_directory=False, skill_roots=[str(root)])
    assert tracker.pop_changed_dirs(str(root.resolve())) == []

    tracker.mark_changed_path(str(skill_dir / "SKILL.md"), is_directory=False, skill_roots=[str(root)])
    changed = tracker.pop_changed_dirs(str(root.resolve()))
    assert changed == [skill_dir.resolve()]


def test_resolve_root_key_and_normalize_targets(tmp_path: Path):
    root = tmp_path / "skills"
    nested = root / "a" / "b"
    nested.mkdir(parents=True)

    key = SkillHotReloadTracker.resolve_root_key(nested, [str(root)])
    assert key == str(root.resolve())

    deduped = SkillHotReloadTracker.normalize_scan_targets([root / "a", nested, root / "c"])
    assert deduped == [root / "a", root / "c"]
