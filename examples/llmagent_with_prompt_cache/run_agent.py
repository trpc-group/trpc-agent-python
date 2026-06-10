# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Prompt cache demo – auto-detect provider from env vars.

This script infers the right agent factory from environment variables and
runs the prompt-cache demo loop.

Provider auto-detection order
------------------------------
1. model name contains ``/`` (``provider/model`` format) → LiteLLM router
2. model name starts with ``claude``                     → Anthropic / Claude
3. Anything else                                         → OpenAI-compatible

Setup
-----
In ``.env``, uncomment the section for your target provider and fill in
credentials, then run::

    python3 run_agent.py
"""

import asyncio
import time
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.events import analyze_cache_performance
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

from agent.agent import create_agent  # noqa: E402

_DEMO_QUERIES = [
    "What's the weather like today?",
    "What's the current weather in Guangzhou?",
    'What will the weather be like in Shanghai for the next three days?',
    "What's the current weather in Shenzhen?",
    'Compare the weather in Beijing and Guangzhou today.',
]


def _format_turn_stats(turn_events: list[Event]) -> str:
    """Render cache stats for a single turn using CacheMetrics."""
    m = analyze_cache_performance(turn_events)
    if m.total_requests == 0:
        return 'no usage metadata'
    cache_pct = f' ({m.cache_hit_ratio:.0f}%)' if m.total_cache_read_tokens else ''
    return (f'llm_calls={m.total_requests} | prompt={m.total_prompt_tokens} | '
            f'cache_read={m.total_cache_read_tokens}{cache_pct}, '
            f'cache_creation={m.total_cache_creation_tokens}')


async def run_demo(
    agent: LlmAgent,
    *,
    app_name: str = 'prompt_cache_demo',
) -> None:
    """Run the prompt-cache demonstration loop."""
    user_id = 'demo_user'
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=agent, session_service=session_service)

    session_id = str(uuid.uuid4())
    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        state={
            'user_name': user_id,
            'user_city': 'Beijing'
        },
    )
    print(f'🆔 Session: {session_id[:8]}… (shared across all turns)\n')

    all_events: list[Event] = []

    for turn, query in enumerate(_DEMO_QUERIES, start=1):
        print(f'===== Turn {turn} =====')
        print(f'📝 User: {query}')

        user_content = Content(parts=[Part.from_text(text=query)])
        turn_events: list[Event] = []
        assistant_started = False
        turn_error: str | None = None
        t_start = time.perf_counter()
        ttft: float | None = None

        async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=user_content,
        ):
            turn_events.append(event)

            # Surface API/streaming failures instead of silently reporting
            # "no usage metadata": the model yields an error event whose
            # content is empty, so without this the turn would look like a
            # no-op success.
            if event.is_error():
                turn_error = f'{event.error_code}: {event.error_message}'
                print(f'\n❌ Error: {turn_error}')
                continue

            if not event.content or not event.content.parts:
                continue

            is_partial = getattr(event, 'partial', False)

            for part in event.content.parts:
                if part.text and not part.thought:
                    # Streaming models emit both per-chunk partial=True events
                    # (text deltas) and a final partial=False event with the full
                    # accumulated text. Print text only from one of them.
                    if ttft is None:
                        ttft = time.perf_counter() - t_start
                    if is_partial:
                        if not assistant_started:
                            print('🤖 Assistant: ', end='', flush=True)
                            assistant_started = True
                        print(part.text, end='', flush=True)
                    elif not assistant_started:
                        print('🤖 Assistant: ', end='', flush=True)
                        print(part.text, end='', flush=True)
                        assistant_started = True
                elif part.function_call and not is_partial:
                    if ttft is None:
                        ttft = time.perf_counter() - t_start
                    print(f'\n🔧 [Tool call: {part.function_call.name}'
                          f'({part.function_call.args})]')
                    assistant_started = False
                elif part.function_response and not is_partial:
                    print(f'📊 [Tool result: {part.function_response.response}]')
                    assistant_started = False

        all_events.extend(turn_events)
        elapsed = time.perf_counter() - t_start
        ttft_str = f'{ttft * 1000:.0f}ms' if ttft is not None else 'N/A'
        print(f'\n⏱  TTFT={ttft_str}, total={elapsed * 1000:.0f}ms')
        print(f'📊 Token stats: {_format_turn_stats(turn_events)}')
        print('-' * 56 + '\n')

    m = analyze_cache_performance(all_events)
    print('===== Session Cache Summary =====')
    print(f'  Total LLM calls : {m.total_requests}')
    print(f'  Cache hit ratio : {m.cache_hit_ratio:.1f}%')
    print(f'  Utilization     : {m.cache_utilization_ratio:.1f}%')
    print(f'  Avg cached tok  : {m.avg_cached_tokens_per_request:.0f}/call')


def main() -> None:
    """Synchronous entry point."""
    asyncio.run(run_demo(create_agent()))


if __name__ == '__main__':
    main()
