# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for _meta.py.

Covers:
- SkillMeta, InputRecordMeta, OutputRecordMeta, WorkspaceMetadata models
- ensure_layout: directory creation and metadata initialisation
- load_metadata: reading metadata.json with datetime parsing
- save_metadata: atomic write via tmp-file rename
- dir_digest: stable SHA-256 hashing of a directory tree
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from trpc_agent_sdk.code_executors._constants import (
    DIR_OUT,
    DIR_RUNS,
    DIR_SKILLS,
    DIR_WORK,
    META_FILE_NAME,
)
from trpc_agent_sdk.code_executors.utils._meta import (
    InputRecordMeta,
    OutputRecordMeta,
    SkillMeta,
    WorkspaceMetadata,
    dir_digest,
    ensure_layout,
    load_metadata,
    save_metadata,
)


# ---------------------------------------------------------------------------
# Pydantic model tests
# ---------------------------------------------------------------------------


class TestSkillMeta:
    """Tests for the SkillMeta model."""

    def test_defaults(self):
        sm = SkillMeta()
        assert sm.name is None
        assert sm.rel_path is None
        assert sm.digest is None
        assert sm.mounted is None
        assert sm.staged_at is None

    def test_full_construction(self):
        now = datetime.now()
        sm = SkillMeta(name="demo", rel_path="skills/demo", digest="abc", mounted=True, staged_at=now)
        assert sm.name == "demo"
        assert sm.rel_path == "skills/demo"
        assert sm.digest == "abc"
        assert sm.mounted is True
        assert sm.staged_at == now

    def test_serialization_round_trip(self):
        now = datetime.now()
        sm = SkillMeta(name="s", staged_at=now)
        data = sm.model_dump(by_alias=True, exclude_none=True)
        assert "name" in data
        assert "staged_at" in data
        restored = SkillMeta(**data)
        assert restored.name == "s"


class TestInputRecordMeta:
    """Tests for the InputRecordMeta model."""

    def test_defaults(self):
        ir = InputRecordMeta()
        assert ir.src is None
        assert ir.dst is None
        assert ir.resolved is None
        assert ir.version is None
        assert ir.mode is None
        assert ir.timestamp is None

    def test_alias_from(self):
        """The 'src' field uses alias 'from' for serialization."""
        data = {"from": "artifact://x", "dst": "/tmp/x"}
        ir = InputRecordMeta(**data)
        assert ir.src == "artifact://x"
        assert ir.dst == "/tmp/x"

    def test_alias_ts(self):
        now = datetime.now()
        ir = InputRecordMeta(ts=now)
        assert ir.timestamp == now

    def test_full_construction(self):
        now = datetime.now()
        data = {"from": "host://a", "dst": "b", "resolved": "c", "version": 2, "mode": "copy", "ts": now}
        ir = InputRecordMeta(**data)
        assert ir.src == "host://a"
        assert ir.version == 2
        assert ir.timestamp == now


class TestOutputRecordMeta:
    """Tests for the OutputRecordMeta model."""

    def test_defaults(self):
        o = OutputRecordMeta()
        assert o.globs == []
        assert o.saved_as == []
        assert o.versions == []
        assert o.limits_hit is None
        assert o.timestamp is None

    def test_full_construction(self):
        now = datetime.now()
        o = OutputRecordMeta(globs=["*.txt"], saved_as=["file.txt"], versions=[1], limits_hit=False, ts=now)
        assert o.globs == ["*.txt"]
        assert o.limits_hit is False

    def test_alias_ts(self):
        now = datetime.now()
        o = OutputRecordMeta(ts=now)
        assert o.timestamp == now


class TestWorkspaceMetadata:
    """Tests for the WorkspaceMetadata model."""

    def test_defaults(self):
        wm = WorkspaceMetadata()
        assert wm.version is None
        assert wm.created_at is None
        assert wm.updated_at is None
        assert wm.last_access is None
        assert wm.skills == {}
        assert wm.inputs == []
        assert wm.outputs == []

    def test_full_construction(self):
        now = datetime.now()
        skill = SkillMeta(name="s1")
        wm = WorkspaceMetadata(
            version=1,
            created_at=now,
            updated_at=now,
            last_access=now,
            skills={"s1": skill},
            inputs=[InputRecordMeta(dst="x")],
            outputs=[OutputRecordMeta(globs=["*"])],
        )
        assert wm.version == 1
        assert "s1" in wm.skills
        assert len(wm.inputs) == 1
        assert len(wm.outputs) == 1

    def test_nested_skill_meta(self):
        now = datetime.now()
        wm = WorkspaceMetadata(skills={"a": SkillMeta(name="a", staged_at=now)})
        assert wm.skills["a"].staged_at == now


# ---------------------------------------------------------------------------
# ensure_layout tests
# ---------------------------------------------------------------------------


class TestEnsureLayout:
    """Tests for ensure_layout function."""

    def test_creates_all_subdirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = ensure_layout(tmpdir)
            for name in (DIR_SKILLS, DIR_WORK, DIR_RUNS, DIR_OUT):
                assert (Path(tmpdir) / name).is_dir()
                assert name in paths
                assert paths[name] == Path(tmpdir) / name

    def test_creates_metadata_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ensure_layout(tmpdir)
            meta_file = Path(tmpdir) / META_FILE_NAME
            assert meta_file.exists()
            data = json.loads(meta_file.read_text("utf-8"))
            assert data["version"] == 1

    def test_idempotent(self):
        """Calling twice should not fail or overwrite existing metadata."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ensure_layout(tmpdir)
            meta_file = Path(tmpdir) / META_FILE_NAME
            first_content = meta_file.read_text("utf-8")

            ensure_layout(tmpdir)
            second_content = meta_file.read_text("utf-8")
            assert first_content == second_content

    def test_accepts_path_object(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = ensure_layout(Path(tmpdir))
            assert (Path(tmpdir) / DIR_SKILLS).is_dir()

    def test_accepts_string(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = ensure_layout(tmpdir)
            assert (Path(tmpdir) / DIR_SKILLS).is_dir()


# ---------------------------------------------------------------------------
# save_metadata / load_metadata round-trip tests
# ---------------------------------------------------------------------------


class TestSaveMetadata:
    """Tests for save_metadata function."""

    def test_saves_json_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            md = WorkspaceMetadata(version=1, created_at=datetime.now())
            save_metadata(root, md)
            meta_file = root / META_FILE_NAME
            assert meta_file.exists()
            data = json.loads(meta_file.read_text("utf-8"))
            assert data["version"] == 1

    def test_updates_updated_at(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            old_time = datetime(2020, 1, 1)
            md = WorkspaceMetadata(version=1, updated_at=old_time)
            save_metadata(root, md)
            assert md.updated_at > old_time

    def test_atomic_write_removes_tmp(self):
        """The .metadata.tmp file should be renamed away after save."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            md = WorkspaceMetadata(version=1)
            save_metadata(root, md)
            assert not (root / ".metadata.tmp").exists()
            assert (root / META_FILE_NAME).exists()

    def test_accepts_string_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md = WorkspaceMetadata(version=1)
            save_metadata(tmpdir, md)
            assert (Path(tmpdir) / META_FILE_NAME).exists()

    def test_file_permissions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            md = WorkspaceMetadata(version=1)
            save_metadata(root, md)
            meta_file = root / META_FILE_NAME
            mode = meta_file.stat().st_mode & 0o777
            assert mode == 0o600

    def test_excludes_none_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            md = WorkspaceMetadata(version=1)
            save_metadata(root, md)
            data = json.loads((root / META_FILE_NAME).read_text("utf-8"))
            assert "created_at" not in data
            assert "last_access" not in data

    def test_serializes_datetime_as_iso(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            now = datetime(2025, 6, 15, 12, 30, 0)
            md = WorkspaceMetadata(version=1, created_at=now)
            save_metadata(root, md)
            data = json.loads((root / META_FILE_NAME).read_text("utf-8"))
            assert data["created_at"] == "2025-06-15T12:30:00"

    def test_serializes_skills_with_datetime(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            staged = datetime(2025, 3, 1, 8, 0, 0)
            md = WorkspaceMetadata(
                version=1,
                skills={"sk": SkillMeta(name="sk", staged_at=staged)},
            )
            save_metadata(root, md)
            data = json.loads((root / META_FILE_NAME).read_text("utf-8"))
            assert data["skills"]["sk"]["staged_at"] == "2025-03-01T08:00:00"

    def test_serializes_inputs_and_outputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            now = datetime.now()
            md = WorkspaceMetadata(
                version=2,
                inputs=[InputRecordMeta(**{"from": "a", "dst": "b", "ts": now})],
                outputs=[OutputRecordMeta(globs=["*.py"], ts=now)],
            )
            save_metadata(root, md)
            data = json.loads((root / META_FILE_NAME).read_text("utf-8"))
            assert len(data["inputs"]) == 1
            assert len(data["outputs"]) == 1


class TestLoadMetadata:
    """Tests for load_metadata function."""

    def test_returns_default_when_file_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md = load_metadata(tmpdir)
            assert md.version == 1
            assert md.created_at is not None
            assert md.skills == {}

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            now = datetime(2025, 6, 15, 12, 0, 0)
            original = WorkspaceMetadata(
                version=3,
                created_at=now,
                last_access=now,
            )
            save_metadata(root, original)
            loaded = load_metadata(root)
            assert loaded.version == 3
            assert loaded.created_at == now

    def test_round_trip_with_skills(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            staged = datetime(2025, 1, 1, 0, 0, 0)
            original = WorkspaceMetadata(
                version=1,
                skills={"demo": SkillMeta(name="demo", digest="d1", staged_at=staged)},
            )
            save_metadata(root, original)
            loaded = load_metadata(root)
            assert "demo" in loaded.skills
            assert loaded.skills["demo"].name == "demo"
            assert loaded.skills["demo"].staged_at == staged

    def test_round_trip_with_inputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ts = datetime(2025, 2, 1, 10, 0, 0)
            original = WorkspaceMetadata(
                version=1,
                inputs=[InputRecordMeta(**{"from": "artifact://x", "dst": "/w/x", "ts": ts})],
            )
            save_metadata(root, original)
            loaded = load_metadata(root)
            assert len(loaded.inputs) == 1
            assert loaded.inputs[0].dst == "/w/x"

    def test_round_trip_with_outputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ts = datetime(2025, 3, 1, 8, 0, 0)
            original = WorkspaceMetadata(
                version=1,
                outputs=[OutputRecordMeta(globs=["out/**"], saved_as=["f.tar"], versions=[1], ts=ts)],
            )
            save_metadata(root, original)
            loaded = load_metadata(root)
            assert len(loaded.outputs) == 1
            assert loaded.outputs[0].globs == ["out/**"]
            assert loaded.outputs[0].timestamp == ts

    def test_accepts_string_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            save_metadata(tmpdir, WorkspaceMetadata(version=5))
            md = load_metadata(tmpdir)
            assert md.version == 5

    def test_loads_json_with_z_suffix_datetimes(self):
        """load_metadata should handle ISO strings ending in Z."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data = {
                "version": 1,
                "created_at": "2025-06-15T12:00:00Z",
                "updated_at": "2025-06-15T12:00:00Z",
                "last_access": "2025-06-15T12:00:00Z",
                "skills": {
                    "s": {"name": "s", "staged_at": "2025-01-01T00:00:00Z"}
                },
                "inputs": [{"ts": "2025-02-01T00:00:00Z", "dst": "x"}],
                "outputs": [{"globs": ["*"], "ts": "2025-03-01T00:00:00Z"}],
            }
            (root / META_FILE_NAME).write_text(json.dumps(data), encoding="utf-8")
            md = load_metadata(root)
            assert md.version == 1
            assert md.created_at is not None
            assert md.skills["s"].staged_at is not None
            assert len(md.inputs) == 1
            assert len(md.outputs) == 1

    def test_loads_json_without_optional_datetime_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data = {"version": 2}
            (root / META_FILE_NAME).write_text(json.dumps(data), encoding="utf-8")
            md = load_metadata(root)
            assert md.version == 2
            assert md.created_at is None

    def test_loads_json_with_null_datetime_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data = {"version": 1, "created_at": None, "updated_at": None, "last_access": None}
            (root / META_FILE_NAME).write_text(json.dumps(data), encoding="utf-8")
            md = load_metadata(root)
            assert md.version == 1
            assert md.created_at is None

    def test_loads_json_with_empty_skills(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data = {"version": 1, "skills": {}}
            (root / META_FILE_NAME).write_text(json.dumps(data), encoding="utf-8")
            md = load_metadata(root)
            assert md.skills == {}

    def test_loads_json_with_empty_inputs_outputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data = {"version": 1, "inputs": [], "outputs": []}
            (root / META_FILE_NAME).write_text(json.dumps(data), encoding="utf-8")
            md = load_metadata(root)
            assert md.inputs == []
            assert md.outputs == []

    def test_loads_json_skills_without_staged_at(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data = {"version": 1, "skills": {"a": {"name": "a"}}}
            (root / META_FILE_NAME).write_text(json.dumps(data), encoding="utf-8")
            md = load_metadata(root)
            assert md.skills["a"].staged_at is None

    def test_loads_json_inputs_without_ts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data = {"version": 1, "inputs": [{"dst": "x"}]}
            (root / META_FILE_NAME).write_text(json.dumps(data), encoding="utf-8")
            md = load_metadata(root)
            assert md.inputs[0].timestamp is None

    def test_loads_json_outputs_without_ts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data = {"version": 1, "outputs": [{"globs": ["*"]}]}
            (root / META_FILE_NAME).write_text(json.dumps(data), encoding="utf-8")
            md = load_metadata(root)
            assert md.outputs[0].timestamp is None


# ---------------------------------------------------------------------------
# dir_digest tests
# ---------------------------------------------------------------------------


class TestDirDigest:
    """Tests for dir_digest function."""

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            digest = dir_digest(root)
            assert isinstance(digest, str)
            assert len(digest) == 64  # SHA-256 hex length

    def test_deterministic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "a.txt").write_text("hello")
            d1 = dir_digest(root)
            d2 = dir_digest(root)
            assert d1 == d2

    def test_different_content_different_digest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "a.txt").write_text("hello")
            d1 = dir_digest(root)

            (root / "a.txt").write_text("world")
            d2 = dir_digest(root)
            assert d1 != d2

    def test_different_filename_different_digest(self):
        d1 = d2 = None
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "a.txt").write_text("hello")
            d1 = dir_digest(root)

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "b.txt").write_text("hello")
            d2 = dir_digest(root)

        assert d1 != d2

    def test_nested_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sub = root / "sub"
            sub.mkdir()
            (sub / "file.txt").write_text("data")
            digest = dir_digest(root)
            assert isinstance(digest, str)
            assert len(digest) == 64

    def test_ignores_directories_in_hash(self):
        """Only files are hashed; empty sub-directories don't change the digest."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "a.txt").write_text("x")
            d1 = dir_digest(root)

            (root / "emptydir").mkdir()
            d2 = dir_digest(root)
            assert d1 == d2

    def test_sorted_order_stability(self):
        """Adding files out of alphabetical order should yield the same digest."""
        d1 = d2 = None
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "b.txt").write_text("2")
            (root / "a.txt").write_text("1")
            d1 = dir_digest(root)

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "a.txt").write_text("1")
            (root / "b.txt").write_text("2")
            d2 = dir_digest(root)

        assert d1 == d2

    def test_binary_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "bin.dat").write_bytes(b"\x00\x01\x02\xff")
            digest = dir_digest(root)
            assert isinstance(digest, str)
            assert len(digest) == 64

    def test_cross_platform_path_normalization(self):
        """Paths are normalized to forward slashes for stability."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            nested = root / "a" / "b"
            nested.mkdir(parents=True)
            (nested / "c.txt").write_text("ok")
            digest = dir_digest(root)
            assert isinstance(digest, str)
            assert len(digest) == 64
