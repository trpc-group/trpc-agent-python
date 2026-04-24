# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Demo entry point for the WebSearchTool example.

The script drives several agents back-to-back so a single ``python3
run_agent.py`` invocation exercises the most common ``WebSearchTool``
features across both supported providers.

DuckDuckGo scenarios (keyless, Instant Answer API):

1. Plain definition / encyclopedia lookup using an entity-style query.
2. ``allowed_domains`` (whitelist) via prompt — only Wikipedia
   sources are kept.
3. ``dedup_urls=False`` raw multi-source hits, including DDG's internal
   ``duckduckgo.com/c/...`` category pages. Serves as the *baseline*
   for scenario 4.
4. ``blocked_domains`` (blacklist) via prompt, using the **same query
   as scenario 3** so the filtering effect is visible by direct
   comparison (``duckduckgo.com/...`` hits disappear).

Google Custom Search scenarios (API key + engine id required):

5. Plain Google CSE lookup with the baseline ``google_agent`` (shared
   ``http_client``, default ``lang="en"``, ``safe="active"`` via
   ``google_extra_params``).
6. ``allowed_domains`` with a single domain — exercises CSE's
   server-side ``siteSearch`` fast path.
7. ``allowed_domains`` with multiple domains — falls back to the
   tool's client-side filter (CSE only supports one ``siteSearch``).
8. ``blocked_domains`` on a broad query.
9. Per-call ``lang`` override — Chinese results via ``lang='zh-CN'``.
10. ``google_raw_agent`` — ``dedup_urls=False`` with a six-month
    recency bias, useful for surfacing recently indexed documentation.

Each scenario uses a fresh session so the agents do not lean on prior
context — this makes the tool-call flow easy to read in the output.

Google scenarios short-circuit with a friendly "provider not
configured" summary when ``GOOGLE_CSE_API_KEY`` / ``GOOGLE_CSE_ENGINE_ID``
are not set, so the DuckDuckGo half of the demo still runs end-to-end
on a fresh checkout without extra setup.
"""

import asyncio
import os
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

APP_NAME = "websearch_agent_demo"
USER_ID = "demo_user"


async def _run_one_query(runner: Runner, *, label: str, query: str) -> None:
    """Drive a single user query through ``runner`` and pretty-print events."""
    session_id = str(uuid.uuid4())
    await runner.session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
        state={"user_name": USER_ID},
    )

    print(f"\n========== {label} ==========")
    print(f"🆔 Session ID: {session_id[:8]}...")
    print(f"📝 User: {query}")
    print("🤖 Assistant: ", end="", flush=True)

    user_content = Content(parts=[Part.from_text(text=query)])
    async for event in runner.run_async(
            user_id=USER_ID,
            session_id=session_id,
            new_message=user_content,
    ):
        if not event.content or not event.content.parts:
            continue

        if event.partial:
            for part in event.content.parts:
                if part.text:
                    print(part.text, end="", flush=True)
            continue

        for part in event.content.parts:
            if part.thought:
                continue
            if part.function_call:
                print(f"\n🔧 [Invoke Tool: {part.function_call.name}({part.function_call.args})]")
            elif part.function_response:
                resp = part.function_response.response
                preview = _summarise_tool_response(resp)
                print(f"📊 [Tool Result: {preview}]")

    print("\n" + "-" * 40)


def _summarise_tool_response(resp) -> str:
    """Compact a ``WebSearchResult`` dict so the demo output stays readable."""
    if not isinstance(resp, dict):
        return str(resp)
    if "error" in resp:
        return f"error={resp.get('error')!r} provider={resp.get('provider')!r}"
    provider = resp.get("provider")
    query = resp.get("query")
    results = resp.get("results") or []
    summary = (resp.get("summary") or "").strip().replace("\n", " ")
    if len(summary) > 120:
        summary = summary[:117] + "..."
    title_preview = ", ".join(((r.get("title") or "")[:40] for r in results[:3]))
    return (f"provider={provider} query={query!r} hits={len(results)} "
            f"summary={summary!r} top_titles=[{title_preview}]")


async def _drive_agent(agent: LlmAgent, *, scenarios: list[tuple[str, str]]) -> None:
    """Spin up a ``Runner`` for ``agent`` and run each ``(label, query)`` pair."""
    runner = Runner(
        app_name=APP_NAME,
        agent=agent,
        session_service=InMemorySessionService(),
    )
    for label, query in scenarios:
        await _run_one_query(runner, label=label, query=query)


def _google_credentials_configured() -> bool:
    """Return ``True`` when both GOOGLE_CSE_* env vars are non-empty."""
    return bool(os.getenv("GOOGLE_CSE_API_KEY") and os.getenv("GOOGLE_CSE_ENGINE_ID"))


async def _run_duckduckgo_scenarios(ddg_agent: LlmAgent, ddg_raw_agent: LlmAgent) -> None:
    """Drive the keyless DuckDuckGo scenarios."""
    ddg_scenarios_pre_raw = [
        (
            "DuckDuckGo · plain lookup",
            "Look up the entity 'Python (programming language)' and summarise it in "
            "one paragraph. Use count=1.",
        ),
        (
            "DuckDuckGo · allowed_domains whitelist",
            "Look up 'Python (programming language)' but only keep results from "
            "wikipedia.org. Return up to 3 results.",
        ),
    ]
    await _drive_agent(ddg_agent, scenarios=ddg_scenarios_pre_raw)

    ddg_raw_scenarios = [
        (
            "DuckDuckGo · raw multi-source hits",
            "Search for 'Python programming language' and return up to 5 results.",
        ),
    ]
    await _drive_agent(ddg_raw_agent, scenarios=ddg_raw_scenarios)

    ddg_blocked_scenarios = [
        (
            "DuckDuckGo · blocked_domains blacklist",
            "Search for 'Python programming language' and exclude any results from "
            "duckduckgo.com. Return up to 5 results.",
        ),
    ]
    await _drive_agent(ddg_agent, scenarios=ddg_blocked_scenarios)


async def _run_google_scenarios(google_agent: LlmAgent, google_raw_agent: LlmAgent) -> None:
    """Drive the Google Custom Search scenarios.

    The scenarios intentionally exercise every surface of the Google
    code path in ``WebSearchTool._search_google``:

    - server-side ``siteSearch=i`` (single-domain allowed_domains)
    - client-side post-hoc filter (multi-domain allowed_domains)
    - server-side ``siteSearch=e`` (blocked_domains)
    - per-call ``lang`` override (Google CSE ``hl`` parameter)
    - ``google_extra_params`` on ``google_raw_agent``
      (``dateRestrict=m6`` for recency)
    """
    google_scenarios = [
        (
            "Google · plain web search",
            "What are the headline features of FastAPI 0.115? Use count=3.",
        ),
        (
            "Google · allowed_domains single (server-side siteSearch)",
            "Search for 'Python asyncio tutorial' but only keep results from "
            "python.org. Return up to 3 results.",
        ),
        (
            "Google · allowed_domains multi (client-side filter)",
            "Search for 'pydantic v2 migration guide' and restrict results to "
            "docs.pydantic.dev or github.com. Return up to 5 results.",
        ),
        (
            "Google · blocked_domains blacklist",
            "Search for 'HTML form tutorial' and exclude any results from "
            "w3schools.com. Return up to 3 results.",
        ),
        (
            "Google · per-call lang override (zh-CN)",
            "Search for 'FastAPI 入门教程' in Chinese (pass lang='zh-CN'). "
            "Return up to 3 results.",
        ),
    ]
    await _drive_agent(google_agent, scenarios=google_scenarios)

    google_raw_scenarios = [
        (
            "Google · raw hits with 6-month recency bias",
            "What are the latest Python 3.13 release highlights this year? "
            "Return up to 5 results.",
        ),
    ]
    await _drive_agent(google_raw_agent, scenarios=google_raw_scenarios)


async def main() -> None:
    from agent.agent import aclose_shared_google_http_client
    from agent.agent import ddg_agent
    from agent.agent import ddg_raw_agent
    from agent.agent import google_agent
    from agent.agent import google_raw_agent

    await _run_duckduckgo_scenarios(ddg_agent, ddg_raw_agent)

    if _google_credentials_configured():
        try:
            await _run_google_scenarios(google_agent, google_raw_agent)
        finally:
            await aclose_shared_google_http_client()
    else:
        print("\n[skip] Google scenarios skipped: set GOOGLE_CSE_API_KEY and "
              "GOOGLE_CSE_ENGINE_ID in the environment (see .env) to enable them.")


if __name__ == "__main__":
    asyncio.run(main())
