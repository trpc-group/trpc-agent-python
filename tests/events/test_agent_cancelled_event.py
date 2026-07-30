# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for AgentCancelledEvent."""

from __future__ import annotations

import uuid

import pytest

from trpc_agent_sdk.events._agent_cancelled_event import AgentCancelledEvent


# ---------------------------------------------------------------------------
# AgentCancelledEvent creation
# ---------------------------------------------------------------------------


class TestAgentCancelledEventCreation:
    def test_default_reason(self):
        event = AgentCancelledEvent(
            invocation_id="inv-1",
            author="test_agent",
        )
        assert event.invocation_id == "inv-1"
        assert event.author == "test_agent"
        assert event.error_code == "run_cancelled"
        assert event.error_message == "Run cancelled by user"

    def test_custom_reason(self):
        event = AgentCancelledEvent(
            invocation_id="inv-2",
            author="my_agent",
            reason="Timeout exceeded",
        )
        assert event.error_message == "Timeout exceeded"
        assert event.error_code == "run_cancelled"

    def test_with_branch(self):
        event = AgentCancelledEvent(
            invocation_id="inv-1",
            author="agent",
            branch="root.child",
        )
        assert event.branch == "root.child"

    def test_without_branch(self):
        event = AgentCancelledEvent(
            invocation_id="inv-1",
            author="agent",
        )
        assert event.branch is None


# ---------------------------------------------------------------------------
# AgentCancelledEvent inherits Event
# ---------------------------------------------------------------------------


class TestAgentCancelledEventInheritance:
    def test_is_event_instance(self):
        from trpc_agent_sdk.events._event import Event

        event = AgentCancelledEvent(invocation_id="inv-1", author="a")
        assert isinstance(event, Event)

    def test_has_auto_generated_id(self):
        event = AgentCancelledEvent(invocation_id="inv-1", author="a")
        assert event.id != ""
        uuid.UUID(event.id)

    def test_has_timestamp(self):
        event = AgentCancelledEvent(invocation_id="inv-1", author="a")
        assert event.timestamp > 0

    def test_is_error_returns_true(self):
        event = AgentCancelledEvent(invocation_id="inv-1", author="a")
        assert event.is_error() is True


# ---------------------------------------------------------------------------
# model_post_init ensures error_code defaults
# ---------------------------------------------------------------------------


class TestModelPostInit:
    def test_error_code_set_by_default(self):
        event = AgentCancelledEvent(invocation_id="inv-1", author="a")
        assert event.error_code == "run_cancelled"

    def test_error_message_set_by_default(self):
        event = AgentCancelledEvent(invocation_id="inv-1", author="a")
        assert event.error_message == "Run cancelled by user"

    def test_custom_reason_preserved(self):
        event = AgentCancelledEvent(
            invocation_id="inv-1",
            author="a",
            reason="custom reason",
        )
        assert event.error_message == "custom reason"

    def test_each_event_gets_unique_id(self):
        e1 = AgentCancelledEvent(invocation_id="inv-1", author="a")
        e2 = AgentCancelledEvent(invocation_id="inv-1", author="a")
        assert e1.id != e2.id

    def test_post_init_fallback_sets_error_code_when_empty(self):
        event = AgentCancelledEvent.model_construct(
            invocation_id="inv-1",
            author="a",
            error_code=None,
            error_message=None,
            id="test-id",
        )
        event.model_post_init(None)
        assert event.error_code == "run_cancelled"
        assert event.error_message == "Run cancelled by user"

    def test_post_init_preserves_existing_error_code(self):
        event = AgentCancelledEvent.model_construct(
            invocation_id="inv-1",
            author="a",
            error_code="custom_code",
            error_message="custom msg",
            id="test-id",
        )
        event.model_post_init(None)
        assert event.error_code == "custom_code"
        assert event.error_message == "custom msg"


# ---------------------------------------------------------------------------
# kwargs pass through to Event
# ---------------------------------------------------------------------------


class TestKwargsPassthrough:
    def test_visible_passthrough(self):
        event = AgentCancelledEvent(
            invocation_id="inv-1",
            author="a",
            visible=False,
        )
        assert event.visible is False

    def test_request_id_passthrough(self):
        event = AgentCancelledEvent(
            invocation_id="inv-1",
            author="a",
            request_id="req-123",
        )
        assert event.request_id == "req-123"

    def test_tag_passthrough(self):
        event = AgentCancelledEvent(
            invocation_id="inv-1",
            author="a",
            tag="cancel-tag",
        )
        assert event.tag == "cancel-tag"


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestAgentCancelledEventSerialization:
    def test_camel_case_dump(self):
        event = AgentCancelledEvent(invocation_id="inv-1", author="a")
        data = event.model_dump(by_alias=True)
        assert data["errorCode"] == "run_cancelled"
        assert data["errorMessage"] == "Run cancelled by user"

    def test_round_trip_json(self):
        event = AgentCancelledEvent(
            invocation_id="inv-1",
            author="agent",
            reason="test cancel",
            branch="b1",
        )
        json_str = event.model_dump_json(by_alias=True)
        from trpc_agent_sdk.events._event import Event

        restored = Event.model_validate_json(json_str)
        assert restored.invocation_id == "inv-1"
        assert restored.error_code == "run_cancelled"
        assert restored.error_message == "test cancel"
        assert restored.branch == "b1"
