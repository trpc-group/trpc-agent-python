# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for steering content marking."""

from __future__ import annotations

import pytest

from trpc_agent_sdk import cancel
from trpc_agent_sdk.cancel import SessionKey
from trpc_agent_sdk.cancel import SteeringStatus
from trpc_agent_sdk.cancel import mark_steering_content
from trpc_agent_sdk.cancel import _public
from trpc_agent_sdk.types import Blob
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


@pytest.fixture
def run_key():
    cancel.destroy_shared_mem()
    key = SessionKey("app", "u", "s")
    cancel.register_run(key)
    yield key
    cancel.unregister_run(key)


class TestMarkSteeringContent:

    def test_marks_text_content(self):
        content = Content(role="user", parts=[Part(text="check Shanghai weather instead")])

        marked = mark_steering_content(content)

        assert marked is not content
        assert marked.role == "user"
        assert [part.text for part in marked.parts] == [
            _public._STEERING_PREFIX,
            "check Shanghai weather instead",
            _public._STEERING_SUFFIX,
        ]
        assert content.parts == [Part(text="check Shanghai weather instead")]

    def test_preserves_multimodal_parts(self):
        image = b"\x89PNG\r\n\x1a\n\x00\xff"
        content = Content(
            role="user",
            parts=[Part(text="look"), Part(inline_data=Blob(mime_type="image/png", data=image))],
        )

        marked = mark_steering_content(content)

        assert marked.parts[0].text == _public._STEERING_PREFIX
        assert marked.parts[1].text == "look"
        assert marked.parts[2].inline_data.mime_type == "image/png"
        assert marked.parts[2].inline_data.data == image
        assert marked.parts[3].text == _public._STEERING_SUFFIX

    def test_preserves_role(self):
        content = Content(role="model", parts=[Part(text="x")])

        assert mark_steering_content(content).role == "model"

    def test_empty_parts_get_markers_only(self):
        marked = mark_steering_content(Content(role="user"))

        assert [part.text for part in marked.parts] == [
            _public._STEERING_PREFIX,
            _public._STEERING_SUFFIX,
        ]


class TestConsumeSteering:

    async def test_returns_none_without_pending(self, run_key):
        assert await cancel.consume_steering(run_key) is None

    async def test_merges_parts_in_fifo_order(self, run_key):
        image = b"\x89PNG\r\n\x1a\n\x00\xff"
        first = Content(
            role="user",
            parts=[Part(text="first"), Part(inline_data=Blob(mime_type="image/png", data=image))],
        )
        second = Content(role="user", parts=[Part(text="second")])
        assert await cancel.send_steering(run_key, first) is SteeringStatus.ACCEPTED
        assert await cancel.send_steering(run_key, second) is SteeringStatus.ACCEPTED

        merged = await cancel.consume_steering(run_key)

        assert merged is not None
        assert merged.role == "user"
        assert merged.parts[0].text == "first"
        assert merged.parts[1].inline_data.data == image
        assert merged.parts[2].text == "second"
        assert await cancel.consume_steering(run_key) is None

    async def test_merged_content_has_user_role(self, run_key):
        content = Content(role="model", parts=[Part(text="steer")])
        assert await cancel.send_steering(run_key, content) is SteeringStatus.ACCEPTED

        merged = await cancel.consume_steering(run_key)

        assert merged is not None
        assert merged.role == "user"
