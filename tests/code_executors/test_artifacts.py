# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import pytest
from unittest.mock import Mock

from trpc_agent_sdk.code_executors._artifacts import (
    load_artifact_helper,
    parse_artifact_ref,
    save_artifact_helper,
)
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.abc import ArtifactEntry, ArtifactVersion
from trpc_agent_sdk.types import Part


class TestParseArtifactRef:
    """Test suite for parse_artifact_ref function."""

    def test_parse_ref_without_version(self):
        """Test parsing artifact reference without version."""
        name, version = parse_artifact_ref("my-artifact")
        assert name == "my-artifact"
        assert version is None

    def test_parse_ref_with_version(self):
        """Test parsing artifact reference with version."""
        name, version = parse_artifact_ref("my-artifact@5")
        assert name == "my-artifact"
        assert version == 5

    def test_parse_ref_with_zero_version(self):
        """Test parsing artifact reference with version 0."""
        name, version = parse_artifact_ref("my-artifact@0")
        assert name == "my-artifact"
        assert version == 0

    def test_parse_ref_invalid_version_non_digit(self):
        """Test parsing artifact reference with non-digit version returns latest marker."""
        name, version = parse_artifact_ref("my-artifact@abc")
        assert name == "my-artifact"
        assert version is None

    def test_parse_ref_invalid_multiple_at_signs(self):
        """Test parsing artifact reference with multiple @ signs follows current parser behavior."""
        name, version = parse_artifact_ref("my-artifact@1@2")
        assert name == "my-artifact"
        assert version == 12

    def test_parse_ref_empty_name(self):
        """Test parsing artifact reference with empty name raises ValueError."""
        with pytest.raises(ValueError, match="invalid ref"):
            parse_artifact_ref("@5")


class TestLoadArtifactHelper:
    """Test suite for load_artifact_helper function."""

    @pytest.mark.asyncio
    async def test_load_artifact_with_version(self):
        """Test loading artifact with specific version."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_entry = Mock(spec=ArtifactEntry)
        mock_entry.version = Mock(spec=ArtifactVersion)
        mock_entry.version.version = 5
        mock_entry.data = Mock()
        mock_entry.data.inline_data = Mock()
        mock_entry.data.inline_data.data = b"test data"

        call_args_list = []

        async def mock_load_artifact(filename, version=None):
            call_args_list.append((filename, version))
            return mock_entry

        mock_ctx.load_artifact = mock_load_artifact

        result = await load_artifact_helper(mock_ctx, "my-artifact", version=5)

        assert result == (b"test data", 5)
        assert len(call_args_list) == 1
        assert call_args_list[0] == ("my-artifact", 5)

    @pytest.mark.asyncio
    async def test_load_artifact_without_version(self):
        """Test loading artifact without version (latest)."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_entry = Mock(spec=ArtifactEntry)
        mock_entry.version = Mock(spec=ArtifactVersion)
        mock_entry.version.version = 10
        mock_entry.data = Mock()
        mock_entry.data.inline_data = Mock()
        mock_entry.data.inline_data.data = b"latest data"

        call_args_list = []

        async def mock_load_artifact(filename, version=None):
            call_args_list.append((filename, version))
            return mock_entry

        mock_ctx.load_artifact = mock_load_artifact

        result = await load_artifact_helper(mock_ctx, "my-artifact", version=None)

        assert result == (b"latest data", 10)
        assert len(call_args_list) == 1
        assert call_args_list[0] == ("my-artifact", None)

    @pytest.mark.asyncio
    async def test_load_artifact_not_found(self):
        """Test loading artifact that doesn't exist returns None."""
        mock_ctx = Mock(spec=InvocationContext)

        async def mock_load_artifact(filename, version=None):
            return None

        mock_ctx.load_artifact = mock_load_artifact

        result = await load_artifact_helper(mock_ctx, "nonexistent", version=None)

        assert result is None

    @pytest.mark.asyncio
    async def test_load_artifact_version_zero(self):
        """Test loading artifact with version 0."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_entry = Mock(spec=ArtifactEntry)
        mock_entry.version = Mock(spec=ArtifactVersion)
        mock_entry.version.version = 0
        mock_entry.data = Mock()
        mock_entry.data.inline_data = Mock()
        mock_entry.data.inline_data.data = b"data"

        async def mock_load_artifact(filename, version=None):
            return mock_entry

        mock_ctx.load_artifact = mock_load_artifact

        result = await load_artifact_helper(mock_ctx, "artifact", version=None)

        assert result == (b"data", 0)


class TestSaveArtifactHelper:
    """Test suite for save_artifact_helper function."""

    @pytest.mark.asyncio
    async def test_save_artifact_success(self):
        """Test saving artifact successfully."""
        mock_ctx = Mock(spec=InvocationContext)
        call_args_list = []

        async def mock_save_artifact(filename, artifact):
            call_args_list.append((filename, artifact))
            return 3

        mock_ctx.save_artifact = mock_save_artifact

        result = await save_artifact_helper(mock_ctx, "test.txt", b"test data", "text/plain")

        assert result == 3
        assert len(call_args_list) == 1
        assert call_args_list[0][0] == "test.txt"
        assert isinstance(call_args_list[0][1], Part)
        assert call_args_list[0][1].inline_data.data == b"test data"
        assert call_args_list[0][1].inline_data.mime_type == "text/plain"

    @pytest.mark.asyncio
    async def test_save_artifact_with_different_mime(self):
        """Test saving artifact with different MIME type."""
        mock_ctx = Mock(spec=InvocationContext)
        call_args_list = []

        async def mock_save_artifact(filename, artifact):
            call_args_list.append((filename, artifact))
            return 1

        mock_ctx.save_artifact = mock_save_artifact

        result = await save_artifact_helper(mock_ctx, "image.png", b"image data", "image/png")

        assert result == 1
        assert len(call_args_list) == 1
        assert call_args_list[0][1].inline_data.mime_type == "image/png"
