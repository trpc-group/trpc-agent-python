# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# This file is part of tRPC-Agent-Python and is licensed under Apache-2.0.
#
# Portions of this file are derived from HKUDS/nanobot (MIT License):
# https://github.com/HKUDS/nanobot.git
#
# Copyright (c) 2025 nanobot contributors
#
# See the project LICENSE / third-party attribution notices for details.
#
"""Web tools: web_search and web_fetch."""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
from typing import Any
from typing import List
from typing import Optional
from urllib.parse import urlparse

import httpx
from nanobot.config.schema import WebSearchConfig
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"
MAX_REDIRECTS = 5

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _strip_tags(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r'<script[\s\S]*?</script>', '', text, flags=re.I)
    text = re.sub(r'<style[\s\S]*?</style>', '', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    return html.unescape(text).strip()


def _normalize(text: str) -> str:
    """Normalize whitespace."""
    text = re.sub(r'[ \t]+', ' ', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _validate_url(url: str) -> tuple[bool, str]:
    """Return (True, '') or (False, reason) for an http(s) URL."""
    try:
        p = urlparse(url)
        if p.scheme not in ('http', 'https'):
            return False, f"Only http/https allowed, got '{p.scheme or 'none'}'"
        if not p.netloc:
            return False, "Missing domain"
        return True, ""
    except Exception as e:
        return False, str(e)


def _format_results(query: str, items: list[dict[str, Any]], n: int) -> str:
    """Format provider results into shared plaintext output."""
    if not items:
        return f"No results for: {query}"
    lines = [f"Results for: {query}\n"]
    for i, item in enumerate(items[:n], 1):
        title = _normalize(_strip_tags(item.get("title", "")))
        snippet = _normalize(_strip_tags(item.get("content", "")))
        lines.append(f"{i}. {title}\n   {item.get('url', '')}")
        if snippet:
            lines.append(f"   {snippet}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# WebSearchTool
# ---------------------------------------------------------------------------


class WebSearchTool(BaseTool):
    """trpc_agent_sdk tool to search the web using a configured provider.

    Args:
        config:       :class:`WebSearchConfig` instance; defaults to a fresh
                      one if *None*.
        proxy:        Optional HTTP proxy URL forwarded to httpx.
        filters_name: Filter names forwarded to
                      :class:`~trpc_agent_sdk.tools.BaseTool`.
        filters:      Filter instances forwarded to
                      :class:`~trpc_agent_sdk.tools.BaseTool`.
    """

    def __init__(
        self,
        config: Optional["WebSearchConfig"] = None,
        proxy: Optional[str] = None,
        filters_name: Optional[List[str]] = None,
        filters: Optional[List[BaseFilter]] = None,
    ) -> None:
        super().__init__(
            name="web_search",
            description="Search the web. Returns titles, URLs, and snippets.",
            filters_name=filters_name,
            filters=filters,
        )
        self._config = config if config is not None else WebSearchConfig()
        self._proxy = proxy

    # ------------------------------------------------------------------
    # BaseTool interface
    # ------------------------------------------------------------------

    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            name="web_search",
            description="Search the web. Returns titles, URLs, and snippets.",
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "query":
                    Schema(type=Type.STRING, description="Search query"),
                    "count":
                    Schema(
                        type=Type.INTEGER,
                        description="Number of results to return (1-10)",
                        minimum=1,
                        maximum=10,
                    ),
                },
                required=["query"],
            ),
        )

    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        query: str = args.get("query", "")
        count: Optional[int] = args.get("count")
        provider = getattr(self._config, "provider", "brave").strip().lower()
        n = min(max(count or self._config.max_results, 1), 10)

        if provider == "duckduckgo":
            return await self._search_duckduckgo(query, n)
        if provider == "tavily":
            return await self._search_tavily(query, n)
        if provider == "searxng":
            return await self._search_searxng(query, n)
        if provider == "jina":
            return await self._search_jina(query, n)
        if provider == "brave":
            return await self._search_brave(query, n)
        return f"Error: unknown search provider '{provider}'"

    # ------------------------------------------------------------------
    # Private provider backends
    # ------------------------------------------------------------------

    async def _search_brave(self, query: str, n: int) -> str:
        api_key = self._config.api_key or os.environ.get("BRAVE_API_KEY", "")
        if not api_key:
            logger.warning("BRAVE_API_KEY not set, falling back to DuckDuckGo")
            return await self._search_duckduckgo(query, n)
        try:
            async with httpx.AsyncClient(proxy=self._proxy) as client:
                r = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={
                        "q": query,
                        "count": n
                    },
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": api_key
                    },
                    timeout=10.0,
                )
                r.raise_for_status()
            items = [{
                "title": x.get("title", ""),
                "url": x.get("url", ""),
                "content": x.get("description", "")
            } for x in r.json().get("web", {}).get("results", [])]
            return _format_results(query, items, n)
        except Exception as e:  # pylint: disable=broad-except
            return f"Error: {e}"

    async def _search_tavily(self, query: str, n: int) -> str:
        api_key = self._config.api_key or os.environ.get("TAVILY_API_KEY", "")
        if not api_key:
            logger.warning("TAVILY_API_KEY not set, falling back to DuckDuckGo")
            return await self._search_duckduckgo(query, n)
        try:
            async with httpx.AsyncClient(proxy=self._proxy) as client:
                r = await client.post(
                    "https://api.tavily.com/search",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "query": query,
                        "max_results": n
                    },
                    timeout=15.0,
                )
                r.raise_for_status()
            return _format_results(query, r.json().get("results", []), n)
        except Exception as e:  # pylint: disable=broad-except
            return f"Error: {e}"

    async def _search_searxng(self, query: str, n: int) -> str:
        base_url = (self._config.base_url or os.environ.get("SEARXNG_BASE_URL", "")).strip()
        if not base_url:
            logger.warning("SEARXNG_BASE_URL not set, falling back to DuckDuckGo")
            return await self._search_duckduckgo(query, n)
        endpoint = f"{base_url.rstrip('/')}/search"
        is_valid, error_msg = _validate_url(endpoint)
        if not is_valid:
            return f"Error: invalid SearXNG URL: {error_msg}"
        try:
            async with httpx.AsyncClient(proxy=self._proxy) as client:
                r = await client.get(
                    endpoint,
                    params={
                        "q": query,
                        "format": "json"
                    },
                    headers={"User-Agent": USER_AGENT},
                    timeout=10.0,
                )
                r.raise_for_status()
            return _format_results(query, r.json().get("results", []), n)
        except Exception as e:  # pylint: disable=broad-except
            return f"Error: {e}"

    async def _search_jina(self, query: str, n: int) -> str:
        api_key = self._config.api_key or os.environ.get("JINA_API_KEY", "")
        if not api_key:
            logger.warning("JINA_API_KEY not set, falling back to DuckDuckGo")
            return await self._search_duckduckgo(query, n)
        try:
            headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
            async with httpx.AsyncClient(proxy=self._proxy) as client:
                r = await client.get(
                    "https://s.jina.ai/",
                    params={"q": query},
                    headers=headers,
                    timeout=15.0,
                )
                r.raise_for_status()
            data = r.json().get("data", [])[:n]
            items = [{
                "title": d.get("title", ""),
                "url": d.get("url", ""),
                "content": d.get("content", "")[:500]
            } for d in data]
            return _format_results(query, items, n)
        except Exception as e:  # pylint: disable=broad-except
            return f"Error: {e}"

    async def _search_duckduckgo(self, query: str, n: int) -> str:
        try:
            from ddgs import DDGS
            ddgs = DDGS(timeout=10)
            raw = await asyncio.to_thread(ddgs.text, query, max_results=n)
            if not raw:
                return f"No results for: {query}"
            items = [{"title": r.get("title", ""), "url": r.get("href", ""), "content": r.get("body", "")} for r in raw]
            return _format_results(query, items, n)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("DuckDuckGo search failed: %s", e)
            return f"Error: DuckDuckGo search failed ({e})"


# ---------------------------------------------------------------------------
# WebFetchTool
# ---------------------------------------------------------------------------


class WebFetchTool(BaseTool):
    """trpc_agent_sdk tool to fetch and extract readable content from a URL.

    Args:
        max_chars:    Character limit for the returned text.
        proxy:        Optional HTTP proxy URL forwarded to httpx.
        filters_name: Filter names forwarded to
                      :class:`~trpc_agent_sdk.tools.BaseTool`.
        filters:      Filter instances forwarded to
                      :class:`~trpc_agent_sdk.tools.BaseTool`.
    """

    def __init__(
        self,
        max_chars: int = 50_000,
        proxy: Optional[str] = None,
        filters_name: Optional[List[str]] = None,
        filters: Optional[List[BaseFilter]] = None,
    ) -> None:
        super().__init__(
            name="web_fetch",
            description="Fetch URL and extract readable content (HTML → markdown/text).",
            filters_name=filters_name,
            filters=filters,
        )
        self._max_chars = max_chars
        self._proxy = proxy

    # ------------------------------------------------------------------
    # BaseTool interface
    # ------------------------------------------------------------------

    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            name="web_fetch",
            description="Fetch URL and extract readable content (HTML → markdown/text).",
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "url":
                    Schema(type=Type.STRING, description="URL to fetch"),
                    "extractMode":
                    Schema(
                        type=Type.STRING,
                        enum=["markdown", "text"],
                        description="Extraction mode (default: markdown)",
                    ),
                    "maxChars":
                    Schema(
                        type=Type.INTEGER,
                        description="Maximum characters to return",
                        minimum=100,
                    ),
                },
                required=["url"],
            ),
        )

    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        url: str = args.get("url", "")
        extract_mode: str = args.get("extractMode", "markdown")
        max_chars: int = int(args.get("maxChars") or self._max_chars)

        is_valid, error_msg = _validate_url(url)
        if not is_valid:
            return json.dumps({"error": f"URL validation failed: {error_msg}", "url": url}, ensure_ascii=False)

        result = await self._fetch_jina(url, max_chars)
        if result is None:
            result = await self._fetch_readability(url, extract_mode, max_chars)
        return result

    # ------------------------------------------------------------------
    # Private fetch backends
    # ------------------------------------------------------------------

    async def _fetch_jina(self, url: str, max_chars: int) -> Optional[str]:
        """Try Jina Reader API; return None on failure."""
        try:
            headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
            jina_key = os.environ.get("JINA_API_KEY", "")
            if jina_key:
                headers["Authorization"] = f"Bearer {jina_key}"
            async with httpx.AsyncClient(proxy=self._proxy, timeout=20.0) as client:
                r = await client.get(f"https://r.jina.ai/{url}", headers=headers)
                if r.status_code == 429:
                    logger.debug("Jina Reader rate limited, falling back to readability")
                    return None
                r.raise_for_status()

            data = r.json().get("data", {})
            text = data.get("content", "")
            if not text:
                return None
            title = data.get("title", "")
            if title:
                text = f"# {title}\n\n{text}"
            truncated = len(text) > max_chars
            if truncated:
                text = text[:max_chars]
            return json.dumps(
                {
                    "url": url,
                    "finalUrl": data.get("url", url),
                    "status": r.status_code,
                    "extractor": "jina",
                    "truncated": truncated,
                    "length": len(text),
                    "text": text,
                },
                ensure_ascii=False)
        except Exception as e:  # pylint: disable=broad-except
            logger.debug("Jina Reader failed for %s, falling back to readability: %s", url, e)
            return None

    async def _fetch_readability(self, url: str, extract_mode: str, max_chars: int) -> str:
        """Local fallback using readability-lxml."""
        from readability import Document
        try:
            async with httpx.AsyncClient(
                    follow_redirects=True,
                    max_redirects=MAX_REDIRECTS,
                    timeout=30.0,
                    proxy=self._proxy,
            ) as client:
                r = await client.get(url, headers={"User-Agent": USER_AGENT})
                r.raise_for_status()

            ctype = r.headers.get("content-type", "")
            if "application/json" in ctype:
                text, extractor = json.dumps(r.json(), indent=2, ensure_ascii=False), "json"
            elif "text/html" in ctype or r.text[:256].lower().startswith(("<!doctype", "<html")):
                doc = Document(r.text)
                content = self._to_markdown(doc.summary()) if extract_mode == "markdown" else _strip_tags(doc.summary())
                text = f"# {doc.title()}\n\n{content}" if doc.title() else content
                extractor = "readability"
            else:
                text, extractor = r.text, "raw"

            truncated = len(text) > max_chars
            if truncated:
                text = text[:max_chars]
            return json.dumps(
                {
                    "url": url,
                    "finalUrl": str(r.url),
                    "status": r.status_code,
                    "extractor": extractor,
                    "truncated": truncated,
                    "length": len(text),
                    "text": text,
                },
                ensure_ascii=False)
        except httpx.ProxyError as e:
            logger.error("WebFetch proxy error for %s: %s", url, e)
            return json.dumps({"error": f"Proxy error: {e}", "url": url}, ensure_ascii=False)
        except Exception as e:  # pylint: disable=broad-except
            logger.error("WebFetch error for %s: %s", url, e)
            return json.dumps({"error": str(e), "url": url}, ensure_ascii=False)

    def _to_markdown(self, html_content: str) -> str:
        """Convert HTML to markdown."""
        text = re.sub(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>',
                      lambda m: f'[{_strip_tags(m[2])}]({m[1]})',
                      html_content,
                      flags=re.I)
        text = re.sub(r'<h([1-6])[^>]*>([\s\S]*?)</h\1>',
                      lambda m: f'\n{"#" * int(m[1])} {_strip_tags(m[2])}\n',
                      text,
                      flags=re.I)
        text = re.sub(r'<li[^>]*>([\s\S]*?)</li>', lambda m: f'\n- {_strip_tags(m[1])}', text, flags=re.I)
        text = re.sub(r'</(p|div|section|article)>', '\n\n', text, flags=re.I)
        text = re.sub(r'<(br|hr)\s*/?>', '\n', text, flags=re.I)
        return _normalize(_strip_tags(text))
