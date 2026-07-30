# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for _feed_back_content module."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.server.ag_ui._core._feed_back_content import AgUiUserFeedBack


def _make_session() -> Session:
    return Session(
        id="sess-1",
        app_name="test-app",
        user_id="user-1",
        save_key="test-key",
        state={},
    )


class TestAgUiUserFeedBackCreation:
    def test_fields_assigned(self):
        session = _make_session()
        fb = AgUiUserFeedBack(
            session=session,
            tool_name="my_tool",
            tool_message="result text",
        )
        assert fb.session is session
        assert fb.tool_name == "my_tool"
        assert fb.tool_message == "result text"

    def test_session_modified_default_false(self):
        fb = AgUiUserFeedBack(
            session=_make_session(),
            tool_name="t",
            tool_message="m",
        )
        assert fb.check_session_modified() is False


class TestMarkSessionModified:
    def test_marks_true(self):
        fb = AgUiUserFeedBack(
            session=_make_session(),
            tool_name="t",
            tool_message="m",
        )
        fb.mark_session_modified()
        assert fb.check_session_modified() is True

    def test_idempotent(self):
        fb = AgUiUserFeedBack(
            session=_make_session(),
            tool_name="t",
            tool_message="m",
        )
        fb.mark_session_modified()
        fb.mark_session_modified()
        assert fb.check_session_modified() is True


class TestCheckSessionModified:
    def test_false_before_mark(self):
        fb = AgUiUserFeedBack(
            session=_make_session(),
            tool_name="t",
            tool_message="m",
        )
        assert fb.check_session_modified() is False

    def test_true_after_mark(self):
        fb = AgUiUserFeedBack(
            session=_make_session(),
            tool_name="t",
            tool_message="m",
        )
        fb.mark_session_modified()
        assert fb.check_session_modified() is True


class TestToolMessageModification:
    def test_tool_message_can_be_updated(self):
        fb = AgUiUserFeedBack(
            session=_make_session(),
            tool_name="t",
            tool_message="original",
        )
        fb.tool_message = "modified"
        assert fb.tool_message == "modified"
