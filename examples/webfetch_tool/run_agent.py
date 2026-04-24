# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Demo entry point for the WebFetchTool example.

The script drives several agents back-to-back so a single ``python3
run_agent.py`` invocation exercises the most common ``WebFetchTool``
constructor knobs:

1. Default fetch — plain HTTP GET + Markdown conversion against a
   small, stable public page. Exercises the default ``timeout`` /
   ``user_agent`` / ``max_content_length`` / ``max_response_bytes`` /
   ``follow_redirects`` / ``max_redirects`` / ``block_private_network``
   settings configured on :data:`default_fetch_agent`.
2. Per-call ``max_length`` override — the LLM passes a smaller
   per-call cap so the response `content` is truncated early without
   changing the tool-level ``max_content_length`` default.
3. Cache miss → cache hit — two back-to-back fetches of the same URL
   against :data:`cached_fetch_agent`; the second response is served
   from the in-process LRU with ``cached=true``, exercising
   ``enable_cache`` / ``cache_ttl_seconds`` / ``cache_max_bytes``.
4. Whitelist rejection — :data:`whitelist_fetch_agent` pins
   ``allowed_domains=["python.org"]``; a non-whitelisted URL comes back
   with ``BLOCKED_URL`` before any HTTP traffic is issued.
5. Blacklist rejection — :data:`blocklist_fetch_agent` pins
   ``blocked_domains=["example.com"]``; a URL on that list is rejected
   with ``BLOCKED_URL`` (blocks win over allow lists).
6. SSRF rejection — :data:`ssrf_fetch_agent` keeps
   ``block_private_network=True`` and is driven against the three
   canonical SSRF payloads (loopback ``127.0.0.1``, the AWS
   cloud-metadata endpoint ``169.254.169.254``, and an RFC 1918 intranet
   IP ``10.0.0.1``). Every attempt is rejected with
   ``SSRF_BLOCKED_URL`` *before* any TCP connection is opened, so the
   demo is safe to run from any network.

The public URLs used below (``https://example.com``,
``https://www.python.org/``) are intentionally small, stable, and
unauthenticated so the example stays reproducible.

Each scenario uses a fresh session so the agents do not lean on prior
context — this makes the tool-call flow easy to read in the output.
"""

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

APP_NAME = "webfetch_agent_demo"
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
    """Compact a ``FetchResult`` dict so the demo output stays readable."""
    if not isinstance(resp, dict):
        return str(resp)
    if resp.get("error"):
        return (f"error={resp.get('error')!r} url={resp.get('url')!r} "
                f"status={resp.get('status_code')}")
    content = (resp.get("content") or "").strip().replace("\n", " ")
    if len(content) > 120:
        content = content[:117] + "..."
    return (f"url={resp.get('url')!r} status={resp.get('status_code')} "
            f"content_type={resp.get('content_type')!r} bytes={resp.get('bytes')} "
            f"cached={resp.get('cached')} duration_ms={resp.get('duration_ms')} "
            f"preview={content!r}")


async def _drive_agent(agent: LlmAgent, *, scenarios: list[tuple[str, str]]) -> None:
    """Spin up a ``Runner`` for ``agent`` and run each ``(label, query)`` pair.

    The runner — and therefore the cached agent's in-process LRU — is
    shared across every scenario in ``scenarios`` so back-to-back
    fetches of the same URL can actually hit the cache.
    """
    runner = Runner(
        app_name=APP_NAME,
        agent=agent,
        session_service=InMemorySessionService(),
    )
    for label, query in scenarios:
        await _run_one_query(runner, label=label, query=query)


async def main() -> None:
    from agent.agent import (
        blocklist_fetch_agent,
        cached_fetch_agent,
        default_fetch_agent,
        ssrf_fetch_agent,
        whitelist_fetch_agent,
    )

    default_scenarios = [
        (
            "Default · plain fetch",
            "Fetch https://example.com and summarise the page in one short paragraph.",
        ),
        (
            "Default · per-call max_length override",
            "Fetch https://example.com but only return the first ~200 characters of the body. "
            "Use max_length=200 on the tool call.",
        ),
    ]
    await _drive_agent(default_fetch_agent, scenarios=default_scenarios)

    cached_scenarios = [
        (
            "Cache · first fetch (network)",
            "Fetch https://example.com and summarise the page in one short paragraph.",
        ),
        (
            "Cache · second fetch (cache hit)",
            "Fetch https://example.com again and summarise the page in one short paragraph. "
            "Tell me whether the tool served the response from the cache.",
        ),
    ]
    await _drive_agent(cached_fetch_agent, scenarios=cached_scenarios)

    whitelist_scenarios = [
        (
            "Whitelist · allowed host (python.org)",
            "Fetch https://www.python.org/ and summarise the landing page in one short paragraph.",
        ),
        (
            "Whitelist · rejected host (example.com)",
            "Fetch https://example.com and summarise the page. If the tool refuses the URL, explain why.",
        ),
    ]
    await _drive_agent(whitelist_fetch_agent, scenarios=whitelist_scenarios)

    blocklist_scenarios = [
        (
            "Blacklist · rejected host (example.com)",
            "Fetch https://example.com and summarise the page. If the tool refuses the URL, explain why.",
        ),
    ]
    await _drive_agent(blocklist_fetch_agent, scenarios=blocklist_scenarios)

    ssrf_scenarios = [
        (
            "SSRF · loopback (127.0.0.1)",
            "Fetch http://127.0.0.1/ and summarise the page. If the tool refuses the URL, "
            "explain why in one or two sentences.",
        ),
        (
            "SSRF · cloud metadata (169.254.169.254)",
            "Fetch http://169.254.169.254/latest/meta-data/ and summarise what it returns. "
            "If the tool refuses the URL, explain why in one or two sentences.",
        ),
        (
            "SSRF · RFC 1918 intranet (10.0.0.1)",
            "Fetch http://10.0.0.1/ and summarise the page. If the tool refuses the URL, "
            "explain why in one or two sentences.",
        ),
    ]
    await _drive_agent(ssrf_fetch_agent, scenarios=ssrf_scenarios)


if __name__ == "__main__":
    asyncio.run(main())
