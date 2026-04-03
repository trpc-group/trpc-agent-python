# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for trpc_agent_sdk.types._event_actions.

Covers:
    - EventActions: defaults, field assignment, camelCase aliasing,
      extra-forbid, populate_by_name, serialisation
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trpc_agent_sdk.types._event_actions import EventActions


class TestEventActionsDefaults:
    """Default values and construction."""

    def test_default_construction(self):
        ea = EventActions()
        assert ea.skip_summarization is None
        assert ea.state_delta == {}
        assert ea.artifact_delta == {}
        assert ea.transfer_to_agent is None
        assert ea.escalate is None

    def test_state_delta_default_is_independent(self):
        ea1 = EventActions()
        ea2 = EventActions()
        ea1.state_delta["key"] = "value"
        assert "key" not in ea2.state_delta

    def test_artifact_delta_default_is_independent(self):
        ea1 = EventActions()
        ea2 = EventActions()
        ea1.artifact_delta["file.txt"] = 1
        assert "file.txt" not in ea2.artifact_delta


class TestEventActionsFields:
    """Explicit field assignment."""

    def test_skip_summarization(self):
        ea = EventActions(skip_summarization=True)
        assert ea.skip_summarization is True

    def test_state_delta(self):
        delta = {"counter": 5, "flag": True}
        ea = EventActions(state_delta=delta)
        assert ea.state_delta == delta

    def test_artifact_delta(self):
        delta = {"report.pdf": 3}
        ea = EventActions(artifact_delta=delta)
        assert ea.artifact_delta == delta

    def test_transfer_to_agent(self):
        ea = EventActions(transfer_to_agent="agent_b")
        assert ea.transfer_to_agent == "agent_b"

    def test_escalate(self):
        ea = EventActions(escalate=True)
        assert ea.escalate is True


class TestEventActionsCamelAlias:
    """camelCase alias generation and populate_by_name."""

    def test_camel_alias_in_serialisation(self):
        ea = EventActions(skip_summarization=True, transfer_to_agent="x")
        data = ea.model_dump(by_alias=True)
        assert "skipSummarization" in data
        assert "transferToAgent" in data
        assert "stateDelta" in data
        assert "artifactDelta" in data

    def test_construction_with_camel_alias(self):
        ea = EventActions(**{
            "skipSummarization": False,
            "stateDelta": {"k": "v"},
            "artifactDelta": {"f": 1},
            "transferToAgent": "agent_c",
            "escalate": True,
        })
        assert ea.skip_summarization is False
        assert ea.state_delta == {"k": "v"}
        assert ea.transfer_to_agent == "agent_c"

    def test_populate_by_name(self):
        ea = EventActions(skip_summarization=True)
        assert ea.skip_summarization is True

    def test_json_roundtrip_by_alias(self):
        ea = EventActions(
            skip_summarization=True,
            state_delta={"a": 1},
            artifact_delta={"b": 2},
            transfer_to_agent="target",
            escalate=False,
        )
        json_str = ea.model_dump_json(by_alias=True)
        restored = EventActions.model_validate_json(json_str)
        assert restored.skip_summarization is True
        assert restored.state_delta == {"a": 1}
        assert restored.artifact_delta == {"b": 2}
        assert restored.transfer_to_agent == "target"
        assert restored.escalate is False


class TestEventActionsExtraForbid:
    """Extra fields should be rejected."""

    def test_extra_field_raises(self):
        with pytest.raises(ValidationError):
            EventActions(unknown="bad")

    def test_extra_camel_field_raises(self):
        with pytest.raises(ValidationError):
            EventActions(unknownField="bad")
