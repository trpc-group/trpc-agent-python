# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from unittest.mock import Mock

import pytest
from trpc_agent_sdk.abc import ArtifactEntry
from trpc_agent_sdk.abc import ArtifactServiceABC
from trpc_agent_sdk.abc import ArtifactVersion
from trpc_agent_sdk.code_executors._artifacts import artifact_service_from_context
from trpc_agent_sdk.code_executors._artifacts import artifact_session_from_context
from trpc_agent_sdk.code_executors._artifacts import load_artifact_helper
from trpc_agent_sdk.code_executors._artifacts import parse_artifact_ref
from trpc_agent_sdk.code_executors._artifacts import save_artifact_helper
from trpc_agent_sdk.code_executors._artifacts import with_artifact_service
from trpc_agent_sdk.code_executors._artifacts import with_artifact_session
from trpc_agent_sdk.context import InvocationContext
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
        """Test parsing artifact reference with non-digit version raises ValueError."""
        with pytest.raises(ValueError, match="invalid version"):
            parse_artifact_ref("my-artifact@abc")

    def test_parse_ref_invalid_multiple_at_signs(self):
        """Test parsing artifact reference with multiple @ signs raises ValueError."""
        with pytest.raises(ValueError, match="invalid ref"):
            parse_artifact_ref("my-artifact@1@2")

    def test_parse_ref_empty_name(self):
        """Test parsing artifact reference with empty name."""
        name, version = parse_artifact_ref("@5")
        assert name == ""
        assert version == 5


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


class TestWithArtifactService:
    """Test suite for with_artifact_service function."""

    def test_with_artifact_service_sets_service(self):
        """Test setting artifact service in context."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_service = Mock(spec=ArtifactServiceABC)

        result = with_artifact_service(mock_ctx, mock_service)

        assert result == mock_ctx
        assert mock_ctx.artifact_service == mock_service

    def test_with_artifact_service_returns_context(self):
        """Test that function returns the context."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_service = Mock(spec=ArtifactServiceABC)

        result = with_artifact_service(mock_ctx, mock_service)

        assert result is mock_ctx


class TestArtifactServiceFromContext:
    """Test suite for artifact_service_from_context function."""

    def test_artifact_service_from_context_with_service(self):
        """Test retrieving artifact service from context."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_service = Mock(spec=ArtifactServiceABC)
        mock_ctx.artifact_service = mock_service

        result = artifact_service_from_context(mock_ctx)

        assert result == mock_service

    def test_artifact_service_from_context_without_service(self):
        """Test retrieving artifact service when not set returns None."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.artifact_service = None

        result = artifact_service_from_context(mock_ctx)

        assert result is None


class TestWithArtifactSession:
    """Test suite for with_artifact_session function."""

    def test_with_artifact_session_not_implemented(self):
        """Test that with_artifact_session raises AssertionError."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_info = Mock()

        with pytest.raises(AssertionError, match="Not implemented"):
            with_artifact_session(mock_ctx, mock_info)


class TestArtifactSessionFromContext:
    """Test suite for artifact_session_from_context function."""

    def test_artifact_session_from_context_not_implemented(self):
        """Test that artifact_session_from_context raises AssertionError."""
        mock_ctx = Mock(spec=InvocationContext)

        with pytest.raises(AssertionError, match="Not implemented"):
            artifact_session_from_context(mock_ctx)
