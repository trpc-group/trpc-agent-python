# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for :mod:`trpc_agent_sdk.tools._webfetch_tool`.

Covers the public tool surface area with emphasis on the Tavily Extract
provider path:

- Constructor validation (provider / extract depth)
- ``_get_declaration`` parameter shape
- Input validation (missing / invalid URL, domain policy)
- Direct HTTP fetch happy path (HTML → Markdown)
- Tavily Extract path (payload, credentials, success / failure, SSRF)
- Cache hit marking and per-call ``max_length`` truncation
"""

from __future__ import annotations

# Ensure ``pydantic.root_model`` is registered before MCP/google-genai import
# chains run; otherwise collection can hit KeyError: 'pydantic.root_model'.
import pydantic.root_model  # noqa: F401

import json
from typing import Any
from typing import Dict
from typing import Optional
from unittest.mock import MagicMock

import httpx
import pytest

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools._webfetch_tool import FetchResult
from trpc_agent_sdk.tools._webfetch_tool import WebFetchTool
from trpc_agent_sdk.tools._webfetch_tool import _cache_key
from trpc_agent_sdk.tools._webfetch_tool import _is_blocked_url
from trpc_agent_sdk.tools._webfetch_tool import _normalise_content_type
from trpc_agent_sdk.tools._webfetch_tool import _truncate
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Type

_TOOL_NAME = "webfetch"


def _make_mock_client(
    *,
    get_responses: Optional[Dict[str, tuple[int, bytes, Dict[str, str]]]] = None,
    post_json: Optional[Dict[str, Dict[str, Any]]] = None,
    post_status: Optional[Dict[str, int]] = None,
) -> httpx.AsyncClient:
    """Build an ``httpx.AsyncClient`` backed by ``MockTransport``.

    ``get_responses`` maps request URL path to ``(status, body, headers)``.
    ``post_json`` maps request URL path to a JSON body for POST responses.
    ``post_status`` optionally overrides the HTTP status for a POST path.
    """
    captured: Dict[str, Any] = {"last_request": None, "all_requests": []}
    get_responses = get_responses or {}
    post_json = post_json or {}
    post_status = post_status or {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["last_request"] = request
        captured["all_requests"].append(request)
        path = request.url.path
        if request.method == "POST":
            body = post_json.get(path)
            if body is None and path not in post_status:
                return httpx.Response(404, json={"error": "no mock for POST path"})
            return httpx.Response(
                post_status.get(path, 200),
                json=body if body is not None else {"error": "mock error"},
            )
        status, body, headers = get_responses.get(path, (404, b"not found", {"content-type": "text/plain"}))
        return httpx.Response(status, content=body, headers=headers)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client._captured = captured  # type: ignore[attr-defined]
    return client


def _tool_ctx() -> InvocationContext:
    return MagicMock(spec=InvocationContext)


class TestFetchResultSchema:

    def test_defaults(self):
        result = FetchResult()
        assert result.url == ""
        assert result.status_code == 0
        assert result.content == ""
        assert result.cached is False
        assert result.error == ""

    def test_model_dump_is_json_serialisable(self):
        result = FetchResult(url="https://example.com", status_code=200, content="hi")
        parsed = json.loads(json.dumps(result.model_dump()))
        assert parsed["url"] == "https://example.com"
        assert parsed["content"] == "hi"


class TestWebFetchToolInit:

    def test_default_provider_is_direct(self):
        tool = WebFetchTool()
        assert tool.name == _TOOL_NAME
        assert tool._provider == "direct"

    def test_invalid_provider_raises(self):
        with pytest.raises(ValueError, match="Unsupported web fetch provider"):
            WebFetchTool(provider="browser")  # type: ignore[arg-type]

    def test_invalid_tavily_extract_depth_raises(self):
        with pytest.raises(ValueError, match="tavily_extract_depth"):
            WebFetchTool(provider="tavily", api_key="k", tavily_extract_depth="deep")  # type: ignore[arg-type]

    def test_tavily_without_creds_warns_not_raises(self, monkeypatch):
        # Empty ``api_key`` falls back to ``TAVILY_API_KEY``; clear it so the
        # missing-credentials path is exercised even when CI exports the var.
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        tool = WebFetchTool(provider="tavily", api_key="")
        assert tool._provider == "tavily"
        assert tool._api_key == ""

    def test_tavily_reads_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "env-tavily-key")
        tool = WebFetchTool(provider="tavily")
        assert tool._api_key == "env-tavily-key"


class TestGetDeclaration:

    def test_declaration_shape(self):
        decl = WebFetchTool()._get_declaration()
        assert isinstance(decl, FunctionDeclaration)
        assert decl.name == _TOOL_NAME
        props = decl.parameters.properties
        assert set(props.keys()) == {"url", "max_length"}
        assert decl.parameters.required == ["url"]
        assert props["url"].type == Type.STRING
        assert props["max_length"].type == Type.INTEGER


class TestInputValidation:

    @pytest.mark.asyncio
    async def test_missing_url_errors(self):
        res = await WebFetchTool()._run_async_impl(tool_context=_tool_ctx(), args={})
        assert "INVALID_ARGS" in res["error"]

    @pytest.mark.asyncio
    async def test_relative_url_errors(self):
        res = await WebFetchTool()._run_async_impl(
            tool_context=_tool_ctx(),
            args={"url": "/relative/path"},
        )
        assert "INVALID_URL" in res["error"]

    @pytest.mark.asyncio
    async def test_blocked_by_allowed_domains(self):
        tool = WebFetchTool(allowed_domains=["python.org"])
        res = await tool._run_async_impl(
            tool_context=_tool_ctx(),
            args={"url": "https://example.com"},
        )
        assert "BLOCKED_URL" in res["error"]
        assert res["url"] == "https://example.com"


class TestDirectProvider:

    @pytest.mark.asyncio
    async def test_html_is_converted_to_markdown(self):
        html = b"<html><body><h1>Hello</h1><p>World</p><script>evil()</script></body></html>"
        client = _make_mock_client(get_responses={
            "/": (200, html, {
                "content-type": "text/html; charset=utf-8"
            }),
        })
        tool = WebFetchTool(
            http_client=client,
            block_private_network=False,
            max_content_length=10_000,
        )
        res = await tool._run_async_impl(
            tool_context=_tool_ctx(),
            args={"url": "https://example.com/"},
        )
        assert res["error"] == ""
        assert res["status_code"] == 200
        assert res["content_type"] == "text/html"
        assert "# Hello" in res["content"]
        assert "World" in res["content"]
        assert "evil()" not in res["content"]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_max_length_truncates_content(self):
        client = _make_mock_client(get_responses={
            "/": (200, b"ABCDEFGHIJKLMNOPQRSTUVWXYZ", {
                "content-type": "text/plain"
            }),
        })
        tool = WebFetchTool(
            http_client=client,
            block_private_network=False,
            max_content_length=100,
        )
        res = await tool._run_async_impl(
            tool_context=_tool_ctx(),
            args={
                "url": "https://example.com/",
                "max_length": 10,
            },
        )
        assert res["content"].endswith("...")
        assert len(res["content"]) <= 13
        await client.aclose()

    @pytest.mark.asyncio
    async def test_cache_hit_marks_cached_true(self):
        client = _make_mock_client(get_responses={
            "/": (200, b"cached body", {
                "content-type": "text/plain"
            }),
        })
        tool = WebFetchTool(
            http_client=client,
            block_private_network=False,
            enable_cache=True,
            cache_ttl_seconds=60.0,
            cache_max_bytes=1024 * 1024,
        )
        first = await tool._run_async_impl(
            tool_context=_tool_ctx(),
            args={"url": "https://example.com/"},
        )
        second = await tool._run_async_impl(
            tool_context=_tool_ctx(),
            args={"url": "https://www.example.com/"},
        )
        assert first["cached"] is False
        assert second["cached"] is True
        assert second["content"] == first["content"]
        assert len(client._captured["all_requests"]) == 1
        await client.aclose()


_TAVILY_EXTRACT_RESPONSE: Dict[str, Any] = {
    "results": [{
        "url": "https://docs.python.org/3/whatsnew/3.13.html",
        "raw_content": "# What's New In Python 3.13\n\nFree-threaded CPython.",
    }],
}


class TestTavilyProvider:

    @pytest.mark.asyncio
    async def test_missing_credentials_returns_structured_error(self, monkeypatch):
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        tool = WebFetchTool(provider="tavily", api_key="")
        res = await tool._run_async_impl(
            tool_context=_tool_ctx(),
            args={"url": "https://example.com"},
        )
        assert "TAVILY_NOT_CONFIGURED" in res["error"]

    @pytest.mark.asyncio
    async def test_happy_path_posts_extract_payload(self):
        client = _make_mock_client(post_json={"/extract": _TAVILY_EXTRACT_RESPONSE})
        tool = WebFetchTool(
            provider="tavily",
            api_key="tvly-test",
            http_client=client,
            base_url="https://api.tavily.com/extract",
            block_private_network=False,
            tavily_extract_depth="advanced",
            tavily_extra_params={"include_images": True},
        )
        res = await tool._run_async_impl(
            tool_context=_tool_ctx(),
            args={"url": "https://docs.python.org/3/whatsnew/3.13.html"},
        )
        assert res["error"] == ""
        assert res["status_code"] == 200
        assert res["content_type"] == "text/markdown"
        assert res["url"] == "https://docs.python.org/3/whatsnew/3.13.html"
        assert "Free-threaded CPython" in res["content"]

        req = client._captured["last_request"]
        assert req.method == "POST"
        assert req.headers.get("authorization") == "Bearer tvly-test"
        payload = json.loads(req.content)
        assert payload["urls"] == ["https://docs.python.org/3/whatsnew/3.13.html"]
        assert payload["extract_depth"] == "advanced"
        assert payload["include_images"] is True
        await client.aclose()

    @pytest.mark.asyncio
    async def test_failed_extract_returns_structured_error(self):
        client = _make_mock_client(
            post_json={"/extract": {
                "results": [],
                "failed_results": ["timeout while extracting"],
            }})
        tool = WebFetchTool(
            provider="tavily",
            api_key="tvly-test",
            http_client=client,
            base_url="https://api.tavily.com/extract",
            block_private_network=False,
        )
        res = await tool._run_async_impl(
            tool_context=_tool_ctx(),
            args={"url": "https://example.com/missing"},
        )
        assert "TAVILY_EXTRACT_ERROR" in res["error"]
        assert "timeout" in res["error"]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_http_status_error_maps_to_http_error(self):
        client = _make_mock_client(
            post_json={"/extract": {
                "error": "unauthorized"
            }},
            post_status={"/extract": 401},
        )
        tool = WebFetchTool(
            provider="tavily",
            api_key="tvly-test",
            http_client=client,
            base_url="https://api.tavily.com/extract",
            block_private_network=False,
        )
        res = await tool._run_async_impl(
            tool_context=_tool_ctx(),
            args={"url": "https://example.com/page"},
        )
        assert "HTTP_ERROR" in res["error"]
        assert "401" in res["error"]
        assert res["status_code"] == 0
        await client.aclose()

    @pytest.mark.asyncio
    async def test_ssrf_guard_blocks_private_targets_before_tavily(self):
        client = _make_mock_client(post_json={"/extract": _TAVILY_EXTRACT_RESPONSE})
        tool = WebFetchTool(
            provider="tavily",
            api_key="tvly-test",
            http_client=client,
            base_url="https://api.tavily.com/extract",
            block_private_network=True,
        )
        res = await tool._run_async_impl(
            tool_context=_tool_ctx(),
            args={"url": "http://127.0.0.1:8080/secret"},
        )
        assert "SSRF_BLOCKED_URL" in res["error"]
        assert client._captured["all_requests"] == []
        await client.aclose()

    @pytest.mark.asyncio
    async def test_content_fallback_uses_content_field(self):
        client = _make_mock_client(post_json={
            "/extract": {
                "results": [{
                    "url": "https://example.com/page",
                    "content": "plain content fallback",
                }],
            }
        })
        tool = WebFetchTool(
            provider="tavily",
            api_key="tvly-test",
            http_client=client,
            base_url="https://api.tavily.com/extract",
            block_private_network=False,
        )
        res = await tool._run_async_impl(
            tool_context=_tool_ctx(),
            args={"url": "https://example.com/page"},
        )
        assert res["content"] == "plain content fallback"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_max_length_truncates_tavily_content(self):
        client = _make_mock_client(post_json={
            "/extract": {
                "results": [{
                    "url": "https://example.com/page",
                    "raw_content": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                }],
            }
        })
        tool = WebFetchTool(
            provider="tavily",
            api_key="tvly-test",
            http_client=client,
            base_url="https://api.tavily.com/extract",
            block_private_network=False,
        )
        res = await tool._run_async_impl(
            tool_context=_tool_ctx(),
            args={
                "url": "https://example.com/page",
                "max_length": 8,
            },
        )
        assert res["content"].endswith("...")
        assert len(res["content"]) <= 11
        await client.aclose()


class TestHelpers:

    def test_truncate(self):
        assert _truncate("abc", 10) == "abc"
        assert _truncate("x" * 20, 10) == ("x" * 10) + "..."
        assert _truncate("keep-all", 0) == "keep-all"

    def test_normalise_content_type(self):
        assert _normalise_content_type("text/html; charset=utf-8") == "text/html"
        assert _normalise_content_type("") == ""

    def test_cache_key_normalises_variants(self):
        assert _cache_key("https://www.Example.com/path/") == _cache_key("https://example.com/path")

    def test_is_blocked_url(self):
        assert _is_blocked_url("https://docs.python.org/x", None, ["python.org"])
        assert not _is_blocked_url("https://python.org/x", ["python.org"], None)
        assert _is_blocked_url("https://example.com/x", ["python.org"], None)
