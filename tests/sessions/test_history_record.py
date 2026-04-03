# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.sessions._history_record.

Covers:
- HistoryRecord: add_record, build_content, validation
"""

from __future__ import annotations

import pytest

from trpc_agent_sdk.sessions._history_record import HistoryRecord


class TestHistoryRecordDefaults:
    """Test HistoryRecord creation and defaults."""

    def test_default_empty_lists(self):
        record = HistoryRecord()
        assert record.user_texts == []
        assert record.assistant_texts == []


class TestHistoryRecordAddRecord:
    """Test add_record method."""

    def test_add_user_only(self):
        record = HistoryRecord()
        record.add_record("Hello")
        assert len(record.user_texts) == 1
        assert record.user_texts[0] == "user: Hello"
        assert len(record.assistant_texts) == 0

    def test_add_user_and_assistant(self):
        record = HistoryRecord()
        record.add_record("Hello", "Hi there")
        assert record.user_texts[0] == "user: Hello"
        assert record.assistant_texts[0] == "assistant: Hi there"

    def test_user_text_already_prefixed(self):
        record = HistoryRecord()
        record.add_record("user: Hello")
        assert record.user_texts[0] == "user: Hello"

    def test_assistant_text_already_prefixed(self):
        record = HistoryRecord()
        record.add_record("Hello", "assistant: Hi")
        assert record.assistant_texts[0] == "assistant: Hi"

    def test_add_multiple_records(self):
        record = HistoryRecord()
        record.add_record("Q1", "A1")
        record.add_record("Q2", "A2")
        assert len(record.user_texts) == 2
        assert len(record.assistant_texts) == 2

    def test_add_record_empty_user_with_assistant_raises(self):
        record = HistoryRecord()
        with pytest.raises(ValueError, match="when user text is empty"):
            record.add_record("", "some assistant text")

    def test_add_record_empty_assistant_text(self):
        record = HistoryRecord()
        record.add_record("Hello", "")
        assert len(record.user_texts) == 1
        assert len(record.assistant_texts) == 0

    def test_add_record_none_assistant_text(self):
        record = HistoryRecord()
        record.add_record("Hello", None)
        assert len(record.user_texts) == 1
        assert len(record.assistant_texts) == 0


class TestHistoryRecordBuildContent:
    """Test build_content method."""

    def test_build_content_single_pair(self):
        record = HistoryRecord()
        record.add_record("Hello", "Hi there")
        content = record.build_content("Next question")
        assert content.role == "user"
        assert len(content.parts) == 3
        assert content.parts[0].text == "user: Hello"
        assert content.parts[1].text == "assistant: Hi there"
        assert content.parts[2].text == "Next question"

    def test_build_content_multiple_pairs(self):
        record = HistoryRecord()
        record.add_record("Q1", "A1")
        record.add_record("Q2", "A2")
        content = record.build_content("Q3")
        assert len(content.parts) == 5

    def test_build_content_with_unanswered_question(self):
        record = HistoryRecord()
        record.add_record("Q1", "A1")
        record.add_record("Q2")
        content = record.build_content("follow-up")
        assert len(content.parts) == 4
        assert content.parts[0].text == "user: Q1"
        assert content.parts[1].text == "assistant: A1"
        assert content.parts[2].text == "user: Q2"
        assert content.parts[3].text == "follow-up"

    def test_build_content_empty_user_message(self):
        record = HistoryRecord()
        record.add_record("Hello", "Hi")
        content = record.build_content("")
        assert content.parts[-1].text == ""

    def test_build_content_raises_if_more_assistants_than_users(self):
        record = HistoryRecord()
        record.user_texts = ["user: Q1"]
        record.assistant_texts = ["assistant: A1", "assistant: A2"]
        with pytest.raises(ValueError, match="user texts must more than assistant texts"):
            record.build_content("test")

    def test_build_content_no_records(self):
        record = HistoryRecord()
        content = record.build_content("Hello")
        assert len(content.parts) == 1
        assert content.parts[0].text == "Hello"
