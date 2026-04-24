"""Unit tests for trpc_agent_sdk.server.openclaw.service._heart_service."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trpc_agent_sdk.server.openclaw.service._heart_service import ClawHeartbeatService


def _make_mock_response(parts=None):
    """Build a mock LLM response with optional parts."""
    resp = MagicMock()
    if parts is None:
        resp.content = None
    else:
        resp.content = MagicMock()
        resp.content.parts = parts
    return resp


def _make_function_call_part(name="heartbeat", args=None):
    """Build a mock Part with a function_call attribute."""
    part = MagicMock()
    fc = MagicMock()
    fc.name = name
    fc.args = args or {}
    part.function_call = fc
    return part


def _make_text_part(text="some text"):
    part = MagicMock()
    part.function_call = None
    part.text = text
    return part


class TestClawHeartbeatServiceDecide:

    def _make_service(self, responses):
        """Create a service with a mocked LLM provider."""
        provider = MagicMock()

        async def mock_generate(request, stream=False):
            for r in responses:
                yield r

        provider.generate_async = mock_generate

        with patch(
            "trpc_agent_sdk.server.openclaw.service._heart_service.heartbeat_service_package.HeartbeatService.__init__",
            return_value=None,
        ):
            svc = ClawHeartbeatService.__new__(ClawHeartbeatService)
            svc.provider = provider
            svc.model = "test-model"
        return svc

    async def test_no_response_returns_skip(self):
        svc = self._make_service(responses=[])
        action, tasks = await svc._decide("some content")
        assert action == "skip"
        assert tasks == ""

    async def test_none_content_returns_skip(self):
        resp = _make_mock_response(parts=None)
        svc = self._make_service(responses=[resp])
        action, tasks = await svc._decide("content")
        assert action == "skip"
        assert tasks == ""

    async def test_function_call_run(self):
        fc_part = _make_function_call_part(
            name="heartbeat",
            args={"action": "run", "tasks": "Deploy update"},
        )
        resp = _make_mock_response(parts=[fc_part])
        svc = self._make_service(responses=[resp])

        action, tasks = await svc._decide("content")
        assert action == "run"
        assert tasks == "Deploy update"

    async def test_function_call_skip(self):
        fc_part = _make_function_call_part(
            name="heartbeat",
            args={"action": "skip"},
        )
        resp = _make_mock_response(parts=[fc_part])
        svc = self._make_service(responses=[resp])

        action, tasks = await svc._decide("content")
        assert action == "skip"
        assert tasks == ""

    async def test_no_function_call_returns_skip(self):
        text_part = _make_text_part("just text")
        resp = _make_mock_response(parts=[text_part])
        svc = self._make_service(responses=[resp])

        action, tasks = await svc._decide("content")
        assert action == "skip"
        assert tasks == ""

    async def test_wrong_function_name_returns_skip(self):
        fc_part = _make_function_call_part(name="other_func", args={"action": "run"})
        resp = _make_mock_response(parts=[fc_part])
        svc = self._make_service(responses=[resp])

        action, tasks = await svc._decide("content")
        assert action == "skip"
        assert tasks == ""

    async def test_function_call_no_args(self):
        fc_part = _make_function_call_part(name="heartbeat", args=None)
        resp = _make_mock_response(parts=[fc_part])
        svc = self._make_service(responses=[resp])

        action, tasks = await svc._decide("content")
        assert action == "skip"
        assert tasks == ""

    async def test_response_is_none_object(self):
        resp = MagicMock()
        resp.content = None
        svc = self._make_service(responses=[resp])

        action, tasks = await svc._decide("content")
        assert action == "skip"

    async def test_multiple_parts_finds_heartbeat(self):
        text_part = _make_text_part("ignored")
        fc_part = _make_function_call_part(
            name="heartbeat",
            args={"action": "run", "tasks": "Do things"},
        )
        resp = _make_mock_response(parts=[text_part, fc_part])
        svc = self._make_service(responses=[resp])

        action, tasks = await svc._decide("content")
        assert action == "run"
        assert tasks == "Do things"
