# -*- coding: utf-8 -*-
"""Unit tests for add_trpc_fastapi_endpoint and create_trpc_app."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trpc_agent_sdk.server.ag_ui._core._endpoint import (
    add_trpc_fastapi_endpoint,
    create_trpc_app,
)


_VALID_PAYLOAD = {
    "threadId": "t1",
    "runId": "r1",
    "state": {},
    "messages": [],
    "tools": [],
    "context": [],
    "forwardedProps": {},
}


def _mock_agent(events=None, *, raise_on_run=None):
    agent = AsyncMock()
    if raise_on_run is not None:

        async def _run_raises(*args, **kwargs):
            raise raise_on_run

        agent.run = _run_raises
    elif events is not None:

        async def _run(*args, **kwargs):
            for e in events:
                yield e

        agent.run = _run
    else:

        async def _run_empty(*args, **kwargs):
            return
            yield  # noqa: unreachable

        agent.run = _run_empty
    return agent


class TestAddTrpcFastapiEndpoint:
    def test_adds_post_route(self):
        app = FastAPI()
        agent = _mock_agent()
        add_trpc_fastapi_endpoint(app, agent, "/my-agent")

        routes = [r for r in app.routes if hasattr(r, "path") and r.path == "/my-agent"]
        assert len(routes) == 1
        assert "POST" in routes[0].methods

    def test_default_path_is_root(self):
        app = FastAPI()
        agent = _mock_agent()
        add_trpc_fastapi_endpoint(app, agent)

        routes = [r for r in app.routes if hasattr(r, "path") and r.path == "/"]
        assert len(routes) == 1

    def test_endpoint_returns_streaming_response(self):
        agent = _mock_agent(events=[])
        app = FastAPI()
        add_trpc_fastapi_endpoint(app, agent, "/agent")

        with patch(
            "trpc_agent_sdk.server.ag_ui._core._endpoint.EventEncoder"
        ) as MockEncoder:
            encoder_instance = MockEncoder.return_value
            encoder_instance.get_content_type.return_value = "text/event-stream"
            encoder_instance.encode = Mock(return_value="data: test\n\n")

            client = TestClient(app)
            response = client.post("/agent", json=_VALID_PAYLOAD)
            assert response.status_code == 200

    def test_endpoint_handles_agent_error(self):
        agent = _mock_agent(raise_on_run=RuntimeError("boom"))
        app = FastAPI()
        add_trpc_fastapi_endpoint(app, agent, "/err")

        with patch(
            "trpc_agent_sdk.server.ag_ui._core._endpoint.EventEncoder"
        ) as MockEncoder:
            encoder_instance = MockEncoder.return_value
            encoder_instance.get_content_type.return_value = "text/event-stream"
            encoder_instance.encode = Mock(return_value="data: error\n\n")

            client = TestClient(app)
            response = client.post("/err", json=_VALID_PAYLOAD)
            assert response.status_code == 200
            assert len(response.text) > 0

    def test_endpoint_encoding_error_fallback(self):
        evt = Mock()
        agent = _mock_agent(events=[evt])
        app = FastAPI()
        add_trpc_fastapi_endpoint(app, agent, "/enc-err")

        with patch(
            "trpc_agent_sdk.server.ag_ui._core._endpoint.EventEncoder"
        ) as MockEncoder:
            encoder_instance = MockEncoder.return_value
            encoder_instance.get_content_type.return_value = "text/event-stream"
            encoder_instance.encode = Mock(side_effect=Exception("encode fail"))

            client = TestClient(app)
            response = client.post("/enc-err", json=_VALID_PAYLOAD)
            assert response.status_code == 200
            assert "Event encoding failed" in response.text


class TestCreateTrpcApp:
    def test_returns_fastapi_app(self):
        agent = _mock_agent()
        app = create_trpc_app(agent)

        assert isinstance(app, FastAPI)
        assert app.title == "TRPC Agent Middleware for AG-UI Protocol"

    def test_app_has_route_at_default_path(self):
        agent = _mock_agent()
        app = create_trpc_app(agent)

        routes = [r for r in app.routes if hasattr(r, "path") and r.path == "/"]
        assert len(routes) == 1

    def test_app_has_route_at_custom_path(self):
        agent = _mock_agent()
        app = create_trpc_app(agent, path="/custom")

        routes = [r for r in app.routes if hasattr(r, "path") and r.path == "/custom"]
        assert len(routes) == 1

    def test_endpoint_encoding_error_and_error_event_also_fails(self):
        evt = Mock()
        agent = _mock_agent(events=[evt])
        app = FastAPI()
        add_trpc_fastapi_endpoint(app, agent, "/double-err")

        call_count = 0

        def failing_encode(event):
            nonlocal call_count
            call_count += 1
            raise Exception("encode fail")

        with patch(
            "trpc_agent_sdk.server.ag_ui._core._endpoint.EventEncoder"
        ) as MockEncoder:
            encoder_instance = MockEncoder.return_value
            encoder_instance.get_content_type.return_value = "text/event-stream"
            encoder_instance.encode = failing_encode

            client = TestClient(app)
            response = client.post("/double-err", json=_VALID_PAYLOAD)
            assert response.status_code == 200
            assert "Event encoding failed" in response.text

    def test_endpoint_agent_error_encode_also_fails(self):
        agent = _mock_agent(raise_on_run=RuntimeError("agent crash"))
        app = FastAPI()
        add_trpc_fastapi_endpoint(app, agent, "/agent-err2")

        with patch(
            "trpc_agent_sdk.server.ag_ui._core._endpoint.EventEncoder"
        ) as MockEncoder:
            encoder_instance = MockEncoder.return_value
            encoder_instance.get_content_type.return_value = "text/event-stream"
            encoder_instance.encode = Mock(side_effect=Exception("encode fail"))

            client = TestClient(app)
            response = client.post("/agent-err2", json=_VALID_PAYLOAD)
            assert response.status_code == 200
            assert "Agent execution failed" in response.text
