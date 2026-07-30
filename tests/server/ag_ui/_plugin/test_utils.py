# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for event_generator utility."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from trpc_agent_sdk.server.ag_ui._plugin._utils import event_generator


def _mock_request(*, disconnected=False):
    request = Mock()
    request.is_disconnected = AsyncMock(return_value=disconnected)
    return request


def _mock_agent(events=None, *, raise_on_run=None):
    agent = AsyncMock()
    agent.get_app_name = Mock(return_value="test_app")
    agent.get_user_id = Mock(return_value="user_1")
    agent.cancel_run = AsyncMock()

    if raise_on_run is not None:

        async def _run_raises(*args, **kwargs):
            raise raise_on_run
            yield  # noqa: unreachable — makes this an async generator

        agent.run = _run_raises
    elif events is not None:

        async def _run(*args, **kwargs):
            for e in events:
                yield e

        agent.run = _run
    else:

        async def _run_empty(*args, **kwargs):
            return
            yield  # noqa: unreachable — makes this an async generator

        agent.run = _run_empty

    return agent


def _mock_input(thread_id="thread_1"):
    inp = Mock()
    inp.thread_id = thread_id
    return inp


def _mock_encoder(*, fail_on_ids=None, fail_error_encode=False):
    """Create a mock encoder.

    Args:
        fail_on_ids: Set of object ids whose encoding should fail.
        fail_error_encode: If True, encoding the RunErrorEvent also fails.
    """
    encoder = Mock()
    fail_ids = fail_on_ids or set()

    def _encode(event):
        if id(event) in fail_ids:
            raise ValueError("encode boom")
        if fail_error_encode and hasattr(event, "code"):
            raise ValueError("error encode boom")
        return f"encoded:{id(event)}"

    encoder.encode = Mock(side_effect=_encode)
    return encoder


async def _collect(async_gen):
    items = []
    async for item in async_gen:
        items.append(item)
    return items


class TestEventGeneratorNormalFlow:
    async def test_yields_encoded_events(self):
        evt1, evt2 = object(), object()
        agent = _mock_agent(events=[evt1, evt2])
        request = _mock_request()
        inp = _mock_input()
        encoder = _mock_encoder()

        results = await _collect(event_generator(request, agent, inp, encoder))

        assert len(results) == 2
        assert results[0] == f"encoded:{id(evt1)}"
        assert results[1] == f"encoded:{id(evt2)}"

    async def test_calls_cancel_run_in_finally(self):
        agent = _mock_agent(events=[])
        request = _mock_request()
        inp = _mock_input(thread_id="t42")

        await _collect(event_generator(request, agent, inp, _mock_encoder()))

        agent.cancel_run.assert_awaited_once_with(
            session_id="t42", app_name="test_app", user_id="user_1"
        )


class TestEventGeneratorClientDisconnect:
    async def test_breaks_on_disconnect(self):
        evt = object()
        agent = _mock_agent(events=[evt, object()])
        request = Mock()
        call_count = 0

        async def _is_disconnected():
            nonlocal call_count
            call_count += 1
            return call_count > 1

        request.is_disconnected = _is_disconnected
        inp = _mock_input()

        results = await _collect(event_generator(request, agent, inp, _mock_encoder()))

        assert len(results) == 1
        agent.cancel_run.assert_awaited_once()


class TestEventGeneratorEncodingError:
    async def test_recoverable_encoding_error_yields_encoded_error_event(self):
        bad_evt = object()
        agent = _mock_agent(events=[bad_evt])
        request = _mock_request()
        inp = _mock_input()
        encoder = _mock_encoder(fail_on_ids={id(bad_evt)})

        results = await _collect(event_generator(request, agent, inp, encoder))

        assert len(results) == 1
        assert "encoded:" in results[0]
        agent.cancel_run.assert_awaited_once()

    async def test_non_recoverable_encoding_error_yields_fallback_sse(self):
        bad_evt = object()
        agent = _mock_agent(events=[bad_evt])
        request = _mock_request()
        inp = _mock_input()
        encoder = _mock_encoder(fail_on_ids={id(bad_evt)}, fail_error_encode=True)

        results = await _collect(event_generator(request, agent, inp, encoder))

        assert len(results) == 1
        assert "error" in results[0]
        agent.cancel_run.assert_awaited_once()

    async def test_encoding_error_stops_stream(self):
        bad_evt = object()
        good_evt = object()
        agent = _mock_agent(events=[bad_evt, good_evt])
        request = _mock_request()
        inp = _mock_input()
        encoder = _mock_encoder(fail_on_ids={id(bad_evt)})

        results = await _collect(event_generator(request, agent, inp, encoder))

        assert len(results) == 1
        agent.cancel_run.assert_awaited_once()


class TestEventGeneratorAgentError:
    async def test_agent_error_yields_encoded_error(self):
        agent = _mock_agent(raise_on_run=RuntimeError("agent boom"))
        request = _mock_request()
        inp = _mock_input()
        encoder = Mock()
        encoder.encode = Mock(return_value="encoded_error")

        results = await _collect(event_generator(request, agent, inp, encoder))

        assert len(results) == 1
        assert results[0] == "encoded_error"
        agent.cancel_run.assert_awaited_once()

    async def test_agent_error_with_non_encodable_error(self):
        agent = _mock_agent(raise_on_run=RuntimeError("agent boom"))
        request = _mock_request()
        inp = _mock_input()
        encoder = Mock()
        encoder.encode = Mock(side_effect=Exception("encode failed"))

        results = await _collect(event_generator(request, agent, inp, encoder))

        assert len(results) == 1
        assert "error" in results[0]
        agent.cancel_run.assert_awaited_once()


class TestEventGeneratorCancelledError:
    async def test_cancelled_error_is_reraised(self):
        agent = _mock_agent(raise_on_run=asyncio.CancelledError())
        request = _mock_request()
        inp = _mock_input()

        with pytest.raises(asyncio.CancelledError):
            await _collect(event_generator(request, agent, inp, _mock_encoder()))

        agent.cancel_run.assert_awaited_once()


class TestEventGeneratorFinallyBlock:
    async def test_cancel_run_called_on_normal_completion(self):
        agent = _mock_agent(events=[object()])
        request = _mock_request()
        inp = _mock_input(thread_id="sess_1")

        await _collect(event_generator(request, agent, inp, _mock_encoder()))

        agent.cancel_run.assert_awaited_once_with(
            session_id="sess_1", app_name="test_app", user_id="user_1"
        )

    async def test_cancel_run_called_on_error(self):
        agent = _mock_agent(raise_on_run=RuntimeError("fail"))
        request = _mock_request()
        inp = _mock_input(thread_id="sess_2")
        encoder = Mock()
        encoder.encode = Mock(return_value="err")

        await _collect(event_generator(request, agent, inp, encoder))

        agent.cancel_run.assert_awaited_once_with(
            session_id="sess_2", app_name="test_app", user_id="user_1"
        )
