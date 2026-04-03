# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for AgUiTranslationContext and AgUiLangGraphEventTranslator."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from trpc_agent_sdk.agents.utils import TRPC_EVENT_MARKER
from trpc_agent_sdk.events import Event as TrpcEvent
from trpc_agent_sdk.server.ag_ui._plugin._langgraph_event_translator import (
    AgUiLangGraphEventTranslator,
    AgUiTranslationContext,
)


class ConcreteTranslator(AgUiLangGraphEventTranslator):
    """Concrete subclass to test the non-abstract need_translate method."""

    async def translate(self, event, context):
        yield None  # pragma: no cover


class TestAgUiTranslationContext:
    def test_creation(self):
        ctx = AgUiTranslationContext(thread_id="t1", run_id="r1")
        assert ctx.thread_id == "t1"
        assert ctx.run_id == "r1"

    def test_equality(self):
        a = AgUiTranslationContext(thread_id="t", run_id="r")
        b = AgUiTranslationContext(thread_id="t", run_id="r")
        assert a == b

    def test_different_values(self):
        a = AgUiTranslationContext(thread_id="t1", run_id="r1")
        b = AgUiTranslationContext(thread_id="t2", run_id="r2")
        assert a != b


class TestNeedTranslate:
    def _make_event(self, *, custom_metadata=None):
        event = Mock(spec=TrpcEvent)
        event.custom_metadata = custom_metadata
        return event

    def test_true_when_marker_present(self):
        translator = ConcreteTranslator()
        event = self._make_event(custom_metadata={TRPC_EVENT_MARKER: True})
        assert translator.need_translate(event) is True

    def test_false_when_no_custom_metadata(self):
        translator = ConcreteTranslator()
        event = self._make_event(custom_metadata=None)
        assert translator.need_translate(event) is False

    def test_false_when_marker_not_in_metadata(self):
        translator = ConcreteTranslator()
        event = self._make_event(custom_metadata={"other_key": True})
        assert translator.need_translate(event) is False

    def test_false_when_metadata_is_empty_dict(self):
        translator = ConcreteTranslator()
        event = self._make_event(custom_metadata={})
        assert translator.need_translate(event) is False

    def test_true_marker_with_extra_keys(self):
        translator = ConcreteTranslator()
        event = self._make_event(
            custom_metadata={TRPC_EVENT_MARKER: True, "extra": "data"}
        )
        assert translator.need_translate(event) is True
