# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import json
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.skills import SkillWorkspaceMetadata
from trpc_agent_sdk.skills import compute_dir_digest
from trpc_agent_sdk.skills import ensure_layout
from trpc_agent_sdk.skills import get_state_delta
from trpc_agent_sdk.skills import load_metadata
from trpc_agent_sdk.skills import save_metadata
from trpc_agent_sdk.skills import set_state_delta
from trpc_agent_sdk.skills import shell_quote


class TestComputeDirDigest:
    """Test suite for compute_dir_digest function."""

    def test_compute_dir_digest(self):
        """Test computing directory digest."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "file1.txt").write_text("content1")
            (root / "file2.txt").write_text("content2")
            (root / "subdir").mkdir()
            (root / "subdir" / "file3.txt").write_text("content3")

            digest = compute_dir_digest(root)

            assert isinstance(digest, str)
            assert len(digest) == 64  # SHA256 hex digest length

    def test_compute_dir_digest_string_path(self):
        """Test computing directory digest with string path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "file.txt").write_text("content")

            digest = compute_dir_digest(tmpdir)

            assert isinstance(digest, str)
            assert len(digest) == 64

    def test_compute_dir_digest_empty_directory(self):
        """Test computing digest of empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            digest = compute_dir_digest(tmpdir)

            assert isinstance(digest, str)
            assert len(digest) == 64

    def test_compute_dir_digest_same_content_same_digest(self):
        """Test that same content produces same digest."""
        with tempfile.TemporaryDirectory() as tmpdir1:
            with tempfile.TemporaryDirectory() as tmpdir2:
                (Path(tmpdir1) / "file.txt").write_text("content")
                (Path(tmpdir2) / "file.txt").write_text("content")

                digest1 = compute_dir_digest(tmpdir1)
                digest2 = compute_dir_digest(tmpdir2)

                assert digest1 == digest2

    def test_compute_dir_digest_different_content_different_digest(self):
        """Test that different content produces different digest."""
        with tempfile.TemporaryDirectory() as tmpdir1:
            with tempfile.TemporaryDirectory() as tmpdir2:
                (Path(tmpdir1) / "file.txt").write_text("content1")
                (Path(tmpdir2) / "file.txt").write_text("content2")

                digest1 = compute_dir_digest(tmpdir1)
                digest2 = compute_dir_digest(tmpdir2)

                assert digest1 != digest2


class TestSaveMetadata:
    """Test suite for save_metadata function."""

    def test_save_metadata(self):
        """Test saving metadata to file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            metadata = SkillWorkspaceMetadata(version=1)

            save_metadata(root, metadata)

            meta_file = root / "metadata.json"
            assert meta_file.exists()
            with open(meta_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                assert data["version"] == 1

    def test_save_metadata_string_path(self):
        """Test saving metadata with string path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata = SkillWorkspaceMetadata(version=1)

            save_metadata(tmpdir, metadata)

            meta_file = Path(tmpdir) / "metadata.json"
            assert meta_file.exists()

    def test_save_metadata_updates_updated_at(self):
        """Test that save_metadata updates updated_at timestamp."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            metadata = SkillWorkspaceMetadata(version=1)
            original_updated_at = metadata.updated_at

            save_metadata(root, metadata)

            assert metadata.updated_at is not None
            if original_updated_at:
                assert metadata.updated_at >= original_updated_at


class TestEnsureLayout:
    """Test suite for ensure_layout function."""

    def test_ensure_layout_creates_directories(self):
        """Test that ensure_layout creates required directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = ensure_layout(root)

            assert (root / "skills").exists()
            assert (root / "work").exists()
            assert (root / "runs").exists()
            assert (root / "out").exists()
            assert len(paths) == 4

    def test_ensure_layout_creates_metadata(self):
        """Test that ensure_layout creates metadata file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ensure_layout(root)

            meta_file = root / "metadata.json"
            assert meta_file.exists()

    def test_ensure_layout_string_path(self):
        """Test ensure_layout with string path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = ensure_layout(tmpdir)

            assert len(paths) == 4
            assert Path(tmpdir) / "skills" in paths.values()

    def test_ensure_layout_idempotent(self):
        """Test that ensure_layout is idempotent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths1 = ensure_layout(root)
            paths2 = ensure_layout(root)

            assert paths1 == paths2


class TestLoadMetadata:
    """Test suite for load_metadata function."""

    def test_load_metadata_existing_file(self):
        """Test loading metadata from existing file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            metadata = SkillWorkspaceMetadata(version=1)
            save_metadata(root, metadata)

            loaded = load_metadata(root)

            assert loaded.version == 1

    def test_load_metadata_nonexistent_file(self):
        """Test loading metadata when file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            loaded = load_metadata(root)

            assert isinstance(loaded, SkillWorkspaceMetadata)
            assert loaded.version == 0

    def test_load_metadata_string_path(self):
        """Test loading metadata with string path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            metadata = SkillWorkspaceMetadata(version=1)
            save_metadata(root, metadata)

            loaded = load_metadata(tmpdir)

            assert loaded.version == 1

    def test_load_metadata_invalid_json(self):
        """Test loading metadata with invalid JSON raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            meta_file = root / "metadata.json"
            meta_file.write_text("invalid json")

            with pytest.raises(ValueError, match="Invalid JSON"):
                load_metadata(root)


class TestShellQuote:
    """Test suite for shell_quote function."""

    def test_shell_quote_simple_string(self):
        """Test quoting simple string."""
        result = shell_quote("hello")

        assert result == "'hello'"

    def test_shell_quote_string_with_single_quote(self):
        """Test quoting string with single quote."""
        result = shell_quote("it's")

        assert result == "'it'\\''s'"

    def test_shell_quote_empty_string(self):
        """Test quoting empty string."""
        result = shell_quote("")

        assert result == "''"

    def test_shell_quote_string_with_spaces(self):
        """Test quoting string with spaces."""
        result = shell_quote("hello world")

        assert result == "'hello world'"

    def test_shell_quote_string_with_special_chars(self):
        """Test quoting string with special characters."""
        result = shell_quote("hello$world")

        assert result == "'hello$world'"


class TestSetStateDelta:
    """Test suite for set_state_delta function."""

    def test_set_state_delta(self):
        """Test setting state delta."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        set_state_delta(mock_ctx, "key", "value")

        assert mock_ctx.actions.state_delta["key"] == "value"

    def test_set_state_delta_overwrites_existing(self):
        """Test setting state delta overwrites existing value."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {"key": "old_value"}

        set_state_delta(mock_ctx, "key", "new_value")

        assert mock_ctx.actions.state_delta["key"] == "new_value"


class TestGetStateDelta:
    """Test suite for get_state_delta function."""

    def test_get_state_delta_from_state_delta(self):
        """Test getting state delta value."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {}
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {"key": "value"}

        result = get_state_delta(mock_ctx, "key")

        assert result == "value"

    def test_get_state_delta_from_session_state(self):
        """Test getting value from session state."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {"key": "session_value"}
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = get_state_delta(mock_ctx, "key")

        assert result == "session_value"

    def test_get_state_delta_state_delta_overrides_session(self):
        """Test that state_delta overrides session_state."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {"key": "session_value"}
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {"key": "delta_value"}

        result = get_state_delta(mock_ctx, "key")

        assert result == "delta_value"

    def test_get_state_delta_not_found(self):
        """Test getting state delta when key not found."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {}
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = get_state_delta(mock_ctx, "nonexistent")

        assert result is None

