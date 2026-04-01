# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for DelegationSignal."""

import pytest
from trpc_agent_sdk.teams.core import DELEGATION_SIGNAL_MARKER
from trpc_agent_sdk.teams.core import DelegationSignal


class TestDelegationSignalBasic:
    """Tests for DelegationSignal basic functionality."""

    def test_default_values(self):
        """Test DelegationSignal initializes with correct defaults."""
        signal = DelegationSignal()
        assert signal.marker == DELEGATION_SIGNAL_MARKER
        assert signal.action == "delegate_to_member"
        assert signal.member_name == ""
        assert signal.task == ""

    def test_create_with_values(self):
        """Test creating DelegationSignal with custom values."""
        signal = DelegationSignal(
            member_name="researcher",
            task="Find information about AI",
        )
        assert signal.member_name == "researcher"
        assert signal.task == "Find information about AI"
        assert signal.marker == DELEGATION_SIGNAL_MARKER

    def test_delegate_to_all_action(self):
        """Test DelegationSignal with delegate_to_all action."""
        signal = DelegationSignal(
            action="delegate_to_all",
            task="Process data",
        )
        assert signal.action == "delegate_to_all"


class TestDelegationSignalDetection:
    """Tests for DelegationSignal detection methods."""

    def test_is_delegation_signal_valid(self):
        """Test detecting valid delegation signal."""
        response_data = {
            "marker": DELEGATION_SIGNAL_MARKER,
            "action": "delegate_to_member",
            "member_name": "researcher",
            "task": "Do something",
        }
        assert DelegationSignal.is_delegation_signal(response_data) is True

    def test_is_delegation_signal_invalid_marker(self):
        """Test detecting invalid marker."""
        response_data = {
            "marker": "wrong_marker",
            "action": "delegate_to_member",
        }
        assert DelegationSignal.is_delegation_signal(response_data) is False

    def test_is_delegation_signal_missing_marker(self):
        """Test detecting missing marker."""
        response_data = {
            "action": "delegate_to_member",
            "member_name": "researcher",
        }
        assert DelegationSignal.is_delegation_signal(response_data) is False

    def test_is_delegation_signal_non_dict(self):
        """Test that non-dict input returns False."""
        assert DelegationSignal.is_delegation_signal(None) is False
        assert DelegationSignal.is_delegation_signal([]) is False
        assert DelegationSignal.is_delegation_signal(123) is False

    def test_is_delegation_signal_json_string_valid(self):
        """Test detecting valid delegation signal from JSON string."""
        import json
        json_string = json.dumps({
            "marker": DELEGATION_SIGNAL_MARKER,
            "action": "delegate_to_member",
            "member_name": "researcher",
            "task": "Do something",
        })
        assert DelegationSignal.is_delegation_signal(json_string) is True

    def test_is_delegation_signal_json_string_invalid_marker(self):
        """Test detecting invalid marker from JSON string."""
        import json
        json_string = json.dumps({
            "marker": "wrong_marker",
            "action": "delegate_to_member",
        })
        assert DelegationSignal.is_delegation_signal(json_string) is False

    def test_is_delegation_signal_invalid_json_string(self):
        """Test that invalid JSON string returns False."""
        assert DelegationSignal.is_delegation_signal("not valid json") is False
        assert DelegationSignal.is_delegation_signal("") is False
        assert DelegationSignal.is_delegation_signal("{invalid}") is False

    def test_is_delegation_signal_json_string_array(self):
        """Test that JSON array string returns False."""
        import json
        assert DelegationSignal.is_delegation_signal(json.dumps([])) is False
        assert DelegationSignal.is_delegation_signal(json.dumps([1, 2, 3])) is False

    def test_is_delegation_signal_delegation_instance(self):
        """Test detecting DelegationSignal instance directly."""
        signal = DelegationSignal(
            member_name="researcher",
            task="Do something",
        )
        assert DelegationSignal.is_delegation_signal(signal) is True


class TestDelegationSignalFromResponse:
    """Tests for creating DelegationSignal from response data."""

    def test_from_response_full_data(self):
        """Test creating signal from full response data."""
        response_data = {
            "marker": DELEGATION_SIGNAL_MARKER,
            "action": "delegate_to_member",
            "member_name": "writer",
            "task": "Write an article",
        }
        signal = DelegationSignal.from_response(response_data)

        assert signal.marker == DELEGATION_SIGNAL_MARKER
        assert signal.action == "delegate_to_member"
        assert signal.member_name == "writer"
        assert signal.task == "Write an article"

    def test_from_response_partial_data(self):
        """Test creating signal from partial response data."""
        response_data = {
            "marker": DELEGATION_SIGNAL_MARKER,
            "task": "Do something",
        }
        signal = DelegationSignal.from_response(response_data)

        assert signal.marker == DELEGATION_SIGNAL_MARKER
        assert signal.action == "delegate_to_member"  # default
        assert signal.member_name == ""  # default
        assert signal.task == "Do something"

    def test_from_response_empty_data(self):
        """Test creating signal from empty response data."""
        signal = DelegationSignal.from_response({})

        assert signal.marker == DELEGATION_SIGNAL_MARKER
        assert signal.action == "delegate_to_member"
        assert signal.member_name == ""
        assert signal.task == ""

    def test_from_response_json_string(self):
        """Test creating signal from JSON string."""
        import json
        json_string = json.dumps({
            "marker": DELEGATION_SIGNAL_MARKER,
            "action": "delegate_to_member",
            "member_name": "researcher",
            "task": "Research topic",
        })
        signal = DelegationSignal.from_response(json_string)

        assert signal is not None
        assert signal.marker == DELEGATION_SIGNAL_MARKER
        assert signal.member_name == "researcher"
        assert signal.task == "Research topic"

    def test_from_response_json_string_partial(self):
        """Test creating signal from partial JSON string."""
        import json
        json_string = json.dumps({
            "marker": DELEGATION_SIGNAL_MARKER,
            "task": "Do something",
        })
        signal = DelegationSignal.from_response(json_string)

        assert signal is not None
        assert signal.task == "Do something"
        assert signal.member_name == ""  # default
        assert signal.action == "delegate_to_member"  # default

    def test_from_response_invalid_json_string(self):
        """Test that invalid JSON string returns None."""
        assert DelegationSignal.from_response("not valid json") is None
        assert DelegationSignal.from_response("") is None
        assert DelegationSignal.from_response("{invalid}") is None

    def test_from_response_json_array_string(self):
        """Test that JSON array string returns None."""
        import json
        assert DelegationSignal.from_response(json.dumps([])) is None
        assert DelegationSignal.from_response(json.dumps([1, 2, 3])) is None


class TestDelegationSignalSerialization:
    """Tests for DelegationSignal serialization."""

    def test_model_dump(self):
        """Test Pydantic model serialization."""
        signal = DelegationSignal(
            member_name="researcher",
            task="Research topic",
        )
        data = signal.model_dump()

        assert data["marker"] == DELEGATION_SIGNAL_MARKER
        assert data["action"] == "delegate_to_member"
        assert data["member_name"] == "researcher"
        assert data["task"] == "Research topic"

    def test_roundtrip_serialization(self):
        """Test serialization roundtrip."""
        original = DelegationSignal(
            action="delegate_to_all",
            member_name="",
            task="Broadcast task",
        )

        # Serialize
        data = original.model_dump()

        # Deserialize
        restored = DelegationSignal.from_response(data)

        assert restored.marker == original.marker
        assert restored.action == original.action
        assert restored.member_name == original.member_name
        assert restored.task == original.task


class TestDelegationSignalMarker:
    """Tests for the delegation signal marker constant."""

    def test_marker_value(self):
        """Test that marker has expected value."""
        assert DELEGATION_SIGNAL_MARKER == "__TEAM_DELEGATION__"

    def test_marker_used_in_signal(self):
        """Test that signal uses the correct marker."""
        signal = DelegationSignal()
        assert signal.marker == DELEGATION_SIGNAL_MARKER
