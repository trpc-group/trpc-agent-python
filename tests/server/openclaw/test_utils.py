# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.server.openclaw._utils."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trpc_agent_sdk.server.openclaw._utils import (
    CHANNELS_WITHOUT_STREAM_PROGRESS,
    build_user_parts,
    is_channel_supports_stream_progress,
    merge_assistant_text,
    merge_raw_events,
    parse_origin,
    register_channel_without_stream_progress,
    write_file,
)


# ---------------------------------------------------------------------------
# parse_origin
# ---------------------------------------------------------------------------
class TestParseOrigin:

    def test_non_system_channel_returns_channel_and_chat_id(self):
        msg = MagicMock(channel="telegram", chat_id="12345")
        assert parse_origin(msg) == ("telegram", "12345")

    def test_system_channel_with_colon_splits_on_first_colon(self):
        msg = MagicMock(channel="system", chat_id="wecom:group:abc")
        result = parse_origin(msg)
        assert list(result) == ["wecom", "group:abc"]

    def test_system_channel_without_colon_defaults_to_cli(self):
        msg = MagicMock(channel="system", chat_id="session123")
        assert parse_origin(msg) == ("cli", "session123")


# ---------------------------------------------------------------------------
# stream progress helpers
# ---------------------------------------------------------------------------
class TestStreamProgress:

    def test_unsupported_channel(self):
        assert is_channel_supports_stream_progress("cli") is False
        assert is_channel_supports_stream_progress("telegram") is False
        assert is_channel_supports_stream_progress("wecom") is False

    def test_supported_channel(self):
        assert is_channel_supports_stream_progress("slack") is True
        assert is_channel_supports_stream_progress("web") is True

    def test_register_new_channel(self):
        channel = "__test_no_stream__"
        try:
            assert is_channel_supports_stream_progress(channel) is True
            register_channel_without_stream_progress(channel)
            assert is_channel_supports_stream_progress(channel) is False
        finally:
            CHANNELS_WITHOUT_STREAM_PROGRESS.discard(channel)


# ---------------------------------------------------------------------------
# merge_assistant_text
# ---------------------------------------------------------------------------
class TestMergeAssistantText:

    def test_both_empty(self):
        assert merge_assistant_text("", "") == ""

    def test_incoming_empty_returns_current(self):
        assert merge_assistant_text("hello", "") == "hello"

    def test_current_empty_returns_incoming(self):
        assert merge_assistant_text("", "world") == "world"

    def test_cumulative_incoming_starts_with_current(self):
        assert merge_assistant_text("hel", "hello world") == "hello world"

    def test_trailing_dup_current_ends_with_incoming(self):
        assert merge_assistant_text("hello world", "world") == "hello world"

    def test_incoming_contained_in_current(self):
        assert merge_assistant_text("hello world", "lo wo") == "hello world"

    def test_current_contained_in_incoming(self):
        assert merge_assistant_text("llo", "hello") == "hello"

    def test_no_overlap_concatenates(self):
        assert merge_assistant_text("abc", "xyz") == "abcxyz"


# ---------------------------------------------------------------------------
# build_user_parts
# ---------------------------------------------------------------------------
class TestBuildUserParts:

    @patch("trpc_agent_sdk.server.openclaw._utils.Part")
    @patch("trpc_agent_sdk.server.openclaw._utils.detect_image_mime")
    def test_no_media_returns_text_only(self, mock_detect, mock_part):
        sentinel = MagicMock()
        mock_part.from_text.return_value = sentinel

        result = build_user_parts("hi")
        assert result == [sentinel]
        mock_part.from_text.assert_called_once_with(text="hi")
        mock_detect.assert_not_called()

    @patch("trpc_agent_sdk.server.openclaw._utils.Part")
    @patch("trpc_agent_sdk.server.openclaw._utils.detect_image_mime")
    @patch("trpc_agent_sdk.server.openclaw._utils.Path")
    def test_valid_image_media(self, mock_path_cls, mock_detect, mock_part):
        fake_path = MagicMock()
        fake_path.is_file.return_value = True
        fake_path.read_bytes.return_value = b"\x89PNG"
        mock_path_cls.return_value = fake_path
        mock_detect.return_value = "image/png"

        img_part = MagicMock()
        txt_part = MagicMock()
        mock_part.from_bytes.return_value = img_part
        mock_part.from_text.return_value = txt_part

        result = build_user_parts("desc", media=["/tmp/photo.png"])
        assert result == [img_part, txt_part]
        mock_part.from_bytes.assert_called_once_with(data=b"\x89PNG", mime_type="image/png")

    @patch("trpc_agent_sdk.server.openclaw._utils.Part")
    @patch("trpc_agent_sdk.server.openclaw._utils.detect_image_mime")
    @patch("trpc_agent_sdk.server.openclaw._utils.Path")
    def test_non_image_media_skipped(self, mock_path_cls, mock_detect, mock_part):
        fake_path = MagicMock()
        fake_path.is_file.return_value = True
        fake_path.read_bytes.return_value = b"data"
        fake_path.__str__ = lambda self: "/tmp/file.mp4"
        mock_path_cls.return_value = fake_path
        mock_detect.return_value = None

        with patch("trpc_agent_sdk.server.openclaw._utils.mimetypes") as mock_mt:
            mock_mt.guess_type.return_value = ("video/mp4", None)
            result = build_user_parts("q", media=["/tmp/file.mp4"])

        assert len(result) == 1
        mock_part.from_bytes.assert_not_called()

    @patch("trpc_agent_sdk.server.openclaw._utils.Part")
    @patch("trpc_agent_sdk.server.openclaw._utils.detect_image_mime")
    @patch("trpc_agent_sdk.server.openclaw._utils.Path")
    def test_nonexistent_file_skipped(self, mock_path_cls, mock_detect, mock_part):
        fake_path = MagicMock()
        fake_path.is_file.return_value = False
        mock_path_cls.return_value = fake_path

        result = build_user_parts("q", media=["/no/such/file.png"])
        assert len(result) == 1
        mock_part.from_bytes.assert_not_called()

    @patch("trpc_agent_sdk.server.openclaw._utils.Part")
    @patch("trpc_agent_sdk.server.openclaw._utils.detect_image_mime")
    @patch("trpc_agent_sdk.server.openclaw._utils.Path")
    def test_exception_in_media_processing_skipped(self, mock_path_cls, mock_detect, mock_part):
        mock_path_cls.side_effect = RuntimeError("boom")
        txt_part = MagicMock()
        mock_part.from_text.return_value = txt_part

        result = build_user_parts("q", media=["/bad/path"])
        assert result == [txt_part]

    @patch("trpc_agent_sdk.server.openclaw._utils.Part")
    @patch("trpc_agent_sdk.server.openclaw._utils.detect_image_mime")
    @patch("trpc_agent_sdk.server.openclaw._utils.Path")
    def test_detect_returns_none_but_guess_returns_image(self, mock_path_cls, mock_detect, mock_part):
        fake_path = MagicMock()
        fake_path.is_file.return_value = True
        fake_path.read_bytes.return_value = b"raw"
        fake_path.__str__ = lambda self: "/tmp/photo.bmp"
        mock_path_cls.return_value = fake_path
        mock_detect.return_value = None

        img_part = MagicMock()
        mock_part.from_bytes.return_value = img_part

        with patch("trpc_agent_sdk.server.openclaw._utils.mimetypes") as mock_mt:
            mock_mt.guess_type.return_value = ("image/bmp", None)
            result = build_user_parts("q", media=["/tmp/photo.bmp"])

        assert len(result) == 2
        mock_part.from_bytes.assert_called_once_with(data=b"raw", mime_type="image/bmp")

    @patch("trpc_agent_sdk.server.openclaw._utils.Part")
    @patch("trpc_agent_sdk.server.openclaw._utils.detect_image_mime")
    @patch("trpc_agent_sdk.server.openclaw._utils.Path")
    def test_mime_is_none_entirely_skipped(self, mock_path_cls, mock_detect, mock_part):
        fake_path = MagicMock()
        fake_path.is_file.return_value = True
        fake_path.read_bytes.return_value = b"raw"
        fake_path.__str__ = lambda self: "/tmp/unknownfile"
        mock_path_cls.return_value = fake_path
        mock_detect.return_value = None

        with patch("trpc_agent_sdk.server.openclaw._utils.mimetypes") as mock_mt:
            mock_mt.guess_type.return_value = (None, None)
            result = build_user_parts("q", media=["/tmp/unknownfile"])

        assert len(result) == 1
        mock_part.from_bytes.assert_not_called()


# ---------------------------------------------------------------------------
# merge_raw_events
# ---------------------------------------------------------------------------
class TestMergeRawEvents:

    def test_both_empty(self):
        assert merge_raw_events([], []) == []

    def test_both_none(self):
        assert merge_raw_events(None, None) == []

    def test_dedup_by_id(self):
        e1 = MagicMock(id="a")
        e2 = MagicMock(id="b")
        e3 = MagicMock(id="a")
        result = merge_raw_events([e1, e2], [e3])
        assert result == [e1, e2]

    def test_events_without_id_always_included(self):
        e1 = MagicMock(spec=[])
        e2 = MagicMock(spec=[])
        result = merge_raw_events([e1], [e2])
        assert result == [e1, e2]

    def test_mixed_id_and_no_id(self):
        e_with_id = MagicMock(id="x")
        e_no_id = MagicMock(spec=[])
        e_dup = MagicMock(id="x")
        result = merge_raw_events([e_with_id, e_no_id], [e_dup, e_no_id])
        assert len(result) == 3
        assert result[0] is e_with_id
        assert result[1] is e_no_id
        assert result[2] is e_no_id

    def test_empty_string_id_not_deduped(self):
        e1 = MagicMock(id="")
        e2 = MagicMock(id="")
        result = merge_raw_events([e1], [e2])
        assert result == [e1, e2]

    def test_none_id_not_deduped(self):
        e1 = MagicMock(id=None)
        e2 = MagicMock(id=None)
        result = merge_raw_events([e1], [e2])
        assert result == [e1, e2]


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------
class TestWriteFile:

    def test_dest_does_not_exist_writes(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("content", encoding="utf-8")
        dest = tmp_path / "dest.txt"

        write_file(src, dest)
        assert dest.read_text(encoding="utf-8") == "content"

    def test_dest_exists_no_force_skips(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("new", encoding="utf-8")
        dest = tmp_path / "dest.txt"
        dest.write_text("old", encoding="utf-8")

        write_file(src, dest, force=False)
        assert dest.read_text(encoding="utf-8") == "old"

    def test_dest_exists_force_overwrites(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("new", encoding="utf-8")
        dest = tmp_path / "dest.txt"
        dest.write_text("old", encoding="utf-8")

        write_file(src, dest, force=True)
        assert dest.read_text(encoding="utf-8") == "new"

    def test_creates_parent_directories(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("nested", encoding="utf-8")
        dest = tmp_path / "a" / "b" / "c" / "dest.txt"

        write_file(src, dest)
        assert dest.read_text(encoding="utf-8") == "nested"

    def test_src_none_writes_empty_string(self, tmp_path):
        dest = tmp_path / "dest.txt"
        write_file(None, dest)
        assert dest.read_text(encoding="utf-8") == ""
