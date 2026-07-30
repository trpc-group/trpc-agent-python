"""Unit tests for trpc_agent_sdk.server.openclaw.tools.web module."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.server.openclaw.tools.web import (
    WebFetchTool,
    WebSearchTool,
    _format_results,
    _normalize,
    _strip_tags,
    _validate_url,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_context() -> InvocationContext:
    ctx = MagicMock(spec=InvocationContext)
    ctx.agent_context = MagicMock()
    return ctx


def _mock_config(**overrides):
    cfg = MagicMock()
    cfg.provider = overrides.get("provider", "brave")
    cfg.api_key = overrides.get("api_key", "")
    cfg.base_url = overrides.get("base_url", "")
    cfg.max_results = overrides.get("max_results", 5)
    return cfg


# ---------------------------------------------------------------------------
# _strip_tags
# ---------------------------------------------------------------------------


class TestStripTags:

    def test_removes_script(self):
        assert _strip_tags("<script>alert(1)</script>hello") == "hello"

    def test_removes_style(self):
        assert _strip_tags("<style>body{}</style>text") == "text"

    def test_removes_html_tags(self):
        assert _strip_tags("<p>hello</p>") == "hello"

    def test_unescapes_entities(self):
        assert _strip_tags("&amp; &lt; &gt;") == "& < >"

    def test_strips_whitespace(self):
        assert _strip_tags("  hello  ") == "hello"

    def test_empty_string(self):
        assert _strip_tags("") == ""

    def test_nested_tags(self):
        assert _strip_tags("<div><span>text</span></div>") == "text"


# ---------------------------------------------------------------------------
# _normalize
# ---------------------------------------------------------------------------


class TestNormalize:

    def test_collapses_spaces(self):
        assert _normalize("hello     world") == "hello world"

    def test_collapses_tabs(self):
        assert _normalize("hello\t\tworld") == "hello world"

    def test_trims_excessive_newlines(self):
        result = _normalize("a\n\n\n\n\nb")
        assert result == "a\n\nb"

    def test_strips_leading_trailing(self):
        assert _normalize("  hello  ") == "hello"


# ---------------------------------------------------------------------------
# _validate_url
# ---------------------------------------------------------------------------


class TestValidateUrl:

    def test_valid_http(self):
        ok, msg = _validate_url("http://example.com")
        assert ok is True
        assert msg == ""

    def test_valid_https(self):
        ok, msg = _validate_url("https://example.com/path")
        assert ok is True

    def test_invalid_scheme_ftp(self):
        ok, msg = _validate_url("ftp://example.com")
        assert ok is False
        assert "ftp" in msg

    def test_no_scheme(self):
        ok, msg = _validate_url("example.com")
        assert ok is False
        assert "none" in msg

    def test_missing_domain(self):
        ok, msg = _validate_url("http://")
        assert ok is False
        assert "Missing domain" in msg

    def test_empty_string(self):
        ok, msg = _validate_url("")
        assert ok is False

    def test_exception_in_urlparse(self):
        with patch("trpc_agent_sdk.server.openclaw.tools.web.urlparse", side_effect=ValueError("parse error")):
            ok, msg = _validate_url("http://example.com")
        assert ok is False
        assert "parse error" in msg


# ---------------------------------------------------------------------------
# _format_results
# ---------------------------------------------------------------------------


class TestFormatResults:

    def test_empty_items(self):
        result = _format_results("test query", [], 5)
        assert "No results" in result
        assert "test query" in result

    def test_normal_formatting(self):
        items = [
            {"title": "Title1", "url": "https://a.com", "content": "Snippet1"},
            {"title": "Title2", "url": "https://b.com", "content": "Snippet2"},
        ]
        result = _format_results("query", items, 5)
        assert "1. Title1" in result
        assert "https://a.com" in result
        assert "Snippet1" in result
        assert "2. Title2" in result

    def test_respects_n_limit(self):
        items = [
            {"title": f"T{i}", "url": f"https://{i}.com", "content": ""} for i in range(10)
        ]
        result = _format_results("q", items, 3)
        assert "3." in result
        assert "4." not in result

    def test_strips_html_in_fields(self):
        items = [{"title": "<b>Bold</b>", "url": "https://x.com", "content": "<i>Italic</i>"}]
        result = _format_results("q", items, 5)
        assert "Bold" in result
        assert "<b>" not in result


# ---------------------------------------------------------------------------
# WebSearchTool
# ---------------------------------------------------------------------------


class TestWebSearchTool:

    def test_declaration(self):
        tool = WebSearchTool(config=_mock_config())
        decl = tool._get_declaration()
        assert decl.name == "web_search"
        assert "query" in decl.parameters.required

    async def test_unknown_provider(self):
        cfg = _mock_config(provider="unknown_engine")
        tool = WebSearchTool(config=cfg)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"query": "test"},
        )
        assert "unknown search provider" in result

    async def test_brave_provider_dispatch(self):
        cfg = _mock_config(provider="brave", api_key="key123")
        tool = WebSearchTool(config=cfg)
        ctx = _tool_context()
        with patch.object(tool, "_search_brave", new_callable=AsyncMock, return_value="brave results") as mock_brave:
            result = await tool._run_async_impl(tool_context=ctx, args={"query": "test"})
        mock_brave.assert_awaited_once()
        assert result == "brave results"

    async def test_duckduckgo_provider_dispatch(self):
        cfg = _mock_config(provider="duckduckgo")
        tool = WebSearchTool(config=cfg)
        ctx = _tool_context()
        with patch.object(tool, "_search_duckduckgo", new_callable=AsyncMock, return_value="ddg") as mock_ddg:
            result = await tool._run_async_impl(tool_context=ctx, args={"query": "test"})
        mock_ddg.assert_awaited_once()
        assert result == "ddg"

    async def test_tavily_provider_dispatch(self):
        cfg = _mock_config(provider="tavily", api_key="tkey")
        tool = WebSearchTool(config=cfg)
        ctx = _tool_context()
        with patch.object(tool, "_search_tavily", new_callable=AsyncMock, return_value="tavily") as mock_t:
            result = await tool._run_async_impl(tool_context=ctx, args={"query": "test"})
        mock_t.assert_awaited_once()
        assert result == "tavily"

    async def test_searxng_provider_dispatch(self):
        cfg = _mock_config(provider="searxng")
        tool = WebSearchTool(config=cfg)
        ctx = _tool_context()
        with patch.object(tool, "_search_searxng", new_callable=AsyncMock, return_value="sx") as mock_sx:
            result = await tool._run_async_impl(tool_context=ctx, args={"query": "test"})
        mock_sx.assert_awaited_once()
        assert result == "sx"

    async def test_jina_provider_dispatch(self):
        cfg = _mock_config(provider="jina", api_key="jkey")
        tool = WebSearchTool(config=cfg)
        ctx = _tool_context()
        with patch.object(tool, "_search_jina", new_callable=AsyncMock, return_value="jina") as mock_j:
            result = await tool._run_async_impl(tool_context=ctx, args={"query": "test"})
        mock_j.assert_awaited_once()
        assert result == "jina"

    async def test_count_clamped(self):
        cfg = _mock_config(provider="brave", api_key="key")
        tool = WebSearchTool(config=cfg)
        ctx = _tool_context()
        with patch.object(tool, "_search_brave", new_callable=AsyncMock, return_value="ok") as mock_brave:
            await tool._run_async_impl(tool_context=ctx, args={"query": "test", "count": 50})
        mock_brave.assert_awaited_once_with("test", 10)

    async def test_brave_no_api_key_falls_back(self):
        cfg = _mock_config(provider="brave", api_key="")
        tool = WebSearchTool(config=cfg)
        with patch.dict("os.environ", {}, clear=False):
            with patch.object(tool, "_search_duckduckgo", new_callable=AsyncMock, return_value="ddg fallback") as m:
                result = await tool._search_brave("query", 5)
        m.assert_awaited_once()
        assert result == "ddg fallback"

    async def test_duckduckgo_search_success(self):
        cfg = _mock_config(provider="duckduckgo")
        tool = WebSearchTool(config=cfg)
        mock_ddgs = MagicMock()
        mock_ddgs.text.return_value = [
            {"title": "T", "href": "https://x.com", "body": "B"},
        ]
        with patch("trpc_agent_sdk.server.openclaw.tools.web.DDGS", return_value=mock_ddgs, create=True):
            with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.return_value = [
                    {"title": "T", "href": "https://x.com", "body": "B"},
                ]
                result = await tool._search_duckduckgo("test", 5)
        assert "T" in result

    async def test_duckduckgo_no_results(self):
        cfg = _mock_config(provider="duckduckgo")
        tool = WebSearchTool(config=cfg)
        with patch("asyncio.to_thread", new_callable=AsyncMock, return_value=[]):
            with patch.dict("sys.modules", {"ddgs": MagicMock()}):
                result = await tool._search_duckduckgo("test", 5)
        assert "No results" in result

    async def test_duckduckgo_exception(self):
        cfg = _mock_config(provider="duckduckgo")
        tool = WebSearchTool(config=cfg)
        with patch.dict("sys.modules", {"ddgs": None}):
            result = await tool._search_duckduckgo("test", 5)
        assert "Error" in result

    async def test_tavily_no_api_key_falls_back(self):
        cfg = _mock_config(provider="tavily", api_key="")
        tool = WebSearchTool(config=cfg)
        with patch.dict("os.environ", {}, clear=False):
            with patch.object(tool, "_search_duckduckgo", new_callable=AsyncMock, return_value="fallback") as m:
                result = await tool._search_tavily("q", 5)
        m.assert_awaited_once()

    async def test_jina_no_api_key_falls_back(self):
        cfg = _mock_config(provider="jina", api_key="")
        tool = WebSearchTool(config=cfg)
        with patch.dict("os.environ", {}, clear=False):
            with patch.object(tool, "_search_duckduckgo", new_callable=AsyncMock, return_value="fallback") as m:
                result = await tool._search_jina("q", 5)
        m.assert_awaited_once()

    async def test_searxng_no_base_url_falls_back(self):
        cfg = _mock_config(provider="searxng", base_url="")
        tool = WebSearchTool(config=cfg)
        with patch.dict("os.environ", {}, clear=False):
            with patch.object(tool, "_search_duckduckgo", new_callable=AsyncMock, return_value="fallback") as m:
                result = await tool._search_searxng("q", 5)
        m.assert_awaited_once()

    async def test_brave_success(self):
        cfg = _mock_config(provider="brave", api_key="test-key")
        tool = WebSearchTool(config=cfg)

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "web": {"results": [{"title": "Brave Result", "url": "https://b.com", "description": "desc"}]}
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool._search_brave("test query", 5)
        assert "Brave Result" in result

    async def test_brave_exception(self):
        cfg = _mock_config(provider="brave", api_key="test-key")
        tool = WebSearchTool(config=cfg)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=RuntimeError("connection error"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool._search_brave("test", 5)
        assert "Error" in result

    async def test_tavily_success(self):
        cfg = _mock_config(provider="tavily", api_key="tkey")
        tool = WebSearchTool(config=cfg)

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "results": [{"title": "Tavily Hit", "url": "https://t.com", "content": "snippet"}]
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool._search_tavily("test query", 5)
        assert "Tavily Hit" in result

    async def test_tavily_exception(self):
        cfg = _mock_config(provider="tavily", api_key="tkey")
        tool = WebSearchTool(config=cfg)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=RuntimeError("fail"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool._search_tavily("test", 5)
        assert "Error" in result

    async def test_searxng_success(self):
        cfg = _mock_config(provider="searxng", base_url="https://searx.example.com")
        tool = WebSearchTool(config=cfg)

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "results": [{"title": "SX Result", "url": "https://sx.com", "content": "info"}]
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool._search_searxng("test", 5)
        assert "SX Result" in result

    async def test_searxng_invalid_url(self):
        cfg = _mock_config(provider="searxng", base_url="ftp://bad.searx.com")
        tool = WebSearchTool(config=cfg)
        result = await tool._search_searxng("test", 5)
        assert "invalid SearXNG URL" in result

    async def test_searxng_exception(self):
        cfg = _mock_config(provider="searxng", base_url="https://searx.example.com")
        tool = WebSearchTool(config=cfg)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=RuntimeError("timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool._search_searxng("test", 5)
        assert "Error" in result

    async def test_jina_search_success(self):
        cfg = _mock_config(provider="jina", api_key="jkey")
        tool = WebSearchTool(config=cfg)

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "data": [{"title": "Jina Hit", "url": "https://j.com", "content": "x" * 600}]
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool._search_jina("test", 5)
        assert "Jina Hit" in result

    async def test_jina_search_exception(self):
        cfg = _mock_config(provider="jina", api_key="jkey")
        tool = WebSearchTool(config=cfg)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=RuntimeError("jina down"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool._search_jina("test", 5)
        assert "Error" in result


# ---------------------------------------------------------------------------
# WebFetchTool
# ---------------------------------------------------------------------------


class TestWebFetchTool:

    def test_declaration(self):
        tool = WebFetchTool()
        decl = tool._get_declaration()
        assert decl.name == "web_fetch"
        assert "url" in decl.parameters.required

    async def test_invalid_url(self):
        tool = WebFetchTool()
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"url": "ftp://bad.com"},
        )
        data = json.loads(result)
        assert "error" in data

    async def test_jina_success(self):
        tool = WebFetchTool()
        ctx = _tool_context()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {"content": "Hello content", "title": "Title", "url": "https://example.com"}
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool._run_async_impl(
                tool_context=ctx,
                args={"url": "https://example.com"},
            )
        data = json.loads(result)
        assert data["extractor"] == "jina"
        assert "Title" in data["text"]

    async def test_jina_fallback_to_readability(self):
        tool = WebFetchTool()
        ctx = _tool_context()

        with patch.object(tool, "_fetch_jina", new_callable=AsyncMock, return_value=None):
            with patch.object(tool, "_fetch_readability", new_callable=AsyncMock, return_value='{"text":"ok"}'):
                result = await tool._run_async_impl(
                    tool_context=ctx,
                    args={"url": "https://example.com"},
                )
        assert "ok" in result

    async def test_jina_rate_limited_returns_none(self):
        tool = WebFetchTool()

        mock_response = MagicMock()
        mock_response.status_code = 429

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool._fetch_jina("https://example.com", 50000)
        assert result is None

    async def test_jina_exception_returns_none(self):
        tool = WebFetchTool()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("network error"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool._fetch_jina("https://example.com", 50000)
        assert result is None

    async def test_jina_empty_content_returns_none(self):
        tool = WebFetchTool()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"content": "", "url": "https://example.com"}}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool._fetch_jina("https://example.com", 50000)
        assert result is None

    async def test_jina_truncation(self):
        tool = WebFetchTool()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"content": "x" * 200, "url": "https://example.com"}}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool._fetch_jina("https://example.com", 50)
        data = json.loads(result)
        assert data["truncated"] is True

    async def test_jina_with_api_key_env(self):
        tool = WebFetchTool()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {"content": "Content", "title": "T", "url": "https://example.com"}
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict("os.environ", {"JINA_API_KEY": "env-key"}):
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await tool._fetch_jina("https://example.com", 50000)
        data = json.loads(result)
        assert data["extractor"] == "jina"

    async def test_readability_html_text_mode(self):
        tool = WebFetchTool()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://example.com"
        mock_response.headers = {"content-type": "text/html"}
        mock_response.text = "<html><body><p>Hello World</p></body></html>"
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_doc = MagicMock()
        mock_doc.summary.return_value = "<p>Plain text</p>"
        mock_doc.title.return_value = ""

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("readability.Document", return_value=mock_doc):
                result = await tool._fetch_readability("https://example.com", "text", 50000)
        data = json.loads(result)
        assert data["extractor"] == "readability"
        assert "Plain text" in data["text"]

    async def test_readability_html_starts_with_doctype(self):
        tool = WebFetchTool()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://example.com"
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.text = "<!doctype html><html><body>Hi</body></html>"
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_doc = MagicMock()
        mock_doc.summary.return_value = "<p>Doc content</p>"
        mock_doc.title.return_value = "Doc Title"

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("readability.Document", return_value=mock_doc):
                result = await tool._fetch_readability("https://example.com", "markdown", 50000)
        data = json.loads(result)
        assert data["extractor"] == "readability"

    async def test_readability_truncation(self):
        tool = WebFetchTool()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://example.com"
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.text = "x" * 200
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool._fetch_readability("https://example.com", "text", 50)
        data = json.loads(result)
        assert data["truncated"] is True
        assert data["extractor"] == "raw"

    async def test_readability_html(self):
        tool = WebFetchTool()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://example.com"
        mock_response.headers = {"content-type": "text/html"}
        mock_response.text = "<html><body><p>Hello World</p></body></html>"
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_doc = MagicMock()
        mock_doc.summary.return_value = "<p>Hello World</p>"
        mock_doc.title.return_value = "Page Title"

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("readability.Document", return_value=mock_doc):
                result = await tool._fetch_readability("https://example.com", "markdown", 50000)
        data = json.loads(result)
        assert data["extractor"] == "readability"
        assert "Page Title" in data["text"]

    async def test_readability_json_content(self):
        tool = WebFetchTool()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://api.example.com"
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"key": "value"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool._fetch_readability("https://api.example.com", "text", 50000)
        data = json.loads(result)
        assert data["extractor"] == "json"

    async def test_readability_raw_content(self):
        tool = WebFetchTool()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://example.com/data"
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.text = "plain text content"
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool._fetch_readability("https://example.com/data", "text", 50000)
        data = json.loads(result)
        assert data["extractor"] == "raw"
        assert "plain text content" in data["text"]

    async def test_readability_proxy_error(self):
        import httpx
        tool = WebFetchTool()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ProxyError("proxy fail"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool._fetch_readability("https://example.com", "text", 50000)
        data = json.loads(result)
        assert "Proxy error" in data["error"]

    async def test_readability_generic_error(self):
        tool = WebFetchTool()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=RuntimeError("oops"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool._fetch_readability("https://example.com", "text", 50000)
        data = json.loads(result)
        assert "error" in data


# ---------------------------------------------------------------------------
# WebFetchTool._to_markdown
# ---------------------------------------------------------------------------


class TestToMarkdown:

    def test_links_converted(self):
        tool = WebFetchTool()
        result = tool._to_markdown('<a href="https://x.com">Link</a>')
        assert "[Link](https://x.com)" in result

    def test_headings_converted(self):
        tool = WebFetchTool()
        result = tool._to_markdown("<h1>Title</h1>")
        assert "# Title" in result

    def test_h3_converted(self):
        tool = WebFetchTool()
        result = tool._to_markdown("<h3>Sub</h3>")
        assert "### Sub" in result

    def test_list_items(self):
        tool = WebFetchTool()
        result = tool._to_markdown("<li>Item1</li><li>Item2</li>")
        assert "- Item1" in result
        assert "- Item2" in result

    def test_br_converted(self):
        tool = WebFetchTool()
        result = tool._to_markdown("line1<br>line2")
        assert "line1" in result
        assert "line2" in result

    def test_p_closing_adds_newlines(self):
        tool = WebFetchTool()
        result = tool._to_markdown("<p>para1</p><p>para2</p>")
        assert "para1" in result
        assert "para2" in result
