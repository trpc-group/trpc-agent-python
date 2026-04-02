# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for trpc_agent_sdk.abc._session.SessionABC.

Covers:
- Required fields and validation
- Default values (state, last_update_time, conversation_count)
- Pydantic config (extra="forbid", alias generation)
- Serialization / deserialization
"""

from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from trpc_agent_sdk.abc._session import SessionABC


class TestSessionCreation:
    """Tests for creating SessionABC instances."""

    def test_minimal_creation(self):
        s = SessionABC(id="s1", app_name="app", user_id="u1", save_key="app:u1")
        assert s.id == "s1"
        assert s.app_name == "app"
        assert s.user_id == "u1"
        assert s.save_key == "app:u1"

    def test_state_defaults_to_empty_dict(self):
        s = SessionABC(id="s1", app_name="app", user_id="u1", save_key="k")
        assert s.state == {}

    def test_conversation_count_defaults_to_zero(self):
        s = SessionABC(id="s1", app_name="app", user_id="u1", save_key="k")
        assert s.conversation_count == 0

    def test_last_update_time_defaults_to_now(self):
        before = time.time()
        s = SessionABC(id="s1", app_name="app", user_id="u1", save_key="k")
        after = time.time()
        assert before <= s.last_update_time <= after

    def test_custom_state(self):
        s = SessionABC(
            id="s1", app_name="app", user_id="u1", save_key="k",
            state={"key": "value"},
        )
        assert s.state == {"key": "value"}

    def test_custom_conversation_count(self):
        s = SessionABC(
            id="s1", app_name="app", user_id="u1", save_key="k",
            conversation_count=5,
        )
        assert s.conversation_count == 5


class TestSessionValidation:
    """Tests for Pydantic validation rules."""

    def test_missing_required_field_id_raises(self):
        with pytest.raises(ValidationError):
            SessionABC(app_name="app", user_id="u1", save_key="k")

    def test_missing_required_field_app_name_raises(self):
        with pytest.raises(ValidationError):
            SessionABC(id="s1", user_id="u1", save_key="k")

    def test_missing_required_field_user_id_raises(self):
        with pytest.raises(ValidationError):
            SessionABC(id="s1", app_name="app", save_key="k")

    def test_missing_required_field_save_key_raises(self):
        with pytest.raises(ValidationError):
            SessionABC(id="s1", app_name="app", user_id="u1")

    def test_extra_fields_are_forbidden(self):
        with pytest.raises(ValidationError):
            SessionABC(
                id="s1", app_name="app", user_id="u1", save_key="k",
                unknown_field="oops",
            )


class TestSessionSerialization:
    """Tests for serialization (model_dump) with camelCase aliases."""

    def test_model_dump_by_alias(self):
        s = SessionABC(
            id="s1", app_name="app", user_id="u1", save_key="k",
            conversation_count=3,
        )
        data = s.model_dump(by_alias=True)
        assert data["appName"] == "app"
        assert data["userId"] == "u1"
        assert data["saveKey"] == "k"
        assert data["conversationCount"] == 3
        assert data["lastUpdateTime"] == s.last_update_time

    def test_model_dump_by_field_name(self):
        s = SessionABC(id="s1", app_name="app", user_id="u1", save_key="k")
        data = s.model_dump()
        assert "app_name" in data
        assert "user_id" in data

    def test_populate_by_alias_name(self):
        s = SessionABC(
            id="s1", appName="app", userId="u1", saveKey="k",
        )
        assert s.app_name == "app"
        assert s.user_id == "u1"
        assert s.save_key == "k"


class TestSessionMutability:
    """Tests that session fields can be mutated after creation."""

    def test_state_can_be_updated(self):
        s = SessionABC(id="s1", app_name="app", user_id="u1", save_key="k")
        s.state["new_key"] = 42
        assert s.state["new_key"] == 42

    def test_conversation_count_can_be_incremented(self):
        s = SessionABC(id="s1", app_name="app", user_id="u1", save_key="k")
        s.conversation_count += 1
        assert s.conversation_count == 1

    def test_last_update_time_can_be_set(self):
        s = SessionABC(id="s1", app_name="app", user_id="u1", save_key="k")
        s.last_update_time = 12345.0
        assert s.last_update_time == 12345.0
