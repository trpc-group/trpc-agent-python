# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for _http_req module."""

from __future__ import annotations

from unittest.mock import Mock

from starlette.requests import Request
from starlette.testclient import TestClient

from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.server.ag_ui._core._http_req import (
    _AGUI_HTTP_REQ_KEY,
    get_agui_http_req,
    set_agui_http_req,
)


def _make_request() -> Request:
    scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
    return Request(scope)


def _make_context(run_config=None) -> Mock:
    ctx = Mock(spec=InvocationContext)
    ctx.run_config = run_config
    return ctx


class TestSetAguiHttpReq:
    def test_stores_request_in_run_config(self):
        config = RunConfig()
        request = _make_request()
        set_agui_http_req(config, request)
        assert config.agent_run_config[_AGUI_HTTP_REQ_KEY] is request

    def test_overwrites_previous_request(self):
        config = RunConfig()
        req1 = _make_request()
        req2 = _make_request()
        set_agui_http_req(config, req1)
        set_agui_http_req(config, req2)
        assert config.agent_run_config[_AGUI_HTTP_REQ_KEY] is req2


class TestGetAguiHttpReq:
    def test_returns_request(self):
        config = RunConfig()
        request = _make_request()
        set_agui_http_req(config, request)
        ctx = _make_context(run_config=config)
        assert get_agui_http_req(ctx) is request

    def test_returns_none_when_no_run_config(self):
        ctx = _make_context(run_config=None)
        assert get_agui_http_req(ctx) is None

    def test_returns_none_when_value_not_request(self):
        config = RunConfig()
        config.agent_run_config[_AGUI_HTTP_REQ_KEY] = "not a request"
        ctx = _make_context(run_config=config)
        assert get_agui_http_req(ctx) is None

    def test_returns_none_when_key_missing(self):
        config = RunConfig()
        ctx = _make_context(run_config=config)
        assert get_agui_http_req(ctx) is None
