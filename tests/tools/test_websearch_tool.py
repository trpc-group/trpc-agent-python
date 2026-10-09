# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for :mod:`trpc_agent_sdk.tools._websearch_tool`.

Covers the full BaseTool surface area:
- ``SearchHit`` / ``WebSearchResult`` / ``TavilyWebSearchResult`` schemas
- Constructor validation and provider dispatch
- ``_get_declaration`` parameter shape (including Tavily ``include_images``)
- Input validation (missing query, short query, mutually exclusive
  ``allowed_domains`` / ``blocked_domains``)
- DuckDuckGo Instant Answer path (summary aggregation, related topics,
  fallback result, post-hoc domain filtering)
- Google CSE path (items, pagemap metatag enrichment, spell-corrected
  effective query, server-side domain filter parameters, API error,
  missing credentials)
- Tavily Search path (results, optional images, domain include/exclude,
  API error, missing credentials, extra params)
- You.com ``you-search`` MCP path (stateless JSON-RPC over SSE, keyless
  free profile, YDC_API_KEY authenticated endpoint, lang inline filter,
  server-side excluded / post-hoc allowed domains, JSON-RPC and tool
  errors, extra params; malformed highlights, structuredContent payloads,
  request-id frame matching, SSE multi-line data + BOM, results-shape
  drift)
- HTTP errors surfacing as structured tool errors
- ``process_request`` registering the declaration and appending the
  "current month / Sources" system instruction
- Internal helpers (``_truncate``, ``_extract_domain_from_url``, ``_is_blocked``,
  ``_extract_title_from_ddg_topic``, ``_extract_desc_from_pagemap``)
"""

from __future__ import annotations

# Ensure ``pydantic.root_model`` is registered before MCP/google-genai import
# chains run; otherwise collection can hit KeyError: 'pydantic.root_model'.
import pydantic.root_model  # noqa: F401

import json
from typing import Any
from typing import Dict
from unittest.mock import MagicMock

import httpx
import pytest

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.tools._websearch_tool import ImageHit
from trpc_agent_sdk.tools._websearch_tool import SearchHit
from trpc_agent_sdk.tools._websearch_tool import TavilyWebSearchResult
from trpc_agent_sdk.tools._websearch_tool import WebSearchResult
from trpc_agent_sdk.tools._websearch_tool import WebSearchTool
from trpc_agent_sdk.tools._websearch_tool import _current_month_year
from trpc_agent_sdk.tools._websearch_tool import _dedup_key
from trpc_agent_sdk.tools._websearch_tool import _extract_domain_from_url
from trpc_agent_sdk.tools._websearch_tool import _extract_desc_from_pagemap
from trpc_agent_sdk.tools._websearch_tool import _extract_title_from_ddg_topic
from trpc_agent_sdk.tools._websearch_tool import _is_blocked
from trpc_agent_sdk.tools._websearch_tool import _parse_sse_payloads
from trpc_agent_sdk.tools._websearch_tool import _truncate
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Type

# Tool name is hard-coded inside WebSearchTool.__init__; tests compare by
# literal to catch accidental rename regressions.
_TOOL_NAME = "websearch"


def _make_mock_client(responses: Dict[str, Dict[str, Any]], *, status: int = 200) -> httpx.AsyncClient:
    """Build an ``httpx.AsyncClient`` backed by ``MockTransport``.

    ``responses`` maps request URL path (e.g. ``"/"``) to a JSON body.
    All non-matching paths return 404 so test misses surface loudly.
    The returned client captures the last request for assertions via
    ``client._last_request``.
    """

    captured = {"last_request": None, "all_requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["last_request"] = request
        captured["all_requests"].append(request)
        body = responses.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"error": "no mock for path"})
        return httpx.Response(status, json=body)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    # Stash captures on the client so tests can introspect them.
    client._captured = captured  # type: ignore[attr-defined]
    return client


def _make_mock_sse_client(responses: Dict[str, str], *, status: int = 200) -> httpx.AsyncClient:
    """Build an ``httpx.AsyncClient`` backed by ``MockTransport`` for SSE bodies.

    The You.com MCP endpoint answers JSON-RPC POSTs with
    ``text/event-stream`` instead of a JSON document; ``responses`` maps
    request URL path (e.g. ``"/mcp"``) to the raw SSE text to return.
    All non-matching paths return 404 so test misses surface loudly.
    The returned client captures the last request for assertions via
    ``client._captured``.
    """

    captured = {"last_request": None, "all_requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["last_request"] = request
        captured["all_requests"].append(request)
        body = responses.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"error": "no mock for path"})
        return httpx.Response(
            status,
            text=body,
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    # Stash captures on the client so tests can introspect them.
    client._captured = captured  # type: ignore[attr-defined]
    return client


def _tool_ctx() -> InvocationContext:
    """Return a minimal fake ``InvocationContext`` — our tool does not touch it."""
    return MagicMock(spec=InvocationContext)


class TestSearchHitSchema:

    def test_defaults(self):
        hit = SearchHit()
        assert hit.title == ""
        assert hit.url == ""
        assert hit.snippet == ""

    def test_roundtrip(self):
        hit = SearchHit(title="T", url="https://x", snippet="S")
        restored = SearchHit.model_validate(hit.model_dump())
        assert restored == hit


class TestWebSearchResultSchema:

    def test_defaults(self):
        r = WebSearchResult(query="q", provider="duckduckgo")
        assert r.results == []
        assert r.summary == ""

    def test_model_dump_is_json_serialisable(self):
        r = WebSearchResult(query="q", provider="google", results=[SearchHit(title="t", url="u")])
        as_json = json.dumps(r.model_dump(), ensure_ascii=False)
        parsed = json.loads(as_json)
        assert parsed["provider"] == "google"
        assert parsed["results"][0]["title"] == "t"


class TestTavilyWebSearchResultSchema:

    def test_defaults_include_images(self):
        r = TavilyWebSearchResult(query="q", provider="tavily")
        assert r.images == []
        assert r.results == []

    def test_roundtrip_with_images(self):
        r = TavilyWebSearchResult(
            query="q",
            provider="tavily",
            results=[SearchHit(title="t", url="https://x", snippet="s")],
            images=[ImageHit(url="https://img.example/a.png", description="alt")],
        )
        restored = TavilyWebSearchResult.model_validate(r.model_dump())
        assert restored == r


class TestWebSearchToolInit:

    def test_default_is_duckduckgo(self):
        t = WebSearchTool()
        assert t.name == _TOOL_NAME
        assert t._provider == "duckduckgo"

    def test_invalid_provider_raises(self):
        with pytest.raises(ValueError, match="Unsupported web search provider"):
            WebSearchTool(provider="bing")  # type: ignore[arg-type]

    def test_google_without_creds_warns_not_raises(self, caplog):
        # Capture warnings from the tool's logger.
        caplog.set_level("WARNING")
        t = WebSearchTool(provider="google", api_key="", engine_id="")
        assert t._provider == "google"

    def test_tavily_without_creds_warns_not_raises(self, monkeypatch):
        # Empty ``api_key`` falls back to ``TAVILY_API_KEY``; clear it so the
        # missing-credentials path is exercised even when CI exports the var.
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        t = WebSearchTool(provider="tavily", api_key="")
        assert t._provider == "tavily"
        assert t._api_key == ""

    def test_tavily_reads_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "env-tavily-key")
        t = WebSearchTool(provider="tavily")
        assert t._api_key == "env-tavily-key"

    def test_youcom_keyless_by_default(self, monkeypatch):
        # No YDC_API_KEY → keyless free profile, no credentials needed.
        monkeypatch.delenv("YDC_API_KEY", raising=False)
        t = WebSearchTool(provider="youcom")
        assert t._api_key == ""
        assert t._base_url == "https://api.you.com/mcp?profile=free"

    def test_youcom_reads_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("YDC_API_KEY", "env-ydc-key")
        t = WebSearchTool(provider="youcom")
        assert t._api_key == "env-ydc-key"
        # A configured key switches to the authenticated endpoint.
        assert t._base_url == "https://api.you.com/mcp"

    def test_results_num_clamped(self):
        from trpc_agent_sdk.tools._websearch_tool import _MAX_COUNT
        # Above the cap → clamped to _MAX_COUNT.
        assert WebSearchTool(results_num=_MAX_COUNT + 50)._results_num == _MAX_COUNT
        # Below 1 → clamped up to 1.
        assert WebSearchTool(results_num=0)._results_num == 1


class TestGetDeclaration:

    def test_declaration_shape(self):
        decl = WebSearchTool()._get_declaration()
        assert isinstance(decl, FunctionDeclaration)
        assert decl.name == _TOOL_NAME
        props = decl.parameters.properties
        assert set(props.keys()) == {
            "query",
            "count",
            "allowed_domains",
            "blocked_domains",
            "lang",
        }
        assert decl.parameters.required == ["query"]
        # Arrays use nested schema items.
        assert props["allowed_domains"].type == Type.ARRAY
        assert props["allowed_domains"].items is not None

    def test_tavily_declaration_exposes_include_images(self):
        decl = WebSearchTool(provider="tavily", api_key="k")._get_declaration()
        props = decl.parameters.properties
        assert "include_images" in props
        assert props["include_images"].type == Type.BOOLEAN

    def test_youcom_declaration_has_base_props_only(self):
        decl = WebSearchTool(provider="youcom")._get_declaration()
        props = decl.parameters.properties
        # youcom adds no provider-specific properties (include_images is Tavily-only).
        assert set(props.keys()) == {
            "query",
            "count",
            "allowed_domains",
            "blocked_domains",
            "lang",
        }


class TestInputValidation:

    @pytest.mark.asyncio
    async def test_empty_query_errors(self):
        res = await WebSearchTool()._run_async_impl(tool_context=_tool_ctx(), args={})
        assert "error" in res
        assert "INVALID_QUERY" in res["error"]

    @pytest.mark.asyncio
    async def test_short_query_errors(self):
        res = await WebSearchTool()._run_async_impl(tool_context=_tool_ctx(), args={"query": "a"})
        assert "INVALID_QUERY" in res["error"]

    @pytest.mark.asyncio
    async def test_conflicting_domains_errors(self):
        res = await WebSearchTool()._run_async_impl(
            tool_context=_tool_ctx(),
            args={
                "query": "python",
                "allowed_domains": ["a.com"],
                "blocked_domains": ["b.com"],
            },
        )
        assert "INVALID_ARGS" in res["error"]

    @pytest.mark.asyncio
    async def test_all_empty_allowed_domains_errors(self):
        """Caller passing an allowlist of only empty strings used to silently
        drop every result via the fail-closed allowlist branch — we now
        surface INVALID_ARGS so the LLM can recover.
        """
        res = await WebSearchTool()._run_async_impl(
            tool_context=_tool_ctx(),
            args={
                "query": "python",
                "allowed_domains": ["", "  "],
            },
        )
        assert "INVALID_ARGS" in res["error"]
        assert "allowed_domains" in res["error"]

    @pytest.mark.asyncio
    async def test_all_empty_blocked_domains_errors(self):
        res = await WebSearchTool()._run_async_impl(
            tool_context=_tool_ctx(),
            args={
                "query": "python",
                "blocked_domains": ["", "  "],
            },
        )
        assert "INVALID_ARGS" in res["error"]
        assert "blocked_domains" in res["error"]

    @pytest.mark.asyncio
    async def test_partial_empty_domains_are_silently_trimmed(self):
        """Mixed list ``["python.org", ""]`` should still work — empties
        are trimmed and the request proceeds with the meaningful entry.
        """
        client = _make_mock_client({"/": _MIN_DDG_RESPONSE})
        t = WebSearchTool(http_client=client)
        res = await t._run_async_impl(
            tool_context=_tool_ctx(),
            args={
                "query": "python",
                "allowed_domains": ["python.org", "", "  "],
            },
        )
        assert "error" not in res
        await client.aclose()

    @pytest.mark.asyncio
    async def test_non_list_domains_errors(self):
        for key in ("allowed_domains", "blocked_domains"):
            res = await WebSearchTool()._run_async_impl(
                tool_context=_tool_ctx(),
                args={
                    "query": "python",
                    key: "python.org",
                },
            )
            assert "INVALID_ARGS" in res["error"]
            assert key in res["error"]

    @pytest.mark.asyncio
    async def test_non_string_domain_items_error(self):
        """Non-string items in a domain list must produce a structured
        ``INVALID_ARGS`` instead of crashing the tool with ``AttributeError``
        from the internal ``.strip()`` call.
        """
        for key in ("allowed_domains", "blocked_domains"):
            for bad_value in ([123, "python.org"], [{"d": "x"}], [None]):
                res = await WebSearchTool()._run_async_impl(
                    tool_context=_tool_ctx(),
                    args={
                        "query": "python",
                        key: bad_value,
                    },
                )
                assert "INVALID_ARGS" in res["error"], (key, bad_value, res)
                assert key in res["error"], (key, bad_value, res)

    @pytest.mark.asyncio
    async def test_count_clamped(self):
        """Invalid ``count`` strings must not crash the tool."""

        client = _make_mock_client({"/": _MIN_DDG_RESPONSE})
        t = WebSearchTool(http_client=client)
        res = await t._run_async_impl(
            tool_context=_tool_ctx(),
            args={
                "query": "python",
                "count": "not-a-number"
            },
        )
        # Count fallback should succeed (not error).
        assert "error" not in res
        await client.aclose()


_MIN_DDG_RESPONSE: Dict[str, Any] = {
    "Answer": "",
    "AbstractText": "",
    "Definition": "",
    "RelatedTopics": [],
}

_FULL_DDG_RESPONSE: Dict[str, Any] = {
    "Answer":
    "Python is a programming language.",
    "AbstractText":
    "Python is an interpreted, high-level language.",
    "AbstractSource":
    "Wikipedia",
    "Definition":
    "A general-purpose programming language.",
    "DefinitionSource":
    "MerriamWebster",
    "RelatedTopics": [
        {
            "Text": "Python (programming language) - A popular language used everywhere",
            "FirstURL": "https://duckduckgo.com/Python",
        },
        # Nested group — should get flattened.
        {
            "Topics": [{
                "Text": "CPython - Reference implementation",
                "FirstURL": "https://docs.python.org/cpython",
            }],
        },
        # Entry with no URL — must be skipped.
        {
            "Text": "No URL here",
            "FirstURL": "",
        },
    ],
}


class TestDuckDuckGoProvider:

    @pytest.mark.asyncio
    async def test_happy_path(self):
        client = _make_mock_client({"/": _FULL_DDG_RESPONSE})
        t = WebSearchTool(http_client=client)
        res = await t._run_async_impl(
            tool_context=_tool_ctx(),
            args={"query": "python"},
        )
        assert res["provider"] == "duckduckgo"
        assert res["query"] == "python"
        # Summary pulls in Answer + Abstract(+source) + Definition(+source).
        assert "Python is a programming language." in res["summary"]
        assert "Wikipedia" in res["summary"]
        assert "MerriamWebster" in res["summary"]
        # Two valid hits (one top-level, one flattened from nested Topics).
        urls = [h["url"] for h in res["results"]]
        assert "https://duckduckgo.com/Python" in urls
        assert "https://docs.python.org/cpython" in urls
        # Title is split on " - ".
        titles = [h["title"] for h in res["results"]]
        assert any(t.startswith("Python (programming language)") for t in titles)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fallback_when_no_topics_but_summary(self):
        body = {
            "Answer": "42",
            "AbstractText": "",
            "Definition": "",
            "RelatedTopics": [],
        }
        client = _make_mock_client({"/": body})
        t = WebSearchTool(http_client=client)
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "life"})
        assert len(res["results"]) == 1
        assert res["results"][0]["url"].startswith("https://duckduckgo.com/?q=")
        await client.aclose()

    @pytest.mark.asyncio
    async def test_empty_response_no_hits_no_summary(self):
        client = _make_mock_client({"/": _MIN_DDG_RESPONSE})
        t = WebSearchTool(http_client=client)
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "xyz"})
        assert res["results"] == []
        assert res["summary"] == ""
        await client.aclose()

    @pytest.mark.asyncio
    async def test_client_side_domain_filtering(self):
        client = _make_mock_client({"/": _FULL_DDG_RESPONSE})
        t = WebSearchTool(http_client=client)
        # Block python.org → docs.python.org should disappear; only ddg hit survives.
        res = await t._run_async_impl(
            tool_context=_tool_ctx(),
            args={
                "query": "python",
                "blocked_domains": ["python.org"],
            },
        )
        urls = [h["url"] for h in res["results"]]
        assert "https://docs.python.org/cpython" not in urls
        assert "https://duckduckgo.com/Python" in urls
        await client.aclose()

    @pytest.mark.asyncio
    async def test_count_limits_hits(self):
        client = _make_mock_client({"/": _FULL_DDG_RESPONSE})
        t = WebSearchTool(http_client=client, results_num=1)
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        assert len(res["results"]) == 1
        await client.aclose()

    @pytest.mark.asyncio
    async def test_results_are_deduplicated_by_url(self):
        """DDG often returns the same URL twice (top-level + nested
        ``Topics``); the tool must keep only the first occurrence so the
        LLM doesn't waste tokens citing the same source twice.
        """
        body = {
            "Answer":
            "",
            "AbstractText":
            "",
            "Definition":
            "",
            "RelatedTopics": [
                {
                    "Text": "Python (programming language) - first occurrence",
                    "FirstURL": "https://docs.python.org/3/",
                },
                {
                    "Topics": [
                        # Same URL with trailing slash dropped + www. → must dedupe.
                        {
                            "Text": "Python docs duplicate",
                            "FirstURL": "https://www.docs.python.org/3",
                        },
                        # A genuinely different URL must still come through.
                        {
                            "Text": "PEP 8 - style guide",
                            "FirstURL": "https://peps.python.org/pep-0008/",
                        },
                    ],
                },
            ],
        }
        client = _make_mock_client({"/": body})
        t = WebSearchTool(http_client=client)
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        urls = [h["url"] for h in res["results"]]
        # Only the first variant of the duplicated URL is kept; second one is dropped.
        assert urls == [
            "https://docs.python.org/3/",
            "https://peps.python.org/pep-0008/",
        ]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_dedup_urls_false_preserves_duplicates(self):
        """When the caller opts out of deduplication every provider hit
        must come through verbatim — useful for downstream re-rankers
        that want to see near-duplicate variants.
        """
        body = {
            "Answer":
            "",
            "AbstractText":
            "",
            "Definition":
            "",
            "RelatedTopics": [
                {
                    "Text": "Python (programming language) - first occurrence",
                    "FirstURL": "https://docs.python.org/3/",
                },
                {
                    "Topics": [
                        {
                            "Text": "Python docs duplicate",
                            "FirstURL": "https://www.docs.python.org/3",
                        },
                    ],
                },
            ],
        }
        client = _make_mock_client({"/": body})
        t = WebSearchTool(http_client=client, dedup_urls=False, results_num=5)
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        urls = [h["url"] for h in res["results"]]
        # Both variants come through because dedup is off.
        assert urls == [
            "https://docs.python.org/3/",
            "https://www.docs.python.org/3",
        ]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_snippet_and_title_len_respected(self):
        """DDG branch must honour tool-level snippet_len/title_len overrides.

        Previously these two knobs only affected the Google branch while DDG
        hard-coded 300/100, producing inconsistent output shapes between
        providers.
        """
        long_text = ("Python (programming language) - " + ("x" * 500))
        body = {
            "Answer": "",
            "AbstractText": "",
            "Definition": "",
            "RelatedTopics": [{
                "Text": long_text,
                "FirstURL": "https://duckduckgo.com/Python",
            }],
        }
        client = _make_mock_client({"/": body})
        t = WebSearchTool(http_client=client, snippet_len=50, title_len=10)
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        hit = res["results"][0]
        # +3 for the "..." suffix appended by _truncate.
        assert len(hit["snippet"]) <= 50 + 3
        assert len(hit["title"]) <= 10 + 3
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fallback_snippet_and_title_len_respected(self):
        """Fallback synthetic hit must also honour snippet_len/title_len."""
        body = {
            "Answer": "Y" * 500,
            "AbstractText": "",
            "Definition": "",
            "RelatedTopics": [],
        }
        client = _make_mock_client({"/": body})
        t = WebSearchTool(http_client=client, snippet_len=40, title_len=8)
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "a-very-long-fallback-query"})
        hit = res["results"][0]
        assert len(hit["snippet"]) <= 40 + 3
        assert len(hit["title"]) <= 8 + 3
        await client.aclose()

    @pytest.mark.asyncio
    async def test_abstract_url_is_extracted_as_first_hit(self):
        """The abstract's canonical URL (usually Wikipedia) must be the
        primary citation, NOT internal ``duckduckgo.com/c/...`` entries
        from ``RelatedTopics``.

        This was the single most visible demo regression: DDG's
        ``RelatedTopics`` URLs are all ``https://duckduckgo.com/...``
        category pages, so for any entity-like query the tool used to
        return five useless self-links instead of a Wikipedia source.
        """
        body = {
            "Heading": "Python (programming language)",
            "Answer": "",
            "AbstractText": "Python is an interpreted language.",
            "AbstractSource": "Wikipedia",
            "AbstractURL": "https://en.wikipedia.org/wiki/Python_(programming_language)",
            "Definition": "",
            "RelatedTopics": [
                {
                    "Text": "Python Category",
                    "FirstURL": "https://duckduckgo.com/c/Python",
                },
            ],
        }
        client = _make_mock_client({"/": body})
        t = WebSearchTool(http_client=client, results_num=5)
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        urls = [h["url"] for h in res["results"]]
        assert urls[0] == "https://en.wikipedia.org/wiki/Python_(programming_language)"
        assert res["results"][0]["title"] == "Python (programming language)"
        # RelatedTopics still come after the canonical abstract URL.
        assert "https://duckduckgo.com/c/Python" in urls
        await client.aclose()

    @pytest.mark.asyncio
    async def test_definition_url_is_extracted_when_distinct(self):
        """``DefinitionURL`` (dictionary source) must also produce a hit."""
        body = {
            "Heading": "Vector database",
            "Answer": "",
            "AbstractText": "A vector database stores embeddings.",
            "AbstractURL": "https://en.wikipedia.org/wiki/Vector_database",
            "Definition": "A database optimised for vector similarity search.",
            "DefinitionSource": "Wiktionary",
            "DefinitionURL": "https://en.wiktionary.org/wiki/vector_database",
            "RelatedTopics": [],
        }
        client = _make_mock_client({"/": body})
        t = WebSearchTool(http_client=client, results_num=5)
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "vector database"})
        urls = [h["url"] for h in res["results"]]
        assert "https://en.wikipedia.org/wiki/Vector_database" in urls
        assert "https://en.wiktionary.org/wiki/vector_database" in urls
        # AbstractURL is the first (higher-priority) entry.
        assert urls[0] == "https://en.wikipedia.org/wiki/Vector_database"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_results_field_is_extracted(self):
        """DDG's ``Results`` array (external links for brand queries) must
        flow through just like ``RelatedTopics`` but at a higher priority.
        """
        body = {
            "Heading": "GitHub",
            "Answer": "",
            "AbstractText": "GitHub is a code hosting platform.",
            "AbstractURL": "https://en.wikipedia.org/wiki/GitHub",
            "Definition": "",
            "Results": [
                {
                    "Text": "GitHub - Official site",
                    "FirstURL": "https://github.com",
                },
            ],
            "RelatedTopics": [
                {
                    "Text": "Git (software)",
                    "FirstURL": "https://duckduckgo.com/Git",
                },
            ],
        }
        client = _make_mock_client({"/": body})
        t = WebSearchTool(http_client=client, results_num=5)
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "github"})
        urls = [h["url"] for h in res["results"]]
        # Priority order: AbstractURL → Results → RelatedTopics.
        assert urls == [
            "https://en.wikipedia.org/wiki/GitHub",
            "https://github.com",
            "https://duckduckgo.com/Git",
        ]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_abstract_url_respects_allowed_domains(self):
        """An ``allowed_domains=['wikipedia.org']`` request must now keep
        the Wikipedia abstract URL and drop internal DDG pages — this
        was broken before because the abstract URL was never extracted.
        """
        body = {
            "Heading": "Python (programming language)",
            "AbstractText": "Python is an interpreted language.",
            "AbstractURL": "https://en.wikipedia.org/wiki/Python_(programming_language)",
            "RelatedTopics": [
                {
                    "Text": "Python Category",
                    "FirstURL": "https://duckduckgo.com/c/Python",
                },
            ],
        }
        client = _make_mock_client({"/": body})
        t = WebSearchTool(http_client=client, results_num=5)
        res = await t._run_async_impl(
            tool_context=_tool_ctx(),
            args={
                "query": "python",
                "allowed_domains": ["wikipedia.org"],
            },
        )
        urls = [h["url"] for h in res["results"]]
        assert urls == [
            "https://en.wikipedia.org/wiki/Python_(programming_language)",
        ]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_abstract_and_related_topics_dedupe_against_each_other(self):
        """If ``AbstractURL`` and a ``RelatedTopics`` entry happen to
        point at the same page (same normalised key) we must only keep
        one — the higher-priority ``AbstractURL`` copy.
        """
        body = {
            "Heading":
            "Python",
            "AbstractText":
            "Python programming language.",
            "AbstractURL":
            "https://en.wikipedia.org/wiki/Python_(programming_language)",
            "RelatedTopics": [
                {
                    "Text": "Python programming language",
                    # Same URL with trailing slash + www. → dedupe key collides.
                    "FirstURL": "https://www.en.wikipedia.org/wiki/Python_(programming_language)/",
                },
                {
                    "Text": "CPython - Reference implementation",
                    "FirstURL": "https://docs.python.org/cpython",
                },
            ],
        }
        client = _make_mock_client({"/": body})
        t = WebSearchTool(http_client=client, results_num=5)
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        urls = [h["url"] for h in res["results"]]
        assert urls == [
            "https://en.wikipedia.org/wiki/Python_(programming_language)",
            "https://docs.python.org/cpython",
        ]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_count_limit_caps_high_priority_sources(self):
        """``count=1`` must keep only the highest-priority hit
        (AbstractURL) and drop everything else — including Results /
        RelatedTopics.
        """
        body = {
            "Heading": "Python",
            "AbstractText": "Python is a language.",
            "AbstractURL": "https://en.wikipedia.org/wiki/Python_(programming_language)",
            "Results": [{
                "Text": "Python.org",
                "FirstURL": "https://python.org",
            }],
            "RelatedTopics": [{
                "Text": "NumPy",
                "FirstURL": "https://duckduckgo.com/NumPy",
            }],
        }
        client = _make_mock_client({"/": body})
        t = WebSearchTool(http_client=client, results_num=5)
        res = await t._run_async_impl(
            tool_context=_tool_ctx(),
            args={
                "query": "python",
                "count": 1,
            },
        )
        urls = [h["url"] for h in res["results"]]
        assert urls == [
            "https://en.wikipedia.org/wiki/Python_(programming_language)",
        ]
        await client.aclose()


_GOOGLE_RESPONSE: Dict[str, Any] = {
    "queries": {
        "request": [{
            "searchTerms": "python releases"
        }]
    },
    "items": [
        {
            "title": "Python 3.13 Release Notes",
            "link": "https://python.org/releases/3.13",
            "snippet": "Release notes for Python 3.13.",
            "pagemap": {
                "metatags": [{
                    "description": "Official release notes",
                    "og:description": "Python 3.13 official",
                }],
            },
        },
        {
            "title": "Python on Wikipedia",
            "link": "https://en.wikipedia.org/wiki/Python",
            "snippet": "Encyclopedia entry for Python.",
        },
    ],
}


class TestGoogleProvider:

    @pytest.mark.asyncio
    async def test_missing_credentials_returns_helpful_result(self):
        t = WebSearchTool(provider="google", api_key="", engine_id="")
        res = await t._run_async_impl(
            tool_context=_tool_ctx(),
            args={"query": "python"},
        )
        assert res["provider"] == "google"
        assert res["results"] == []
        assert "not configured" in res["summary"]

    @pytest.mark.asyncio
    async def test_happy_path(self):
        client = _make_mock_client({"/customsearch/v1": _GOOGLE_RESPONSE})
        t = WebSearchTool(
            provider="google",
            api_key="fake-key",
            engine_id="fake-cx",
            http_client=client,
        )
        res = await t._run_async_impl(
            tool_context=_tool_ctx(),
            args={
                "query": "python",
                "lang": "en",
            },
        )
        assert res["provider"] == "google"
        assert [h["url"] for h in res["results"]] == [
            "https://python.org/releases/3.13",
            "https://en.wikipedia.org/wiki/Python",
        ]
        # Metatag enrichment is folded into the snippet.
        assert "Official release notes" in res["results"][0]["snippet"]
        # Effective query is surfaced because it differs from user query.
        assert "python releases" in res["summary"]

        # Inspect the last request — lang + key + cx should be attached.
        req = client._captured["last_request"]
        assert req.url.params.get("hl") == "en"
        assert req.url.params.get("key") == "fake-key"
        assert req.url.params.get("cx") == "fake-cx"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_single_allowed_domain_maps_to_server_side_filter(self):
        """Exactly ONE allowed domain → offload to Google's siteSearch."""
        client = _make_mock_client({"/customsearch/v1": _GOOGLE_RESPONSE})
        t = WebSearchTool(
            provider="google",
            api_key="k",
            engine_id="cx",
            http_client=client,
        )
        await t._run_async_impl(
            tool_context=_tool_ctx(),
            args={
                "query": "python",
                "allowed_domains": ["python.org"],
            },
        )
        req = client._captured["last_request"]
        assert req.url.params.get("siteSearch") == "python.org"
        assert req.url.params.get("siteSearchFilter") == "i"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_single_blocked_domain_maps_to_server_side_filter(self):
        """Exactly ONE blocked domain → offload to Google's siteSearch."""
        client = _make_mock_client({"/customsearch/v1": _GOOGLE_RESPONSE})
        t = WebSearchTool(
            provider="google",
            api_key="k",
            engine_id="cx",
            http_client=client,
        )
        await t._run_async_impl(
            tool_context=_tool_ctx(),
            args={
                "query": "python",
                "blocked_domains": ["wikipedia.org"],
            },
        )
        req = client._captured["last_request"]
        assert req.url.params.get("siteSearch") == "wikipedia.org"
        assert req.url.params.get("siteSearchFilter") == "e"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_multi_allowed_domains_skip_server_filter_and_filter_clientside(self):
        """Multiple allowed domains → no siteSearch, rely on client-side filter.

        Without this behaviour CSE would only return results from
        ``allowed[0]`` and silently drop everything from ``allowed[1:]``.
        """
        client = _make_mock_client({"/customsearch/v1": _GOOGLE_RESPONSE})
        t = WebSearchTool(
            provider="google",
            api_key="k",
            engine_id="cx",
            http_client=client,
        )
        res = await t._run_async_impl(
            tool_context=_tool_ctx(),
            args={
                "query": "python",
                "allowed_domains": ["python.org", "wikipedia.org"],
            },
        )

        # No server-side filter should be attached.
        req = client._captured["last_request"]
        assert req.url.params.get("siteSearch") is None
        assert req.url.params.get("siteSearchFilter") is None

        # Client-side filter keeps only the whitelisted domains.
        urls = [h["url"] for h in res["results"]]
        assert "https://python.org/releases/3.13" in urls
        assert "https://en.wikipedia.org/wiki/Python" in urls
        await client.aclose()

    @pytest.mark.asyncio
    async def test_multi_blocked_domains_skip_server_filter_and_filter_clientside(self):
        """Multiple blocked domains → no siteSearch, rely on client-side filter."""
        client = _make_mock_client({"/customsearch/v1": _GOOGLE_RESPONSE})
        t = WebSearchTool(
            provider="google",
            api_key="k",
            engine_id="cx",
            http_client=client,
        )
        res = await t._run_async_impl(
            tool_context=_tool_ctx(),
            args={
                "query": "python",
                "blocked_domains": ["wikipedia.org", "stackoverflow.com"],
            },
        )

        req = client._captured["last_request"]
        assert req.url.params.get("siteSearch") is None
        assert req.url.params.get("siteSearchFilter") is None

        # Client-side filter excludes blocked domains.
        urls = [h["url"] for h in res["results"]]
        assert "https://en.wikipedia.org/wiki/Python" not in urls
        assert "https://python.org/releases/3.13" in urls
        await client.aclose()

    @pytest.mark.asyncio
    async def test_results_are_deduplicated_by_url(self):
        """Google CSE rarely emits duplicates on its own, but ``http``/
        ``https`` and trailing-slash variants still slip through (and
        ``google_extra_params`` can produce them); the tool must collapse
        them.
        """
        body = {
            "queries": {
                "request": [{
                    "searchTerms": "python"
                }]
            },
            "items": [
                {
                    "title": "Python docs",
                    "link": "https://docs.python.org/3",
                    "snippet": "first",
                },
                {
                    "title": "Python docs (dup)",
                    "link": "https://www.docs.python.org/3/",
                    "snippet": "duplicate",
                },
                {
                    "title": "PEP 8",
                    "link": "https://peps.python.org/pep-0008/",
                    "snippet": "style",
                },
            ],
        }
        client = _make_mock_client({"/customsearch/v1": body})
        t = WebSearchTool(provider="google", api_key="k", engine_id="cx", http_client=client)
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        urls = [h["url"] for h in res["results"]]
        assert urls == [
            "https://docs.python.org/3",
            "https://peps.python.org/pep-0008/",
        ]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_dedup_urls_false_preserves_duplicates(self):
        body = {
            "queries": {
                "request": [{
                    "searchTerms": "python"
                }]
            },
            "items": [
                {
                    "title": "Python docs",
                    "link": "https://docs.python.org/3",
                    "snippet": "first",
                },
                {
                    "title": "Python docs (dup)",
                    "link": "https://www.docs.python.org/3/",
                    "snippet": "duplicate",
                },
            ],
        }
        client = _make_mock_client({"/customsearch/v1": body})
        t = WebSearchTool(
            provider="google",
            api_key="k",
            engine_id="cx",
            http_client=client,
            dedup_urls=False,
        )
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        urls = [h["url"] for h in res["results"]]
        assert urls == [
            "https://docs.python.org/3",
            "https://www.docs.python.org/3/",
        ]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_google_num_clamped_to_provider_hard_cap(self):
        """Google CSE rejects ``num > 10`` — the tool must clamp server-side.

        The tool-level ``count`` / ``_MAX_COUNT`` can legitimately go up to 20,
        but when the provider is Google that value MUST be clamped to 10 in
        the outgoing request to avoid a 400 from CSE.
        """
        client = _make_mock_client({"/customsearch/v1": _GOOGLE_RESPONSE})
        t = WebSearchTool(
            provider="google",
            api_key="k",
            engine_id="cx",
            http_client=client,
        )
        await t._run_async_impl(
            tool_context=_tool_ctx(),
            args={
                "query": "python",
                "count": 20,
            },
        )
        req = client._captured["last_request"]
        assert req.url.params.get("num") == "10"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_api_error_surfaced_as_structured_result(self):
        error_body = {
            "error": {
                "code": 403,
                "message": "API key invalid",
            }
        }
        client = _make_mock_client({"/customsearch/v1": error_body})
        t = WebSearchTool(
            provider="google",
            api_key="bad",
            engine_id="cx",
            http_client=client,
        )
        res = await t._run_async_impl(
            tool_context=_tool_ctx(),
            args={"query": "python"},
        )
        assert res["results"] == []
        assert "API key invalid" in res["summary"]
        await client.aclose()


_TAVILY_RESPONSE: Dict[str, Any] = {
    "answer":
    "Python 3.13 adds free-threaded support.",
    "results": [
        {
            "title": "What's New In Python 3.13",
            "url": "https://docs.python.org/3/whatsnew/3.13.html",
            "content": "This article explains the new features in Python 3.13.",
        },
        {
            "title": "Python on Wikipedia",
            "url": "https://en.wikipedia.org/wiki/Python_(programming_language)",
            "content": "Python is a high-level programming language.",
            "images": [{
                "url": "https://upload.wikimedia.org/python.png",
                "description": "Python logo",
            }],
        },
    ],
    "images": [
        "https://cdn.example.com/python.jpg",
        {
            "url": "https://cdn.example.com/python.jpg",
            "description": "duplicate image",
        },
        {
            "url": "https://images.python.org/hero.png",
            "description": "Python hero image",
        },
    ],
}


class TestTavilyProvider:

    @pytest.mark.asyncio
    async def test_missing_credentials_returns_helpful_result(self, monkeypatch):
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        t = WebSearchTool(provider="tavily", api_key="")
        res = await t._run_async_impl(
            tool_context=_tool_ctx(),
            args={"query": "python"},
        )
        assert res["provider"] == "tavily"
        assert res["results"] == []
        assert res["images"] == []
        assert "not configured" in res["summary"]

    @pytest.mark.asyncio
    async def test_happy_path_posts_payload_and_parses_results(self):
        client = _make_mock_client({"/search": _TAVILY_RESPONSE})
        t = WebSearchTool(
            provider="tavily",
            api_key="tvly-test",
            http_client=client,
            base_url="https://api.tavily.com/search",
        )
        res = await t._run_async_impl(
            tool_context=_tool_ctx(),
            args={
                "query": "Python 3.13",
                "count": 2,
            },
        )
        assert res["provider"] == "tavily"
        assert res["query"] == "Python 3.13"
        # The implementation surfaces ``answer`` whenever Tavily returns it.
        assert "free-threaded" in res["summary"]
        assert [h["url"] for h in res["results"]] == [
            "https://docs.python.org/3/whatsnew/3.13.html",
            "https://en.wikipedia.org/wiki/Python_(programming_language)",
        ]
        assert res["images"] == []

        req = client._captured["last_request"]
        assert req.method == "POST"
        assert req.headers.get("authorization") == "Bearer tvly-test"
        payload = json.loads(req.content)
        assert payload["query"] == "Python 3.13"
        assert payload["max_results"] == 2
        assert payload["include_images"] is False
        assert payload["include_answer"] is False
        await client.aclose()

    @pytest.mark.asyncio
    async def test_include_answer_fills_summary(self):
        client = _make_mock_client({"/search": _TAVILY_RESPONSE})
        t = WebSearchTool(
            provider="tavily",
            api_key="tvly-test",
            http_client=client,
            base_url="https://api.tavily.com/search",
            tavily_extra_params={"include_answer": True},
        )
        res = await t._run_async_impl(
            tool_context=_tool_ctx(),
            args={"query": "Python 3.13"},
        )
        assert "free-threaded" in res["summary"]
        payload = json.loads(client._captured["last_request"].content)
        assert payload["include_answer"] is True
        await client.aclose()

    @pytest.mark.asyncio
    async def test_include_images_returns_deduped_image_hits(self):
        client = _make_mock_client({"/search": _TAVILY_RESPONSE})
        t = WebSearchTool(
            provider="tavily",
            api_key="tvly-test",
            http_client=client,
            base_url="https://api.tavily.com/search",
            results_num=5,
        )
        res = await t._run_async_impl(
            tool_context=_tool_ctx(),
            args={
                "query": "python",
                "include_images": True,
            },
        )
        image_urls = [img["url"] for img in res["images"]]
        assert image_urls == [
            "https://cdn.example.com/python.jpg",
            "https://images.python.org/hero.png",
            "https://upload.wikimedia.org/python.png",
        ]
        assert res["images"][2]["description"] == "Python logo"

        req = client._captured["last_request"]
        payload = json.loads(req.content)
        assert payload["include_images"] is True
        await client.aclose()

    @pytest.mark.asyncio
    async def test_allowed_domains_mapped_to_include_domains(self):
        client = _make_mock_client({"/search": _TAVILY_RESPONSE})
        t = WebSearchTool(
            provider="tavily",
            api_key="tvly-test",
            http_client=client,
            base_url="https://api.tavily.com/search",
        )
        res = await t._run_async_impl(
            tool_context=_tool_ctx(),
            args={
                "query": "python",
                "allowed_domains": ["python.org"],
            },
        )
        req = client._captured["last_request"]
        payload = json.loads(req.content)
        assert payload["include_domains"] == ["python.org"]
        assert "exclude_domains" not in payload
        # Client-side filter keeps only python.org (docs.python.org matches).
        assert [h["url"] for h in res["results"]] == [
            "https://docs.python.org/3/whatsnew/3.13.html",
        ]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_blocked_domains_mapped_to_exclude_domains(self):
        client = _make_mock_client({"/search": _TAVILY_RESPONSE})
        t = WebSearchTool(
            provider="tavily",
            api_key="tvly-test",
            http_client=client,
            base_url="https://api.tavily.com/search",
        )
        res = await t._run_async_impl(
            tool_context=_tool_ctx(),
            args={
                "query": "python",
                "blocked_domains": ["wikipedia.org"],
            },
        )
        req = client._captured["last_request"]
        payload = json.loads(req.content)
        assert payload["exclude_domains"] == ["wikipedia.org"]
        urls = [h["url"] for h in res["results"]]
        assert "https://en.wikipedia.org/wiki/Python_(programming_language)" not in urls
        assert "https://docs.python.org/3/whatsnew/3.13.html" in urls
        await client.aclose()

    @pytest.mark.asyncio
    async def test_results_are_deduplicated_by_url(self):
        body = {
            "results": [
                {
                    "title": "Python docs",
                    "url": "https://docs.python.org/3",
                    "content": "first",
                },
                {
                    "title": "Python docs (dup)",
                    "url": "https://www.docs.python.org/3/",
                    "content": "duplicate",
                },
                {
                    "title": "PEP 8",
                    "url": "https://peps.python.org/pep-0008/",
                    "content": "style",
                },
            ],
        }
        client = _make_mock_client({"/search": body})
        t = WebSearchTool(
            provider="tavily",
            api_key="tvly-test",
            http_client=client,
            base_url="https://api.tavily.com/search",
        )
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        assert [h["url"] for h in res["results"]] == [
            "https://docs.python.org/3",
            "https://peps.python.org/pep-0008/",
        ]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_tavily_extra_params_are_merged(self):
        client = _make_mock_client({"/search": {"results": []}})
        t = WebSearchTool(
            provider="tavily",
            api_key="tvly-test",
            http_client=client,
            base_url="https://api.tavily.com/search",
            tavily_extra_params={
                "search_depth": "advanced",
                "include_answer": True,
            },
        )
        await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        payload = json.loads(client._captured["last_request"].content)
        assert payload["search_depth"] == "advanced"
        assert payload["include_answer"] is True
        await client.aclose()

    @pytest.mark.asyncio
    async def test_api_error_surfaced_as_structured_result(self):
        client = _make_mock_client({"/search": {"error": "Invalid API key"}})
        t = WebSearchTool(
            provider="tavily",
            api_key="bad",
            http_client=client,
            base_url="https://api.tavily.com/search",
        )
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        assert res["results"] == []
        assert res["images"] == []
        assert "Invalid API key" in res["summary"]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_count_limits_hits_and_images(self):
        client = _make_mock_client({"/search": _TAVILY_RESPONSE})
        t = WebSearchTool(
            provider="tavily",
            api_key="tvly-test",
            http_client=client,
            base_url="https://api.tavily.com/search",
            results_num=1,
        )
        res = await t._run_async_impl(
            tool_context=_tool_ctx(),
            args={
                "query": "python",
                "include_images": True,
            },
        )
        assert len(res["results"]) == 1
        assert len(res["images"]) == 1
        await client.aclose()


_YOUCOM_TOOL_TEXT: str = json.dumps({
    "results": {
        "web": [
            {
                "title": "What's New In Python 3.13",
                "url": "https://docs.python.org/3/whatsnew/3.13.html",
                "description": "This article explains the new features in Python 3.13.",
                "contents": {
                    "highlights": ["Python 3.13 adds free-threaded support."],
                },
            },
            {
                # No description — snippet must fall back to the first highlight.
                "title": "Python on Wikipedia",
                "url": "https://en.wikipedia.org/wiki/Python_(programming_language)",
                "description": "",
                "contents": {
                    "highlights": ["Python is a high-level programming language."],
                },
            },
        ],
        "news": [
            {
                "title": "Python 3.13.1 released",
                "url": "https://blog.python.org/python-3-13-1-released",
                "description": "The first maintenance release of Python 3.13.",
            },
        ],
    },
})


def _youcom_sse_body(text: str) -> str:
    """Build the SSE body the You.com endpoint returns for ``tools/call``.

    Mirrors the wire format of ``https://api.you.com/mcp``: an optional
    ``notifications/message`` notification followed by the JSON-RPC result
    carrying the tool text content.
    """
    notification = {
        "jsonrpc": "2.0",
        "method": "notifications/message",
        "params": {
            "level": "info",
            "data": "Search successful",
        },
    }
    result = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "content": [{
                "type": "text",
                "text": text,
            }],
        },
    }
    return ("event: message\n"
            f"data: {json.dumps(notification)}\n\n"
            "event: message\n"
            f"data: {json.dumps(result)}\n\n")


class TestYoucomProvider:
    """You.com ``youcom`` provider: stateless JSON-RPC over streamable HTTP."""

    @pytest.mark.asyncio
    async def test_keyless_search_maps_web_and_news_hits(self, monkeypatch):
        monkeypatch.delenv("YDC_API_KEY", raising=False)
        client = _make_mock_sse_client({"/mcp": _youcom_sse_body(_YOUCOM_TOOL_TEXT)})
        t = WebSearchTool(provider="youcom", http_client=client)
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "Python 3.13"})
        assert res["provider"] == "youcom"
        assert res["query"] == "Python 3.13"
        assert res["summary"] == ""
        urls = [h["url"] for h in res["results"]]
        # Web hits first, then news hits merged in.
        assert urls == [
            "https://docs.python.org/3/whatsnew/3.13.html",
            "https://en.wikipedia.org/wiki/Python_(programming_language)",
            "https://blog.python.org/python-3-13-1-released",
        ]
        # Descriptions map to snippets...
        assert "new features in Python 3.13" in res["results"][0]["snippet"]
        # ...with first-highlight fallback when description is empty.
        assert res["results"][1]["snippet"] == "Python is a high-level programming language."
        req = client._captured["last_request"]
        # Keyless: no Authorization header on the free-profile request.
        assert req.headers.get("authorization") is None
        # The endpoint rejects JSON-only Accept headers (406); SSE must be accepted.
        assert "text/event-stream" in req.headers.get("accept", "")
        payload = json.loads(req.content)
        assert payload["method"] == "tools/call"
        assert payload["params"]["name"] == "you-search"
        arguments = payload["params"]["arguments"]
        assert arguments["query"] == "Python 3.13"
        assert arguments["count"] == 5  # default results_num
        await client.aclose()

    @pytest.mark.asyncio
    async def test_api_key_sends_bearer_header(self):
        client = _make_mock_sse_client({"/mcp": _youcom_sse_body(_YOUCOM_TOOL_TEXT)})
        t = WebSearchTool(
            provider="youcom",
            api_key="ydc-test",
            http_client=client,
            base_url="https://api.you.com/mcp",
        )
        await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        req = client._captured["last_request"]
        assert req.headers.get("authorization") == "Bearer ydc-test"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_lang_maps_to_inline_filter(self):
        client = _make_mock_sse_client({"/mcp": _youcom_sse_body(_YOUCOM_TOOL_TEXT)})
        t = WebSearchTool(
            provider="youcom",
            http_client=client,
            base_url="https://api.you.com/mcp?profile=free",
        )
        await t._run_async_impl(
            tool_context=_tool_ctx(),
            args={
                "query": "python release",
                "lang": "zh-CN"
            },
        )
        payload = json.loads(client._captured["last_request"].content)
        assert payload["params"]["arguments"]["query"] == "python release lang:zh-CN"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_blocked_domains_sent_server_side_and_filtered(self):
        client = _make_mock_sse_client({"/mcp": _youcom_sse_body(_YOUCOM_TOOL_TEXT)})
        t = WebSearchTool(
            provider="youcom",
            http_client=client,
            base_url="https://api.you.com/mcp?profile=free",
        )
        res = await t._run_async_impl(
            tool_context=_tool_ctx(),
            args={
                "query": "python",
                "blocked_domains": ["wikipedia.org"]
            },
        )
        payload = json.loads(client._captured["last_request"].content)
        # Blocked domains ride along as server-side exclude_domains.
        assert payload["params"]["arguments"]["exclude_domains"] == ["wikipedia.org"]
        urls = [h["url"] for h in res["results"]]
        assert all("wikipedia.org" not in u for u in urls)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_allowed_domains_filter_post_hoc(self):
        client = _make_mock_sse_client({"/mcp": _youcom_sse_body(_YOUCOM_TOOL_TEXT)})
        t = WebSearchTool(
            provider="youcom",
            http_client=client,
            base_url="https://api.you.com/mcp?profile=free",
        )
        res = await t._run_async_impl(
            tool_context=_tool_ctx(),
            args={
                "query": "python",
                "allowed_domains": ["python.org"]
            },
        )
        payload = json.loads(client._captured["last_request"].content)
        # Allowlists are enforced client-side; nothing rides along server-side.
        assert "exclude_domains" not in payload["params"]["arguments"]
        urls = [h["url"] for h in res["results"]]
        assert urls == [
            "https://docs.python.org/3/whatsnew/3.13.html",
            "https://blog.python.org/python-3-13-1-released",
        ]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_youcom_extra_params_are_merged(self):
        client = _make_mock_sse_client({"/mcp": _youcom_sse_body(_YOUCOM_TOOL_TEXT)})
        t = WebSearchTool(
            provider="youcom",
            http_client=client,
            base_url="https://api.you.com/mcp?profile=free",
            youcom_extra_params={"freshness": "week"},
        )
        await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        payload = json.loads(client._captured["last_request"].content)
        assert payload["params"]["arguments"]["freshness"] == "week"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_count_limits_hits(self):
        client = _make_mock_sse_client({"/mcp": _youcom_sse_body(_YOUCOM_TOOL_TEXT)})
        t = WebSearchTool(
            provider="youcom",
            http_client=client,
            base_url="https://api.you.com/mcp?profile=free",
            results_num=1,
        )
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        assert len(res["results"]) == 1
        payload = json.loads(client._captured["last_request"].content)
        assert payload["params"]["arguments"]["count"] == 1
        await client.aclose()

    @pytest.mark.asyncio
    async def test_dedup_urls_and_urlless_hits_skipped(self):
        text = json.dumps({
            "results": {
                "web": [
                    {
                        "title": "A",
                        "url": "https://example.com/page",
                        "description": "d1"
                    },
                    # Trailing slash — same normalised key as the first hit.
                    {
                        "title": "B",
                        "url": "https://example.com/page/",
                        "description": "d2"
                    },
                    # No URL — must be skipped.
                    {
                        "title": "C",
                        "url": "",
                        "description": "d3"
                    },
                ],
            },
        })
        client = _make_mock_sse_client({"/mcp": _youcom_sse_body(text)})
        t = WebSearchTool(
            provider="youcom",
            http_client=client,
            base_url="https://api.you.com/mcp?profile=free",
        )
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        urls = [h["url"] for h in res["results"]]
        assert urls == ["https://example.com/page"]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_jsonrpc_error_surfaced_as_summary(self):
        error = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {
                "code": -32000,
                "message": "rate limited",
            },
        }
        body = f"event: message\ndata: {json.dumps(error)}\n\n"
        client = _make_mock_sse_client({"/mcp": body})
        t = WebSearchTool(
            provider="youcom",
            http_client=client,
            base_url="https://api.you.com/mcp?profile=free",
        )
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        assert res["results"] == []
        assert "rate limited" in res["summary"]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_tool_iserror_surfaced_as_summary(self):
        result = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "isError": True,
                "content": [{
                    "type": "text",
                    "text": "upstream provider unavailable",
                }],
            },
        }
        body = f"event: message\ndata: {json.dumps(result)}\n\n"
        client = _make_mock_sse_client({"/mcp": body})
        t = WebSearchTool(
            provider="youcom",
            http_client=client,
            base_url="https://api.you.com/mcp?profile=free",
        )
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        assert res["results"] == []
        assert "upstream provider unavailable" in res["summary"]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_no_result_payload_returns_summary(self):
        # SSE body carrying only a notification — no JSON-RPC result line.
        notification = {"jsonrpc": "2.0", "method": "notifications/message", "params": {}}
        body = f"data: {json.dumps(notification)}\n\n"
        client = _make_mock_sse_client({"/mcp": body})
        t = WebSearchTool(
            provider="youcom",
            http_client=client,
            base_url="https://api.you.com/mcp?profile=free",
        )
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        assert res["results"] == []
        assert res["summary"] == "You.com search returned no result payload."
        await client.aclose()

    @pytest.mark.asyncio
    async def test_non_json_tool_text_returns_summary(self):
        client = _make_mock_sse_client({"/mcp": _youcom_sse_body("<not json>")})
        t = WebSearchTool(
            provider="youcom",
            http_client=client,
            base_url="https://api.you.com/mcp?profile=free",
        )
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        assert res["results"] == []
        assert res["summary"] == "You.com search returned an unexpected payload."
        await client.aclose()

    @pytest.mark.asyncio
    async def test_highlights_malformed_types_do_not_break_search(self):
        text = json.dumps({
            "results": {
                "web": [
                    {
                        "title": "A",
                        "url": "https://example.com/a",
                        "description": "",
                        # A bare string instead of a list: no crash and no
                        # single-character snippet.
                        "contents": {
                            "highlights": "snippet text"
                        },
                    },
                    {
                        "title": "B",
                        "url": "https://example.com/b",
                        "description": "",
                        # A dict instead of a list: must not escape to the
                        # broad except in _run_async_impl as SEARCH_ERROR.
                        "contents": {
                            "highlights": {
                                "0": "snippet text"
                            }
                        },
                    },
                    {
                        "title": "C",
                        "url": "https://example.com/c",
                        "description": "",
                        # First element not a string: not a usable fallback.
                        "contents": {
                            "highlights": [42]
                        },
                    },
                ],
            },
        })
        client = _make_mock_sse_client({"/mcp": _youcom_sse_body(text)})
        t = WebSearchTool(
            provider="youcom",
            http_client=client,
            base_url="https://api.you.com/mcp?profile=free",
        )
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        assert "error" not in res
        assert [h["url"] for h in res["results"]] == [
            "https://example.com/a",
            "https://example.com/b",
            "https://example.com/c",
        ]
        assert [h["snippet"] for h in res["results"]] == ["", "", ""]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_tool_iserror_without_text_reports_error(self):
        # isError with no text content used to fall through to payload
        # parsing and report a misleading "unexpected payload" success.
        result = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "isError": True,
                "content": [],
                "structuredContent": {
                    "code": -32000,
                    "message": "quota exceeded"
                },
            },
        }
        body = f"event: message\ndata: {json.dumps(result)}\n\n"
        client = _make_mock_sse_client({"/mcp": body})
        t = WebSearchTool(
            provider="youcom",
            http_client=client,
            base_url="https://api.you.com/mcp?profile=free",
        )
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        assert res["results"] == []
        assert res["summary"].startswith("You.com search error")
        assert "quota exceeded" in res["summary"]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_structured_content_used_when_text_is_not_json(self):
        result = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{
                    "type": "text",
                    "text": "Search finished; see the structured results.",
                }],
                "structuredContent": {
                    "results": {
                        "web": [{
                            "title": "Python",
                            "url": "https://www.python.org",
                            "description": "Welcome to Python.org",
                        }],
                    },
                },
            },
        }
        body = f"event: message\ndata: {json.dumps(result)}\n\n"
        client = _make_mock_sse_client({"/mcp": body})
        t = WebSearchTool(
            provider="youcom",
            http_client=client,
            base_url="https://api.you.com/mcp?profile=free",
        )
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        assert [h["url"] for h in res["results"]] == ["https://www.python.org"]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_stray_error_superseded_by_later_result(self):
        # An error frame followed by a valid result frame used to discard
        # the result because the error had unconditional priority.
        error = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "transient"}}
        result = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{
                    "type": "text",
                    "text": _YOUCOM_TOOL_TEXT
                }]
            },
        }
        body = (f"event: message\ndata: {json.dumps(error)}\n\n"
                f"event: message\ndata: {json.dumps(result)}\n\n")
        client = _make_mock_sse_client({"/mcp": body})
        t = WebSearchTool(
            provider="youcom",
            http_client=client,
            base_url="https://api.you.com/mcp?profile=free",
        )
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        assert res["summary"] == ""
        assert len(res["results"]) == 3
        await client.aclose()

    @pytest.mark.asyncio
    async def test_same_frame_result_and_error_treated_as_error(self):
        frame = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": None,
            "error": {
                "code": -32000,
                "message": "rate limited"
            },
        }
        body = f"event: message\ndata: {json.dumps(frame)}\n\n"
        client = _make_mock_sse_client({"/mcp": body})
        t = WebSearchTool(
            provider="youcom",
            http_client=client,
            base_url="https://api.you.com/mcp?profile=free",
        )
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        assert res["results"] == []
        assert "rate limited" in res["summary"]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_frames_for_other_request_ids_ignored(self):
        other = {
            "jsonrpc": "2.0",
            "id": 2,
            "error": {
                "code": -32000,
                "message": "someone else's failure"
            },
        }
        result = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{
                    "type": "text",
                    "text": _YOUCOM_TOOL_TEXT
                }]
            },
        }
        body = (f"event: message\ndata: {json.dumps(other)}\n\n"
                f"event: message\ndata: {json.dumps(result)}\n\n")
        client = _make_mock_sse_client({"/mcp": body})
        t = WebSearchTool(
            provider="youcom",
            http_client=client,
            base_url="https://api.you.com/mcp?profile=free",
        )
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        assert res["summary"] == ""
        assert len(res["results"]) == 3
        await client.aclose()

    @pytest.mark.asyncio
    async def test_lang_operator_prefix_not_duplicated(self):
        client = _make_mock_sse_client({"/mcp": _youcom_sse_body(_YOUCOM_TOOL_TEXT)})
        t = WebSearchTool(
            provider="youcom",
            http_client=client,
            base_url="https://api.you.com/mcp?profile=free",
        )
        await t._run_async_impl(
            tool_context=_tool_ctx(),
            args={
                "query": "python release",
                "lang": "lang:ja"
            },
        )
        payload = json.loads(client._captured["last_request"].content)
        assert payload["params"]["arguments"]["query"] == "python release lang:ja"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_youcom_extra_params_cannot_override_call_arguments(self):
        client = _make_mock_sse_client({"/mcp": _youcom_sse_body(_YOUCOM_TOOL_TEXT)})
        t = WebSearchTool(
            provider="youcom",
            http_client=client,
            base_url="https://api.you.com/mcp?profile=free",
            youcom_extra_params={
                "freshness": "week",
                "query": "pinned query",
                "count": 99
            },
        )
        await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        arguments = json.loads(client._captured["last_request"].content)["params"]["arguments"]
        assert arguments["query"] == "python"
        assert arguments["count"] == 5  # default results_num
        assert arguments["freshness"] == "week"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_non_dict_results_shape_surfaced_as_summary(self):
        # A non-empty non-dict ``results`` is an upstream shape change, not
        # an empty search; it used to degrade to a silent 0-hit success.
        text = json.dumps({"results": [{"url": "https://example.com", "title": "flat"}]})
        client = _make_mock_sse_client({"/mcp": _youcom_sse_body(text)})
        t = WebSearchTool(
            provider="youcom",
            http_client=client,
            base_url="https://api.you.com/mcp?profile=free",
        )
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        assert res["results"] == []
        assert res["summary"] == "You.com search returned an unexpected results shape."
        await client.aclose()

    @pytest.mark.asyncio
    async def test_non_list_sections_skipped_without_breaking_search(self):
        text = json.dumps({
            "results": {
                "web": {
                    "url": "https://example.com",
                    "title": "not a list"
                },
                "news": [{
                    "title": "Python 3.13.1 released",
                    "url": "https://blog.python.org/python-3-13-1-released",
                    "description": "The first maintenance release of Python 3.13.",
                }],
            },
        })
        client = _make_mock_sse_client({"/mcp": _youcom_sse_body(text)})
        t = WebSearchTool(
            provider="youcom",
            http_client=client,
            base_url="https://api.you.com/mcp?profile=free",
        )
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        assert "error" not in res
        assert [h["url"] for h in res["results"]] == ["https://blog.python.org/python-3-13-1-released"]
        await client.aclose()

    def test_parse_sse_payloads_skips_garbage_lines(self):
        text = ('data: not-json\n\n'
                'event: message\n'
                'data: {"result": {"x": 1}}\n\n')
        payloads = _parse_sse_payloads(text)
        assert payloads == [{"result": {"x": 1}}]

    def test_parse_sse_payloads_joins_multi_line_data_frames(self):
        # Per the SSE spec one event's data may span several data: lines;
        # each fragment alone fails to parse, the join must not.
        text = 'data: {"result": {"x":\ndata: 1}}\n\n'
        payloads = _parse_sse_payloads(text)
        assert payloads == [{"result": {"x": 1}}]

    def test_parse_sse_payloads_falls_back_to_single_lines_without_separators(self):
        # Servers that omit blank-line event separators: each line is
        # still tried on its own so complete frames are not dropped.
        text = 'data: {"a": 1}\ndata: {"b": 2}\n'
        payloads = _parse_sse_payloads(text)
        assert payloads == [{"a": 1}, {"b": 2}]

    def test_parse_sse_payloads_strips_leading_bom(self):
        text = '﻿data: {"result": {"x": 1}}\n\n'
        payloads = _parse_sse_payloads(text)
        assert payloads == [{"result": {"x": 1}}]


class TestHttpErrorHandling:

    @pytest.mark.asyncio
    async def test_http_error_returns_structured_error(self):

        def boom(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="server on fire")

        client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
        t = WebSearchTool(http_client=client)
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        assert "error" in res
        assert "HTTP_ERROR" in res["error"]
        assert res["provider"] == "duckduckgo"
        await client.aclose()


class TestInjectedHttpClient:

    @pytest.mark.asyncio
    async def test_injected_client_still_uses_tool_user_agent(self):
        """A pre-built ``http_client`` must NOT bypass the tool's User-Agent.

        The injected client owns proxy + connection pool, but the
        per-request ``User-Agent`` (and timeout) must come from the tool
        so that callers can't accidentally fingerprint the wrong identity
        just by sharing a client.
        """
        client = _make_mock_client({"/": _MIN_DDG_RESPONSE})
        t = WebSearchTool(http_client=client, user_agent="custom-ua/9.9")
        await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        req = client._captured["last_request"]
        assert req.headers.get("user-agent") == "custom-ua/9.9"
        await client.aclose()


class TestProcessRequest:

    @pytest.mark.asyncio
    async def test_registers_declaration_and_instructions(self):
        tool = WebSearchTool()
        req = LlmRequest()
        await tool.process_request(tool_context=_tool_ctx(), llm_request=req)

        assert tool.name in req.tools_dict
        assert req.config is not None
        assert req.config.system_instruction
        si = str(req.config.system_instruction)
        assert _TOOL_NAME in si
        assert "Sources:" in si
        # Current month/year string should be embedded.
        assert _current_month_year() in si


class TestHelpers:

    def test_truncate_short(self):
        assert _truncate("abc", 10) == "abc"

    def test_truncate_long(self):
        assert _truncate("x" * 50, 10) == "xxxxxxxxxx..."

    def test_extract_domain_from_url(self):
        assert _extract_domain_from_url("https://www.python.org/x") == "python.org"
        assert _extract_domain_from_url("http://docs.python.org/x") == "docs.python.org"
        assert _extract_domain_from_url("") == ""

    def test_extract_domain_from_url_strips_port_and_userinfo(self):
        # Explicit ports used to leak into the host via the old regex
        # (``example.com:8080``), which made allow/block matching silently
        # fail. ``urlparse`` correctly strips them.
        assert _extract_domain_from_url("https://example.com:8080/x") == "example.com"
        assert _extract_domain_from_url("https://user:pass@example.com/x") == "example.com"
        # Mixed case normalises to lower.
        assert _extract_domain_from_url("HTTPS://WWW.Example.COM/x") == "example.com"

    def test_extract_domain_from_url_non_http_schemes_fail_closed(self):
        for bad in ("javascript:alert(1)", "ftp://foo/bar", "mailto:a@b.c", "not a url"):
            assert _extract_domain_from_url(bad) == ""

    def test_dedup_key_normalises_common_variants(self):
        # Trailing slash, www., uppercase host all collapse to the same key.
        a = _dedup_key("https://www.Example.com/path/")
        b = _dedup_key("https://example.com/path")
        assert a == b
        # Fragments are ignored.
        assert _dedup_key("https://x.com/y#section") == _dedup_key("https://x.com/y")
        # Path case is preserved (case can be significant on some servers).
        assert _dedup_key("https://x.com/A") != _dedup_key("https://x.com/a")
        # Different schemes are NOT merged (http vs https are different
        # destinations from a security/content standpoint).
        assert _dedup_key("http://x.com/y") != _dedup_key("https://x.com/y")
        # Unparseable URLs fall through to their stripped form.
        assert _dedup_key("  ") == ""
        assert _dedup_key("not a url") == "not a url"
        # Query strings ARE part of the identity — a sitemap vs a search
        # results page on the same path must not dedupe.
        assert _dedup_key("https://x.com/s?q=1") != _dedup_key("https://x.com/s?q=2")
        # Default ports collapse with their bare form.
        assert _dedup_key("https://x.com:443/y") == _dedup_key("https://x.com/y")
        assert _dedup_key("http://x.com:80/y") == _dedup_key("http://x.com/y")
        # Non-default ports are preserved (different service).
        assert _dedup_key("https://x.com:8443/y") != _dedup_key("https://x.com/y")

    def test_is_blocked_with_blocklist(self):
        assert _is_blocked("https://docs.python.org/x", None, ["python.org"])
        assert _is_blocked("https://python.org/x", None, ["python.org"])
        assert not _is_blocked("https://example.com/x", None, ["python.org"])

    def test_is_blocked_with_allowlist(self):
        assert not _is_blocked("https://python.org/x", ["python.org"], None)
        assert _is_blocked("https://example.com/x", ["python.org"], None)

    def test_is_blocked_no_lists_passes(self):
        assert not _is_blocked("https://x.com", None, None)

    def test_is_blocked_invalid_url_fails_closed(self):
        # URLs we can't parse a host out of should always be treated as blocked,
        # so they don't slip past allow/block filters into the hit list.
        for bad in ("", "not a url", "javascript:alert(1)", "ftp://foo/bar", "mailto:a@b.c"):
            assert _is_blocked(bad, None, None) is True
            assert _is_blocked(bad, ["example.com"], None) is True
            assert _is_blocked(bad, None, ["example.com"]) is True

    def test_extract_title_from_ddg_topic(self):
        assert _extract_title_from_ddg_topic("Python - the language") == "Python"
        assert _extract_title_from_ddg_topic("") == ""
        long_title = "X" * 200
        out = _extract_title_from_ddg_topic(long_title)
        assert out.endswith("...")

    def test_extract_desc_from_pagemap_ok(self):
        pagemap = {
            "metatags": [
                {
                    "description": "first"
                },
                {
                    "og:description": "second"
                },
                {},
            ]
        }
        assert _extract_desc_from_pagemap(pagemap) == "first\nsecond"

    def test_extract_desc_from_pagemap_bad_shapes(self):
        assert _extract_desc_from_pagemap({}) == ""
        assert _extract_desc_from_pagemap({"metatags": "not-a-list"}) == ""
        assert _extract_desc_from_pagemap({"metatags": [None, 42]}) == ""


class TestOutputShape:

    @pytest.mark.asyncio
    async def test_output_is_jsonable_dict(self):
        client = _make_mock_client({"/": _FULL_DDG_RESPONSE})
        t = WebSearchTool(http_client=client)
        res = await t._run_async_impl(tool_context=_tool_ctx(), args={"query": "python"})
        # Must be JSON-serialisable so the framework can place it in tool_result.
        dumped = json.dumps(res)
        parsed = json.loads(dumped)
        assert parsed["query"] == "python"
        assert isinstance(parsed["results"], list)
        await client.aclose()
