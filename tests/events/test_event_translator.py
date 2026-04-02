# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for EventTranslatorBase."""

from __future__ import annotations

from typing import AsyncGenerator

import pytest

from trpc_agent_sdk.events._event import Event
from trpc_agent_sdk.events._event_translator import EventTranslatorBase
from trpc_agent_sdk.types import Content, Part


# ---------------------------------------------------------------------------
# Concrete implementations for testing
# ---------------------------------------------------------------------------


class DummyProtocolEvent:
    """A simple protocol event for testing."""

    def __init__(self, text: str):
        self.text = text


class DummyContext:
    """A simple context for testing."""

    def __init__(self, prefix: str = ""):
        self.prefix = prefix


class AcceptAllTranslator(EventTranslatorBase[DummyProtocolEvent, DummyContext]):
    """Translator that accepts all events."""

    def need_translate(self, event: Event) -> bool:
        return True

    async def translate(
        self,
        event: Event,
        context: DummyContext,
    ) -> AsyncGenerator[DummyProtocolEvent, None]:
        text = event.get_text()
        yield DummyProtocolEvent(text=f"{context.prefix}{text}")


class TextOnlyTranslator(EventTranslatorBase[DummyProtocolEvent, DummyContext]):
    """Translator that only accepts events with text content."""

    def need_translate(self, event: Event) -> bool:
        return bool(event.content and event.content.parts and event.get_text())

    async def translate(
        self,
        event: Event,
        context: DummyContext,
    ) -> AsyncGenerator[DummyProtocolEvent, None]:
        yield DummyProtocolEvent(text=event.get_text())


class MultiYieldTranslator(EventTranslatorBase[DummyProtocolEvent, DummyContext]):
    """Translator that yields multiple protocol events per input event."""

    def need_translate(self, event: Event) -> bool:
        return True

    async def translate(
        self,
        event: Event,
        context: DummyContext,
    ) -> AsyncGenerator[DummyProtocolEvent, None]:
        yield DummyProtocolEvent(text="start")
        yield DummyProtocolEvent(text=event.get_text())
        yield DummyProtocolEvent(text="end")


class EmptyTranslator(EventTranslatorBase[DummyProtocolEvent, DummyContext]):
    """Translator that yields nothing."""

    def need_translate(self, event: Event) -> bool:
        return True

    async def translate(
        self,
        event: Event,
        context: DummyContext,
    ) -> AsyncGenerator[DummyProtocolEvent, None]:
        return
        yield


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _make_text_event(text: str) -> Event:
    return Event(
        invocation_id="inv-1",
        author="agent",
        content=Content(parts=[Part(text=text)]),
    )


def _make_empty_event() -> Event:
    return Event(invocation_id="inv-1", author="agent", content=None)


class TestEventTranslatorBaseIsAbstract:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            EventTranslatorBase()


class TestAcceptAllTranslator:
    def test_need_translate_always_true(self):
        translator = AcceptAllTranslator()
        assert translator.need_translate(_make_text_event("hi")) is True
        assert translator.need_translate(_make_empty_event()) is True

    @pytest.mark.asyncio
    async def test_translate_with_context(self):
        translator = AcceptAllTranslator()
        ctx = DummyContext(prefix="[OUT] ")
        results = []
        async for proto_event in translator.translate(_make_text_event("hello"), ctx):
            results.append(proto_event)
        assert len(results) == 1
        assert results[0].text == "[OUT] hello"


class TestTextOnlyTranslator:
    def test_need_translate_rejects_empty(self):
        translator = TextOnlyTranslator()
        assert translator.need_translate(_make_empty_event()) is False

    def test_need_translate_accepts_text(self):
        translator = TextOnlyTranslator()
        assert translator.need_translate(_make_text_event("data")) is True

    @pytest.mark.asyncio
    async def test_translate_extracts_text(self):
        translator = TextOnlyTranslator()
        results = []
        async for proto_event in translator.translate(_make_text_event("data"), DummyContext()):
            results.append(proto_event)
        assert results[0].text == "data"


class TestMultiYieldTranslator:
    @pytest.mark.asyncio
    async def test_yields_multiple_events(self):
        translator = MultiYieldTranslator()
        results = []
        async for proto_event in translator.translate(_make_text_event("body"), DummyContext()):
            results.append(proto_event)
        assert len(results) == 3
        assert results[0].text == "start"
        assert results[1].text == "body"
        assert results[2].text == "end"


class TestEmptyTranslator:
    @pytest.mark.asyncio
    async def test_yields_nothing(self):
        translator = EmptyTranslator()
        results = []
        async for proto_event in translator.translate(_make_text_event("x"), DummyContext()):
            results.append(proto_event)
        assert results == []


class TestTranslatorGenericTypes:
    def test_subclass_isinstance(self):
        translator = AcceptAllTranslator()
        assert isinstance(translator, EventTranslatorBase)

    def test_different_translators_are_independent(self):
        t1 = AcceptAllTranslator()
        t2 = TextOnlyTranslator()
        event = _make_empty_event()
        assert t1.need_translate(event) is True
        assert t2.need_translate(event) is False
