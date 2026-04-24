# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Web fetch tool for TRPC Agent framework.

Provides a client-side :class:`WebFetchTool` that:

- issues an unauthenticated HTTP GET against a single URL,
- converts the response to text (Markdown for HTML; verbatim for
  textual MIME types),
- optionally caches raw fetch results in a TTL + byte-bounded LRU so
  repeated hits skip the network.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import time
from collections import OrderedDict
from typing import Any
from typing import List
from typing import Optional
from typing import Tuple
from urllib.parse import urlparse
from typing_extensions import override

import httpx
from markdownify import markdownify as _markdownify  # type: ignore[import-untyped]
from pydantic import BaseModel
from pydantic import Field

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

from ._base_tool import BaseTool

# Default HTTP timeout in seconds.
_DEFAULT_TIMEOUT = 30.0
# Default cap on returned content in chars (~25K tokens worst-case).
_DEFAULT_MAX_CONTENT_LENGTH = 100_000
# Default upper bound on redirects when ``follow_redirects`` is on.
_DEFAULT_MAX_REDIRECTS = 5
# Default HTTP User-Agent string.
_DEFAULT_USER_AGENT = "trpc-agent-python-webfetch/1.0"
# Hard byte cap on the raw response body read from the wire.
_DEFAULT_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
# Number of prefix bytes inspected when sniffing a response that lacks a Content-Type header.
_BINARY_SNIFF_BYTES = 4096
# Cache defaults (15 minutes / 50 MiB)
_DEFAULT_CACHE_TTL_SECONDS = 15.0 * 60
_DEFAULT_CACHE_MAX_BYTES = 50 * 1024 * 1024
# MIME types we render as Markdown-ish text.
_HTML_TYPES = frozenset(["text/html", "application/xhtml+xml"])
# MIME types we return verbatim. Any unknown ``text/*`` MIME is accepted
_TEXT_TYPES = frozenset([
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/xml",
    "text/css",
    "text/javascript",
    "text/rtf",
    "application/json",
    "application/xml",
    "application/yaml",
    "application/x-yaml",
    "application/ld+json",
])
# WebFetchTool description shown to the LLM
_BASE_DESCRIPTION = """\
Fetch a single URL via HTTP GET and return its textual content.
- HTML pages are stripped to Markdown-ish plain text; other textual MIME types are returned verbatim.
- Content is capped to a finite length (override via `max_length`) so large pages do not overflow the context window.
- Binary content (PDF / image / archive / ...) is rejected with a structured error; use a dedicated tool for it.
- The URL must be an absolute http(s) URL. The tool's domain allow/block lists are enforced before the request.
- By default the tool refuses to dial loopback / private / link-local targets
  (e.g. 127.0.0.1, 169.254.169.254, intranet IPs) so it cannot be abused as an SSRF probe.
- This tool is read-only and does not modify any files or remote state.

USE WHEN the user asks to:
  - read, summarise, quote or extract a fact from a specific webpage, doc page, RFC, changelog, or news article
  - follow a link returned by `websearch` for a deeper read

DO NOT USE WHEN:
  - an MCP-provided web fetch tool is available — prefer it, as it may have fewer restrictions and richer auth
  - the URL requires authentication or session cookies (Google Docs, Confluence, Jira, private GitHub, intranet)
    — prefer a dedicated MCP/authenticated tool
  - you need to render JavaScript, submit forms, or perform interactive browsing (this tool only issues one GET)
  - the content is binary — the tool rejects non-textual responses

Usage notes:
  - Always pass the fully-qualified URL including scheme (``http://`` / ``https://``).
  - When the returned ``content`` is truncated, summarise faithfully rather than invent missing sections.
  - Includes a self-cleaning 15-minute cache for faster responses when repeatedly accessing the same URL.
  \
"""


class FetchResult(BaseModel):
    """Structured output of :class:`WebFetchTool`."""

    url: str = Field(default="", description="Final URL after any redirects")
    status_code: int = Field(default=0, description="HTTP status code (0 when the request never completed)")
    status_text: str = Field(default="", description="HTTP reason phrase")
    content_type: str = Field(default="", description="Normalised media type (no parameters)")
    content: str = Field(default="", description="Textual body, possibly truncated")
    bytes: int = Field(default=0, description="UTF-8 byte length of the returned ``content``")
    duration_ms: int = Field(default=0, description="Wall-clock time spent on the request")
    cached: bool = Field(default=False, description="True when served from the in-process LRU cache")
    error: str = Field(default="", description="Populated when the fetch failed or content was rejected")


def _normalise_content_type(raw: str) -> str:
    """Return the bare media type from a ``Content-Type`` header."""
    if not raw:
        return ""
    return raw.split(";", 1)[0].strip().lower()


def _is_html_type(media_type: str) -> bool:
    return media_type in _HTML_TYPES


def _is_text_type(media_type: str) -> bool:
    """Accept known-textual types plus any ``text/*`` fallback."""
    if media_type in _TEXT_TYPES:
        return True
    return media_type.startswith("text/")


def _extract_host(url: str) -> str:
    """Return the lower-cased host of an ``http(s)`` URL"""
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        logger.error("Failed to parse URL: %s", url)
        return ""
    if parsed.scheme.lower() not in ("http", "https"):
        return ""
    try:
        host = (parsed.hostname or "").lower()
    except ValueError:
        logger.error("Failed to parse hostname: %s", url)
        return ""
    return host.removeprefix("www.")


class _SSRFBlockedError(Exception):
    """Raised when a target URL resolves to a disallowed network."""
    pass


class _DomainBlockedError(Exception):
    """Raised when a target URL is rejected by the tool's domain allow/block policy."""
    pass


def _is_disallowed_ip(ip: "ipaddress._BaseAddress") -> bool:
    """Return ``True`` for addresses never want to dial."""
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
                or ip.is_unspecified)


async def _resolve_host(host: str) -> List[str]:
    """Resolve ``host`` to every A/AAAA address SSRF blocked"""
    if not host:
        return []
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.run_in_executor(
            None,
            lambda: socket.getaddrinfo(host, None, type=socket.SOCK_STREAM),
        )
    except (socket.gaierror, UnicodeError, OSError) as e:
        logger.debug("WebFetchTool DNS resolution failed for %s: %s", host, e)
        return []
    addrs: List[str] = []
    for info in infos:
        sockaddr = info[4] if len(info) > 4 else None
        if sockaddr and sockaddr[0]:
            addrs.append(sockaddr[0])
    return addrs


async def _assert_public_host(url: str) -> None:
    """Judge if ``url`` targets a non-public address."""
    host = _extract_host(url)
    if not host:
        raise _SSRFBlockedError("unparseable host")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if _is_disallowed_ip(ip):
            raise _SSRFBlockedError(f"{host} is a private/reserved address")
        return
    addrs = await _resolve_host(host)
    if not addrs:
        raise _SSRFBlockedError(f"{host!r} did not resolve")
    for raw in addrs:
        try:
            resolved = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if _is_disallowed_ip(resolved):
            raise _SSRFBlockedError(f"{host!r} resolves to private/reserved address {raw}")


def _is_blocked_url(
    url: str,
    allowed: Optional[List[str]],
    blocked: Optional[List[str]],
) -> bool:
    """Tool-level URL allow/block check. Returns ``True`` when blocked."""
    host = _extract_host(url)
    if not host:
        return True
    if blocked:
        for d in blocked:
            d = (d or "").lower().removeprefix("www.")
            if d and (host == d or host.endswith("." + d)):
                return True
    if allowed:
        is_allowed = False
        for d in allowed:
            d = (d or "").lower().removeprefix("www.")
            if d and (host == d or host.endswith("." + d)):
                is_allowed = True
                break
        if not is_allowed:
            return True
    return False


def _truncate(text: str, limit: int) -> str:
    """Truncate ``text`` to at most ``limit`` chars; ``0`` disables the cap."""
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _cache_key(url: str) -> str:
    """Normalise a URL for use as a cache key."""
    raw = (url or "").strip()
    if not raw:
        return raw
    try:
        p = urlparse(raw)
    except ValueError:
        return raw
    scheme = (p.scheme or "").lower()
    if scheme not in ("http", "https"):
        return raw
    host = (p.hostname or "").lower().removeprefix("www.")
    if not host:
        return raw
    default_port = 80 if scheme == "http" else 443
    netloc = host if p.port is None or p.port == default_port else f"{host}:{p.port}"
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    key = f"{scheme}://{netloc}{path}"
    if p.query:
        key = f"{key}?{p.query}"
    return key


_STRIPPED_BLOCK_RE = re.compile(
    r"<(script|style|noscript|template|svg)\b[^>]*>.*?</\1\s*>",
    re.DOTALL | re.IGNORECASE,
)


def _scrub_non_content_blocks(html: str) -> str:
    """Remove ``<script>`` / ``<style>`` / ``<noscript>`` / ``<template>`` /
    ``<svg>`` blocks including their inner text."""
    return _STRIPPED_BLOCK_RE.sub("", html)


def _normalise_blank_lines(text: str) -> str:
    """Collapse >1 consecutive blank lines and trim trailing whitespace."""
    out: List[str] = []
    blank = 0
    for line in text.splitlines():
        stripped = line.rstrip()
        if not stripped:
            blank += 1
            if blank <= 1:
                out.append("")
        else:
            blank = 0
            out.append(stripped)
    return "\n".join(out).strip()


def _html_to_text(html: str) -> str:
    """Convert HTML to Markdown"""
    cleaned = _scrub_non_content_blocks(html)
    try:
        text = _markdownify(cleaned, heading_style="ATX")
    except Exception as e:  # pylint: disable=broad-except
        logger.warning("WebFetchTool markdownify failed: %s", e)
        return ""
    return _normalise_blank_lines(text)


class _LRUCache:
    """Async-safe KV: URL -> ``FetchResult`` cache with TTL + byte budget."""

    def __init__(self, *, ttl_seconds: float, max_bytes: int) -> None:
        self._ttl = max(0.0, float(ttl_seconds))
        self._max_bytes = max(0, int(max_bytes))
        self._store: "OrderedDict[str, Tuple[float, int, FetchResult]]" = OrderedDict()
        self._current_bytes = 0
        self._lock = asyncio.Lock()

    @property
    def size_bytes(self) -> int:
        return self._current_bytes

    @property
    def entries(self) -> int:
        return len(self._store)

    async def get(self, key: str) -> Optional[FetchResult]:
        if not key or self._max_bytes <= 0:
            return None
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            inserted_at, size, value = entry
            if self._ttl > 0 and (time.monotonic() - inserted_at) > self._ttl:
                del self._store[key]
                self._current_bytes -= size
                return None
            self._store.move_to_end(key)
            return value.model_copy()

    async def put(self, key: str, value: FetchResult) -> None:
        if not key or self._max_bytes <= 0:
            return
        size = len(value.content.encode("utf-8", errors="ignore"))
        if size <= 0 or size > self._max_bytes:
            return
        async with self._lock:
            if key in self._store:
                _, old_size, _ = self._store.pop(key)
                self._current_bytes -= old_size
            while self._store and self._current_bytes + size > self._max_bytes:
                _, (_, evict_size, _) = self._store.popitem(last=False)
                self._current_bytes -= evict_size
            self._store[key] = (time.monotonic(), size, value.model_copy())
            self._current_bytes += size

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()
            self._current_bytes = 0


class WebFetchTool(BaseTool):
    """LLM tool that fetches a single URL and returns its textual content.

    The tool issues an unauthenticated HTTP GET, converts the response
    to text (Markdown-ish for HTML, verbatim for other textual types),
    enforces tool-level allow/block lists, and caps the returned content
    to protect the model context window. Binary responses are rejected
    with a structured error rather than dumped as base64 blobs.

    Optionally, an in-process LRU cache (``enable_cache``) stores raw
    ``FetchResult`` objects with a 15-minute TTL and a 50 MiB byte
    budget so repeated hits skip the network. A cached entry is marked
    with ``cached=True`` on the result so the caller can reason about
    freshness.

    Args:
        timeout: HTTP timeout in seconds.
        user_agent: HTTP ``User-Agent`` header.
        proxy: Optional HTTP proxy URL forwarded to httpx.
        http_client: Optional pre-built ``httpx.AsyncClient`` to reuse (caller owns its lifecycle).
        max_content_length: Default char cap on returned ``content``; ``0`` disables,
            overridden by per-call ``max_length``.
        allowed_domains: Tool-level host whitelist (subdomain-aware, ``www.`` stripped); NOT overrideable by the LLM.
        blocked_domains: Tool-level host blacklist using the same matching as ``allowed_domains``; checked first.
        follow_redirects: Whether httpx auto-follows redirects (default ``True``).
        max_redirects: Upper bound on redirect hops when ``follow_redirects=True``.
        enable_cache: Toggle the in-process LRU cache (default ``False``).
        cache_ttl_seconds: TTL for cached entries (default 15 min).
        cache_max_bytes: Total byte budget for the cache (default 50 MiB).
        max_response_bytes: Hard cap on the raw wire body streamed from the server (default 5 MiB, ``0`` disables).
        block_private_network: When ``True`` (default), reject every hop that resolves to loopback / private /
            link-local / multicast / reserved / unspecified addresses (SSRF guard).
        filters_name / filters: forwarded to :class:`BaseTool`.
    """

    def __init__(
        self,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        user_agent: str = _DEFAULT_USER_AGENT,
        proxy: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        max_content_length: int = _DEFAULT_MAX_CONTENT_LENGTH,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None,
        block_private_network: bool = True,
        follow_redirects: bool = True,
        max_redirects: int = _DEFAULT_MAX_REDIRECTS,
        enable_cache: bool = False,
        cache_ttl_seconds: float = _DEFAULT_CACHE_TTL_SECONDS,
        cache_max_bytes: int = _DEFAULT_CACHE_MAX_BYTES,
        filters_name: Optional[List[str]] = None,
        filters: Optional[List[BaseFilter]] = None,
    ) -> None:
        super().__init__(
            name="webfetch",
            description=_BASE_DESCRIPTION,
            filters_name=filters_name,
            filters=filters,
        )
        self._timeout = float(timeout)
        self._user_agent = user_agent
        self._proxy = proxy
        self._http_client = http_client
        self._max_content_length = max(0, int(max_content_length))
        self._max_response_bytes = max(0, int(max_response_bytes))
        self._allowed_domains = self._clean_domains(allowed_domains)
        self._blocked_domains = self._clean_domains(blocked_domains)
        self._block_private_network = bool(block_private_network)
        self._follow_redirects = bool(follow_redirects)
        self._max_redirects = max(0, int(max_redirects))

        # LRU cache
        self._enable_cache = bool(enable_cache)
        self._cache: Optional[_LRUCache] = None
        if self._enable_cache:
            self._cache = _LRUCache(
                ttl_seconds=cache_ttl_seconds,
                max_bytes=cache_max_bytes,
            )

    @staticmethod
    def _clean_domains(value: Optional[List[str]]) -> Optional[List[str]]:
        """Trim and drop blank entries. Returns ``None`` for empty lists."""
        if not value:
            return None
        cleaned = [d.strip() for d in value if isinstance(d, str) and d.strip()]
        return cleaned or None

    @property
    def cache(self) -> Optional[_LRUCache]:
        """Expose the cache for inspection / manual invalidation in tests."""
        return self._cache

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            name="webfetch",
            description=_BASE_DESCRIPTION,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "url":
                    Schema(
                        type=Type.STRING,
                        description=("Required. Absolute http(s) URL to fetch. Must include the scheme. "
                                     "Example: 'https://docs.python.org/3/whatsnew/3.13.html'."),
                    ),
                    "max_length":
                    Schema(
                        type=Type.INTEGER,
                        description=(f"Optional. Override the per-call cap on returned `content` length "
                                     f"in chars (0 disables the cap). Defaults to the tool-level value "
                                     f"({self._max_content_length}). Prefer smaller values when you only "
                                     f"need a short excerpt."),
                        minimum=0,
                    ),
                },
                required=["url"],
            ),
        )

    @override
    async def _run_async_impl(
        self,
        *,
        tool_context: InvocationContext,
        args: dict[str, Any],
    ) -> Any:
        raw_url = args.get("url")
        if not isinstance(raw_url, str) or not raw_url.strip():
            return {"error": "INVALID_ARGS: `url` must be a non-empty string"}
        url = raw_url.strip()

        parsed = urlparse(url)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
            return {"error": f"INVALID_URL: {url!r} must be an absolute http(s) URL"}

        if _is_blocked_url(url, self._allowed_domains, self._blocked_domains):
            return {
                "url": url,
                "error": (f"BLOCKED_URL: {parsed.hostname!r} is not permitted by "
                          "the tool's domain policy"),
            }

        # Resolve effective caps: LLM arg overrides tool default.
        cap = self._coerce_cap(args.get("max_length"))

        key = _cache_key(url)
        result: Optional[FetchResult] = None
        if self._cache is not None:
            cached_result = await self._cache.get(key)
            if cached_result is not None:
                cached_result.cached = True
                result = cached_result
                logger.info("WebFetchTool got cached result for %s in LRU cache", url)

        if result is None:
            result = await self._fetch_and_build(url)
            if self._cache is not None and not result.error and result.content:
                await self._cache.put(key, result)
                logger.info("WebFetchTool put result for %s in LRU cache", url)

        if not result.error:
            result.content = _truncate(result.content, cap)
            result.bytes = len(result.content.encode("utf-8", errors="ignore"))

        return result.model_dump()

    def _coerce_cap(self, raw: Any) -> int:
        """Resolve the effective ``max_length`` cap for this call."""
        try:
            cap = int(raw) if raw is not None else self._max_content_length
        except (TypeError, ValueError):
            cap = self._max_content_length
        return max(0, cap)

    async def _fetch_and_build(self, url: str) -> FetchResult:
        """Issue a streaming GET and map the response to :class:`FetchResult`."""
        start = time.monotonic()
        try:
            return await self._do_fetch(url, start)
        except _DomainBlockedError as e:
            return FetchResult(
                url=url,
                error=f"Domain_BLOCKED_URL: {e}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except _SSRFBlockedError as e:
            return FetchResult(
                url=url,
                error=f"SSRF_BLOCKED_URL: {e}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except httpx.HTTPError as e:
            logger.warning("WebFetchTool HTTP error for %s: %s", url, e)
            return FetchResult(
                url=url,
                error=f"HTTP_ERROR: {e}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as e:  # pylint: disable=broad-except
            logger.error("WebFetchTool unexpected error for %s: %s", url, e)
            return FetchResult(
                url=url,
                error=f"FETCH_ERROR: {e}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

    async def _do_fetch(self, url: str, start: float) -> FetchResult:
        """Drive the request + manual-redirect loop and build the result."""
        client = self._get_client()
        owns_client = self._http_client is None
        try:
            current_url = url
            hops = 0
            while True:
                if _is_blocked_url(current_url, self._allowed_domains, self._blocked_domains):
                    host = _extract_host(current_url) or current_url
                    raise _DomainBlockedError(f"{host!r} is not permitted by the tool's domain policy")
                await self._check_ssrf(current_url)
                async with client.stream(
                        "GET",
                        current_url,
                        timeout=self._timeout,
                        follow_redirects=False,
                        headers={"User-Agent": self._user_agent},
                ) as response:
                    is_redirect = 300 <= response.status_code < 400
                    location = response.headers.get("location", "") if is_redirect else ""
                    if self._follow_redirects and location:
                        hops += 1
                        if hops > self._max_redirects:
                            raise httpx.TooManyRedirects(
                                f"Exceeded {self._max_redirects} redirects while fetching {url}",
                                request=response.request,
                            )
                        current_url = str(response.url.join(location))
                        continue
                    return await self._build_from_response(response, start)
        finally:
            if owns_client:
                await client.aclose()

    async def _build_from_response(self, response: httpx.Response, start: float) -> FetchResult:
        """Drain ``response`` under the byte cap and shape a :class:`FetchResult`."""
        media_type = _normalise_content_type(response.headers.get("content-type", ""))
        result = FetchResult(
            url=str(response.url),
            status_code=response.status_code,
            status_text=response.reason_phrase or "",
            content_type=media_type,
            duration_ms=int((time.monotonic() - start) * 1000),
        )

        if response.status_code < 200 or response.status_code >= 300:
            result.error = f"HTTP_STATUS {response.status_code}: {response.reason_phrase or ''}".strip()
            return result

        # Reject non-text content-types
        is_html = _is_html_type(media_type)
        is_text = _is_text_type(media_type)
        if media_type and not is_html and not is_text:
            result.error = f"UNSUPPORTED_CONTENT_TYPE: {media_type}"
            return result

        body_bytes, aborted = await self._read_body(response)

        if not media_type and b"\x00" in body_bytes[:_BINARY_SNIFF_BYTES]:
            result.error = ("UNSUPPORTED_CONTENT_TYPE: response has no Content-Type "
                            "header and looks binary")
            return result

        result.duration_ms = int((time.monotonic() - start) * 1000)

        encoding = response.encoding or "utf-8"
        try:
            body = body_bytes.decode(encoding, errors="replace")
        except LookupError:
            body = body_bytes.decode("utf-8", errors="replace")
        except Exception as e:  # pylint: disable=broad-except
            result.error = f"DECODE_ERROR: {e}"
            return result

        content = _html_to_text(body) if is_html else body
        result.content = content
        result.bytes = len(content.encode("utf-8", errors="ignore"))
        if aborted:
            logger.info(
                "WebFetchTool truncated %s at %d bytes (exceeded max_response_bytes)",
                str(response.url),
                self._max_response_bytes,
            )
        return result

    async def _read_body(self, response: httpx.Response) -> Tuple[bytes, bool]:
        """Stream the response body up to :attr:`_max_response_bytes`."""
        limit = self._max_response_bytes
        buf = bytearray()
        aborted = False
        async for chunk in response.aiter_bytes():
            if not chunk:
                continue
            if limit <= 0:
                buf.extend(chunk)
                continue
            remaining = limit - len(buf)
            if remaining <= 0:
                aborted = True
                break
            if len(chunk) > remaining:
                buf.extend(chunk[:remaining])
                aborted = True
                break
            buf.extend(chunk)
        return bytes(buf), aborted

    def _get_client(self) -> httpx.AsyncClient:
        """Return an ``httpx.AsyncClient``"""
        if self._http_client is not None:
            return self._http_client
        return httpx.AsyncClient(
            timeout=self._timeout,
            proxy=self._proxy,
            follow_redirects=False,
            headers={"User-Agent": self._user_agent},
        )

    async def _check_ssrf(self, url: str) -> None:
        """Enforce the SSRF boundary on a single URL when enabled."""
        if not self._block_private_network:
            return
        await _assert_public_host(url)
