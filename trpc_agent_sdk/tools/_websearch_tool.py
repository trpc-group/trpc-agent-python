# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Web search tool for TRPC Agent framework.

Provides a client-side :class:`WebSearchTool` that lets LLMs search the
public web for up-to-date information. Two pluggable provider backends
are supported, ``duckduckgo`` and ``google search``:

1. ``duckduckgo`` — DuckDuckGo(DDG) Instant Answer API. Keyless, good for
   factual/encyclopedic/definition lookups. Returns curated instant
   answers and related topics (NOT full real-time web results).
2. ``google`` — Google Custom Search (CSE). Requires ``api_key`` +
   ``engine_id``; returns true web results with snippet support, domain
   filtering and language targeting.
"""

from __future__ import annotations

import datetime as _dt
import os
from typing import Any
from typing import List
from typing import Literal
from typing import Optional
from urllib.parse import quote_plus
from urllib.parse import urlparse
from typing_extensions import override

import httpx
from pydantic import BaseModel
from pydantic import Field

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

from ._base_tool import BaseTool

# default http timeout, 15 seconds
_DEFAULT_TIMEOUT = 15.0
# maximum number of results to return
_MAX_COUNT = 10
# Google Custom Search API hard cap for the ``num`` parameter per request.
# Values above 10 cause CSE to return 400 Bad Request
_GOOGLE_MAX_NUM = 10
# default maximum number of results to return
_DEFAULT_RESULTS_NUM = 5
# maximum snippet length
_MAX_SNIPPET_LEN = 1000
# default snippet length
_DEFAULT_SNIPPET_LEN = 300
# maximum title length
_MAX_TITLE_LEN = 200
# default title length
_DEFAULT_TITLE_LEN = 100
# DuckDuckGo base URL
_DDG_BASE_URL = "https://api.duckduckgo.com"
# Google Custom Search base URL
_GOOGLE_BASE_URL = "https://www.googleapis.com/customsearch/v1"
# Description shown to the LLM as part of the tool schema.
_BASE_DESCRIPTION = """\
Search the public web and use the results to inform responses.
- Provides up-to-date information for current events and recent data.
- Returns structured results as {title, url, snippet} plus, for DuckDuckGo, an instant-answer summary.
  All URLs are citable as markdown hyperlinks.
- Use this tool for accessing information beyond the model's knowledge cutoff.
- Each invocation performs a single search request; prefer one well-formed query over many narrow ones.

USE WHEN the user asks about:
  - current events, news, releases, prices, version numbers
  - information past the model's knowledge cutoff
  - entity / definition / fact lookups that need citable sources

DO NOT USE WHEN:
  - the answer is already in conversation history or memory
  - the user asks for real-time data this tool cannot provide (e.g. stock ticks, live scores)
    — say so instead of guessing

Usage notes:
  - Domain filtering is supported via `allowed_domains` (whitelist) or `blocked_domains` (blacklist);
    the two are mutually exclusive.
  - After calling this tool you MUST cite the returned URLs — see the system instruction
    for the required 'Sources:' format.\
"""

ProviderType = Literal["duckduckgo", "google"]


class SearchHit(BaseModel):
    """A single simplified search result."""

    title: str = Field(default="", description="Title of the result")
    url: str = Field(default="", description="URL of the result")
    snippet: str = Field(default="", description="Short description / excerpt")


class WebSearchResult(BaseModel):
    """Structured output of :class:`WebSearchTool`."""

    query: str
    provider: ProviderType
    # results is the list of search results.
    results: List[SearchHit] = Field(default_factory=list)
    # DDG-only: summary is the aggregated instant answer / abstract / definition text.
    summary: str = ""


def _truncate(text: str, limit: int) -> str:
    """Truncate text to a maximum length."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _extract_title_from_ddg_topic(raw: str, limit: int = _DEFAULT_TITLE_LEN) -> str:
    """Extract a human-readable title from a DDG RelatedTopic ``Text`` field."""
    if not raw:
        return ""
    head = raw.split(" - ", 1)[0]
    return _truncate(head, limit)


def _extract_domain_from_url(url: str) -> str:
    """Extract the lower-cased host of an ``http(s)`` URL."""
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


def _dedup_key(url: str) -> str:
    """Return a normalised key used to deduplicate result URLs."""

    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except ValueError:
        return raw
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        return raw
    try:
        host = (parsed.hostname or "").lower().removeprefix("www.")
    except ValueError:
        return raw
    if not host:
        return raw
    netloc = host
    if parsed.port is not None:
        default_port = 80 if scheme == "http" else 443
        if parsed.port != default_port:
            netloc = f"{host}:{parsed.port}"
    path = parsed.path or ""
    if path.endswith("/") and path != "/":
        path = path.rstrip("/")
    key = f"{scheme}://{netloc}{path}"
    if parsed.query:
        key = f"{key}?{parsed.query}"
    return key


def _normalise_domains(value: Any, field: str) -> tuple[Optional[List[str]], Optional[str]]:
    """Normalise and validate a domain-list arg from the tool-call payload."""
    if value is None:
        return None, None
    if not isinstance(value, list):
        return None, f"INVALID_ARGS: `{field}` must be an array of domain strings"
    cleaned: List[str] = []
    for d in value:
        if not isinstance(d, str):
            return None, f"INVALID_ARGS: `{field}` items must all be strings"
        s = d.strip()
        if s:
            cleaned.append(s)
    if not cleaned:
        return None, f"INVALID_ARGS: `{field}` must contain at least one non-empty domain"
    return cleaned, None


def _is_blocked(url: str, allowed: Optional[List[str]], blocked: Optional[List[str]]) -> bool:
    """Post-hoc domain filter for backends that don't support it natively.
    Returns True if the URL is blocked, False if it is allowed.
    """
    host = _extract_domain_from_url(url)
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


def _current_month_year() -> str:
    """Return the current month and year as e.g. ``"April 2026"``."""
    try:
        from tzlocal import get_localzone
        now = _dt.datetime.now(get_localzone())
    except Exception:  # pylint: disable=broad-except
        now = _dt.datetime.now()
    return now.strftime("%B %Y")


def _extract_desc_from_pagemap(pagemap: dict[str, Any]) -> str:
    """Extract description from pagemap metatags.
    Collects ``description`` / ``og:description`` values out of CSE
    pagemap metatags and joins them with newlines.
    """
    try:
        metatags = pagemap.get("metatags") or []
        if not isinstance(metatags, list):
            return ""
        descs: List[str] = []
        for meta in metatags:
            if not isinstance(meta, dict):
                continue
            desc = (meta.get("description") or meta.get("og:description") or "").strip()
            if desc:
                descs.append(desc)
        return "\n".join(descs)
    except Exception as ex:  # pylint: disable=broad-except
        logger.warning("Failed to extract description from pagemap: %s", ex)
        return ""


class WebSearchTool(BaseTool):
    """LLM tool that searches the public web.

    The WebSearchTool enables LLM agents to search the public web using major search engines
    such as DuckDuckGo (default, no API key required) and Google Custom Search (API key required).
    It retrieves up-to-date information including titles, URLs, and content snippets, and also
    provides instant-answer summaries when available (e.g., via DuckDuckGo). This tool is best
    used for queries about recent events, new releases, factual lookups, or definitions that benefit
    from authoritative and citable sources. Results are automatically filtered by site/domain
    according to user or system configuration. When using this tool, answers should cite all retrieved
    sources as Markdown hyperlinks in the final output.

    Args:
        provider: Backend name, ``"duckduckgo"`` (default) or ``"google"``.
        api_key: Google CSE API key; falls back to ``GOOGLE_CSE_API_KEY``.
        engine_id: Google CSE engine id (``cx``); falls back to ``GOOGLE_CSE_ENGINE_ID``.
        results_num: Default result count, clamped to ``[1, _MAX_COUNT]``.
        snippet_len: Max snippet length, clamped to ``[1, _MAX_SNIPPET_LEN]``.
        title_len: Max title length, clamped to ``[1, _MAX_TITLE_LEN]``.
        timeout: HTTP timeout in seconds.
        base_url: Override provider base URL (for tests / proxies).
        user_agent: HTTP User-Agent header.
        proxy: Optional HTTP proxy URL forwarded to httpx.
        lang: Default language code for Google CSE (ignored by DDG).
        http_client: Optional pre-built ``httpx.AsyncClient`` to reuse; caller owns its lifecycle.
        dedup_urls: Collapse results with the same normalised URL (default ``True``).
        filters_name / filters: forwarded to :class:`BaseTool`.
    """

    def __init__(
        self,
        *,
        provider: ProviderType = "duckduckgo",
        api_key: Optional[str] = None,
        engine_id: Optional[str] = None,
        base_url: Optional[str] = None,
        user_agent: str = "trpc-agent-python-websearch/1.0",
        proxy: Optional[str] = None,
        lang: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        results_num: int = _DEFAULT_RESULTS_NUM,
        snippet_len: int = _DEFAULT_SNIPPET_LEN,
        title_len: int = _DEFAULT_TITLE_LEN,
        timeout: float = _DEFAULT_TIMEOUT,
        dedup_urls: bool = True,
        ddg_extra_params: Optional[dict[str, Any]] = None,
        google_extra_params: Optional[dict[str, Any]] = None,
        filters_name: Optional[List[str]] = None,
        filters: Optional[List[BaseFilter]] = None,
    ) -> None:

        super().__init__(
            name="websearch",
            description=_BASE_DESCRIPTION,
            filters_name=filters_name,
            filters=filters,
        )

        self._provider: ProviderType = provider
        self._api_key = api_key or os.environ.get("GOOGLE_CSE_API_KEY", "")
        self._engine_id = engine_id or os.environ.get("GOOGLE_CSE_ENGINE_ID", "")
        self._results_num = max(1, min(int(results_num), _MAX_COUNT))
        self._snippet_len = max(1, min(int(snippet_len), _MAX_SNIPPET_LEN))
        self._title_len = max(1, min(int(title_len), _MAX_TITLE_LEN))
        self._timeout = float(timeout)
        self._base_url = base_url or (_GOOGLE_BASE_URL if provider == "google" else _DDG_BASE_URL)
        self._user_agent = user_agent
        self._proxy = proxy
        self._lang = lang
        self._http_client = http_client
        self._dedup_urls = bool(dedup_urls)
        self._ddg_extra_params = ddg_extra_params or {}
        self._google_extra_params = google_extra_params or {}

        if provider == "google" and not (self._api_key and self._engine_id):
            logger.warning("WebSearchTool: provider='google' but api_key or "
                           "engine_id is missing; calls will return an error.")

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            name="websearch",
            description=_BASE_DESCRIPTION,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "query":
                    Schema(
                        type=Type.STRING,
                        description=("Required. Search query to send to the provider. Min 2 chars. "
                                     "Include year/version for recent topics. "
                                     "Example: 'Python 3.13 release notes', 'OpenAI GPT-5 pricing 2026', "
                                     "'FastAPI websocket auth', 'vector database definition'."),
                    ),
                    "count":
                    Schema(
                        type=Type.INTEGER,
                        description=(f"Optional. Max results to return, 1-{_MAX_COUNT} (clamped). "
                                     f"Default: {self._results_num}. Prefer small values to save context. "
                                     f"Example: 3, 5."),
                        minimum=1,
                        maximum=_MAX_COUNT,
                    ),
                    "allowed_domains":
                    Schema(
                        type=Type.ARRAY,
                        items=Schema(type=Type.STRING),
                        description=("Optional. Whitelist of domains, host only (subdomain-aware, "
                                     "'www.' stripped). Mutually exclusive with blocked_domains. "
                                     "Default: None. "
                                     "Example: ['python.org'], ['github.com', 'stackoverflow.com']."),
                    ),
                    "blocked_domains":
                    Schema(
                        type=Type.ARRAY,
                        items=Schema(type=Type.STRING),
                        description=("Optional. Blacklist of domains (same matching as allowed_domains). "
                                     "Mutually exclusive with allowed_domains. Default: None. "
                                     "Example: ['pinterest.com'], ['content-farm.net']."),
                    ),
                    "lang":
                    Schema(
                        type=Type.STRING,
                        description=("Optional. Language hint for the provider (Google CSE 'hl'); "
                                     "ignored by DuckDuckGo. Default: tool-level lang or unset. "
                                     "Example: 'en', 'zh-CN', 'ja'."),
                    ),
                },
                required=["query"],
            ),
        )

    @override
    async def process_request(
        self,
        *,
        tool_context: InvocationContext,
        llm_request: LlmRequest,
    ) -> None:
        """Register the declaration and inject behavioural mandates."""
        await super().process_request(tool_context=tool_context, llm_request=llm_request)
        instruction = ("You have access to the `websearch` tool which performs live web searches.\n"
                       "\n"
                       "CRITICAL REQUIREMENT — you MUST follow this:\n"
                       "  - After answering the user's question, you MUST include a 'Sources:' "
                       "section at the end of your response.\n"
                       "  - In the Sources section, list all relevant URLs from the search "
                       "results as markdown hyperlinks: `[Title](URL)`.\n"
                       "  - This is MANDATORY — never skip including sources, and never "
                       "fabricate URLs (only cite URLs returned by the tool).\n"
                       "  - Example format:\n"
                       "\n"
                       "    [Your answer here]\n"
                       "\n"
                       "    Sources:\n"
                       "    - [Source Title 1](https://example.com/1)\n"
                       "    - [Source Title 2](https://example.com/2)\n"
                       "\n"
                       f"IMPORTANT — use the correct year in search queries:\n"
                       f"  - The current month is {_current_month_year()}. You MUST use this "
                       f"year when searching for recent information, documentation, or current "
                       f"events.\n"
                       f"  - Example: if the user asks for 'latest React docs', search for "
                       f"'React documentation in the current year', NOT last year.\n"
                       "\n"
                       "Usage notes:\n"
                       "  - Domain filtering is supported via `allowed_domains` / "
                       "`blocked_domains` (mutually exclusive).\n"
                       "  - Each `websearch` call issues exactly one search request; prefer "
                       "a single well-formed query over multiple narrow ones.")
        llm_request.append_instructions([instruction])

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        query = (args.get("query") or "").strip()
        if len(query) < 2:
            return {"error": f"INVALID_QUERY: query must be at least {2} characters"}

        allowed, err = _normalise_domains(args.get("allowed_domains"), "allowed_domains")
        if err:
            return {"error": "Allowed_domains_error: " + err}
        blocked, err = _normalise_domains(args.get("blocked_domains"), "blocked_domains")
        if err:
            return {"error": "Blocked_domains_error: " + err}
        if allowed and blocked:
            return {
                "error": ("INVALID_ARGS: cannot specify both allowed_domains "
                          "and blocked_domains in the same request")
            }

        # number of results to return
        count = args.get("count")
        try:
            n = int(count) if count is not None else self._results_num
        except (TypeError, ValueError):
            n = self._results_num
        n = max(1, min(n, _MAX_COUNT))

        lang = (args.get("lang") or self._lang or "").strip() or None

        try:
            if self._provider == "duckduckgo":
                result = await self._search_duckduckgo(query, n, allowed, blocked)
            else:
                result = await self._search_google(query, n, allowed, blocked, lang)
        except httpx.HTTPError as e:
            logger.warning("WebSearchTool HTTP error (%s): %s", self._provider, e)
            return {"error": f"HTTP_ERROR: {e}", "provider": self._provider, "query": query}
        except Exception as e:  # pylint: disable=broad-except
            logger.error("WebSearchTool unexpected error (%s): %s", self._provider, e)
            return {"error": f"SEARCH_ERROR: {e}", "provider": self._provider, "query": query}

        # Return a plain dict so downstream JSON-serialisation
        return result.model_dump()

    def _get_client(self) -> httpx.AsyncClient:
        """Return an httpx AsyncClient."""
        if self._http_client is not None:
            return self._http_client
        return httpx.AsyncClient(
            timeout=self._timeout,
            proxy=self._proxy,
            headers={"User-Agent": self._user_agent},
        )

    async def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        """Issue a GET and decode the JSON body."""
        client = self._get_client()
        close = self._http_client is None
        try:
            resp = await client.get(
                url,
                params=params,
                timeout=self._timeout,
                headers={"User-Agent": self._user_agent},
            )
            resp.raise_for_status()
            return resp.json()
        finally:
            if close:
                await client.aclose()

    async def _search_duckduckgo(
        self,
        query: str,
        n: int,
        allowed: Optional[List[str]],
        blocked: Optional[List[str]],
    ) -> WebSearchResult:
        """Hit the DDG Instant Answer API."""
        params = {
            # search query
            "q": query,
            # return JSON format
            "format": "json",
            # no HTML in the response, only text
            "no_html": "1",
            # skip disambiguation, only return the most related topics result
            "skip_disambig": "1",
        }
        params.update(self._ddg_extra_params)

        data = await self._get_json(self._base_url, params=params)

        # Aggregate the free-text instant answer fields into a summary.
        summary_parts: List[str] = []
        if ans := (data.get("Answer") or "").strip():
            summary_parts.append(ans)
        abstract_text = (data.get("AbstractText") or "").strip()
        abstract_source = (data.get("AbstractSource") or "").strip()
        if abstract_text:
            summary_parts.append(f"{abstract_text} (source: {abstract_source})" if abstract_source else abstract_text)
        definition_text = (data.get("Definition") or "").strip()
        definition_source = (data.get("DefinitionSource") or "").strip()
        if definition_text:
            summary_parts.append(
                f"{definition_text} (source: {definition_source})" if definition_source else definition_text)
        summary = "\n".join(summary_parts)

        heading = (data.get("Heading") or "").strip() or query

        hits: List[SearchHit] = []
        # Track deduplicated URLs to avoid re-citing the same source.
        seen: set[str] = set()

        def _add_hit(url: str, title: str, snippet: str):
            """Append a SearchHit after filter + dedup checks."""
            url = (url or "").strip()
            if not url:
                return False
            if _is_blocked(url, allowed, blocked):
                return False
            if self._dedup_urls:
                key = _dedup_key(url)
                if key in seen:
                    return False
                seen.add(key)
            hits.append(
                SearchHit(
                    title=_truncate(title or heading, self._title_len),
                    url=url,
                    snippet=_truncate(snippet, self._snippet_len),
                ))

        # AbstractURL — canonical source of the abstract (e.g. Wikipedia).
        abstract_url = (data.get("AbstractURL") or "").strip()
        if abstract_url and len(hits) < n:
            _add_hit(abstract_url, heading, abstract_text or summary)

        # DefinitionURL — dictionary source.
        definition_url = (data.get("DefinitionURL") or "").strip()
        if definition_url and len(hits) < n:
            _add_hit(definition_url, heading, definition_text or summary)

        # Results — genuine external links DDG returns for the query
        for item in data.get("Results") or []:
            if len(hits) >= n:
                break
            if not isinstance(item, dict):
                continue
            url = (item.get("FirstURL") or "").strip()
            text = (item.get("Text") or "").strip()
            if not url or not text:
                continue
            _add_hit(
                url,
                _extract_title_from_ddg_topic(text, self._title_len),
                text,
            )

        # RelatedTopics
        for topic in data.get("RelatedTopics") or []:
            if len(hits) >= n:
                break
            # Nested "Topics" groups — flatten one level.
            inner = topic.get("Topics") if isinstance(topic, dict) else None
            candidates = inner if isinstance(inner, list) else [topic]
            for item in candidates:
                if len(hits) >= n:
                    break
                if not isinstance(item, dict):
                    continue
                url = (item.get("FirstURL") or "").strip()
                text = (item.get("Text") or "").strip()
                if not url or not text:
                    continue
                _add_hit(
                    url,
                    _extract_title_from_ddg_topic(text, self._title_len),
                    text,
                )

        # Fallback: if DDG had nothing useful but did give a summary, at
        # least surface the search page URL so the model can cite *something*.
        if not hits and summary:
            hits.append(
                SearchHit(
                    title=_truncate(query, self._title_len),
                    url=f"https://duckduckgo.com/?q={quote_plus(query)}",
                    snippet=_truncate(summary, self._snippet_len),
                ))

        return WebSearchResult(
            query=query,
            provider="duckduckgo",
            results=hits,
            summary=summary,
        )

    async def _search_google(
        self,
        query: str,
        n: int,
        allowed: Optional[List[str]],
        blocked: Optional[List[str]],
        lang: Optional[str],
    ) -> WebSearchResult:
        """Hit the Google Custom Search API."""
        if not (self._api_key and self._engine_id):
            return WebSearchResult(
                query=query,
                provider="google",
                results=[],
                summary=("Google provider is not configured: set api_key + "
                         "engine_id (or GOOGLE_CSE_API_KEY + GOOGLE_CSE_ENGINE_ID)."),
            )

        google_n = min(n, _GOOGLE_MAX_NUM)
        params: dict[str, Any] = {
            "key": self._api_key,
            "cx": self._engine_id,
            # search query
            "q": query,
            # number of results to return (Google CSE caps at 10 per request)
            "num": google_n,
        }
        # language hint for the search provider
        if lang:
            params["hl"] = lang

        # Google CSE only supports ONE siteSearch value. Use the server-side
        # filter only when exactly one domain is requested — this keeps the
        # common single-domain case fast. For multiple domains, skip the
        # server-side filter entirely and rely on the client-side
        if allowed and len(allowed) == 1:
            params["siteSearch"] = allowed[0]
            params["siteSearchFilter"] = "i"
        elif blocked and len(blocked) == 1:
            params["siteSearch"] = blocked[0]
            params["siteSearchFilter"] = "e"

        params.update(self._google_extra_params)

        data = await self._get_json(self._base_url, params=params)

        # Surface API-level errors cleanly.
        if err := data.get("error"):
            msg = err.get("message") if isinstance(err, dict) else str(err)
            return WebSearchResult(
                query=query,
                provider="google",
                results=[],
                summary=f"Google Search API error: {msg}",
            )

        hits: List[SearchHit] = []
        seen: set[str] = set()
        for item in data.get("items") or []:
            url = (item.get("link") or "").strip()
            if _is_blocked(url, allowed, blocked):
                continue
            if self._dedup_urls:
                key = _dedup_key(url)
                if key in seen:
                    continue
                seen.add(key)
            snippet = (item.get("snippet") or "").strip()
            # Enrich snippet with metatag descriptions when CSE has them
            # for denser grounding context with low token overhead.
            extra = _extract_desc_from_pagemap(item.get("pagemap") or {})
            if extra and extra not in snippet:
                snippet = f"{snippet}\n{extra}" if snippet else extra
            hits.append(
                SearchHit(
                    title=_truncate(item.get("title") or "", self._title_len),
                    url=url,
                    snippet=_truncate(snippet, self._snippet_len),
                ))
            if len(hits) >= n:
                break

        # CSE may spell-correct the query; surface the effective query in the summary.
        effective = ""
        try:
            reqs = (data.get("queries") or {}).get("request") or []
            if reqs:
                effective = (reqs[0].get("searchTerms") or "").strip()
        except Exception:  # pylint: disable=broad-except
            effective = ""
        summary = (f"(Effective query: {effective})" if effective and effective != query else "")

        return WebSearchResult(
            query=query,
            provider="google",
            results=hits,
            summary=summary,
        )
