# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Unit tests for trpc_agent_sdk.skills._utils.

Covers:
- compute_dir_digest: hashing stability and content sensitivity
- save_metadata / load_metadata: round-trip, missing file
- ensure_layout: directory creation, metadata initialization
- shell_quote: quoting edge cases
- set_state_delta / get_state_delta
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.skills._types import SkillWorkspaceMetadata
from trpc_agent_sdk.skills._utils import (
    compute_dir_digest,
    ensure_layout,
    get_state_delta,
    load_metadata,
    save_metadata,
    set_state_delta,
    shell_quote,
)


def _make_ctx(state_delta=None, session_state=None):
    ctx = MagicMock()
    ctx.actions.state_delta = state_delta or {}
    ctx.session_state = session_state or {}
    return ctx


# ---------------------------------------------------------------------------
# compute_dir_digest
# ---------------------------------------------------------------------------

class TestComputeDirDigest:
    def test_empty_dir(self, tmp_path):
        digest = compute_dir_digest(tmp_path)
        assert isinstance(digest, str)
        assert len(digest) == 64

    def test_single_file(self, tmp_path):
        (tmp_path / "file.txt").write_text("hello")
        d1 = compute_dir_digest(tmp_path)
        assert len(d1) == 64

    def test_content_change_changes_digest(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hello")
        d1 = compute_dir_digest(tmp_path)
        f.write_text("world")
        d2 = compute_dir_digest(tmp_path)
        assert d1 != d2

    def test_stable_across_calls(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        d1 = compute_dir_digest(tmp_path)
        d2 = compute_dir_digest(tmp_path)
        assert d1 == d2

    def test_string_path(self, tmp_path):
        (tmp_path / "file.txt").write_text("test")
        d = compute_dir_digest(str(tmp_path))
        assert len(d) == 64

    def test_nested_files(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "nested.txt").write_text("nested content")
        d = compute_dir_digest(tmp_path)
        assert len(d) == 64


# ---------------------------------------------------------------------------
# save_metadata / load_metadata
# ---------------------------------------------------------------------------

class TestSaveLoadMetadata:
    def test_save_and_load(self, tmp_path):
        md = SkillWorkspaceMetadata(version=5)
        save_metadata(tmp_path, md)
        loaded = load_metadata(tmp_path)
        assert loaded.version == 5

    def test_save_updates_updated_at(self, tmp_path):
        md = SkillWorkspaceMetadata(version=1)
        save_metadata(tmp_path, md)
        assert md.updated_at is not None

    def test_load_missing_returns_default(self, tmp_path):
        loaded = load_metadata(tmp_path)
        assert isinstance(loaded, SkillWorkspaceMetadata)
        assert loaded.version == 0

    def test_load_invalid_json_raises(self, tmp_path):
        from trpc_agent_sdk.code_executors import META_FILE_NAME
        meta_file = tmp_path / META_FILE_NAME
        meta_file.write_text("not json")
        with pytest.raises(ValueError, match="Invalid JSON"):
            load_metadata(tmp_path)

    def test_string_path(self, tmp_path):
        md = SkillWorkspaceMetadata(version=3)
        save_metadata(str(tmp_path), md)
        loaded = load_metadata(str(tmp_path))
        assert loaded.version == 3


# ---------------------------------------------------------------------------
# ensure_layout
# ---------------------------------------------------------------------------

class TestEnsureLayout:
    def test_creates_subdirectories(self, tmp_path):
        paths = ensure_layout(tmp_path)
        assert len(paths) == 4
        for p in paths.values():
            assert p.exists()

    def test_creates_metadata_file(self, tmp_path):
        from trpc_agent_sdk.code_executors import META_FILE_NAME
        ensure_layout(tmp_path)
        assert (tmp_path / META_FILE_NAME).exists()

    def test_idempotent(self, tmp_path):
        paths1 = ensure_layout(tmp_path)
        paths2 = ensure_layout(tmp_path)
        assert set(paths1.keys()) == set(paths2.keys())

    def test_string_path(self, tmp_path):
        paths = ensure_layout(str(tmp_path))
        assert len(paths) == 4


# ---------------------------------------------------------------------------
# shell_quote
# ---------------------------------------------------------------------------

class TestShellQuote:
    def test_simple_string(self):
        assert shell_quote("hello") == "'hello'"

    def test_empty_string(self):
        assert shell_quote("") == "''"

    def test_string_with_single_quote(self):
        result = shell_quote("it's")
        assert "'" in result
        assert "\\" in result

    def test_string_with_spaces(self):
        result = shell_quote("hello world")
        assert result == "'hello world'"

    def test_string_with_special_chars(self):
        result = shell_quote("a$b")
        assert result == "'a$b'"


# ---------------------------------------------------------------------------
# set_state_delta / get_state_delta
# ---------------------------------------------------------------------------

class TestStateDelta:
    def test_set_state_delta(self):
        ctx = _make_ctx()
        set_state_delta(ctx, "key", "value")
        assert ctx.actions.state_delta["key"] == "value"

    def test_get_state_delta_from_delta(self):
        ctx = _make_ctx(state_delta={"key": "delta_val"})
        result = get_state_delta(ctx, "key")
        assert result == "delta_val"

    def test_get_state_delta_from_session(self):
        ctx = _make_ctx(session_state={"key": "session_val"})
        result = get_state_delta(ctx, "key")
        assert result == "session_val"

    def test_get_state_delta_prefers_delta(self):
        ctx = _make_ctx(state_delta={"k": "d"}, session_state={"k": "s"})
        assert get_state_delta(ctx, "k") == "d"

    def test_get_state_delta_missing(self):
        ctx = _make_ctx()
        assert get_state_delta(ctx, "missing") is None
