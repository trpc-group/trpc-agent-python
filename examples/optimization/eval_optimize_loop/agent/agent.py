"""Real and deterministic agents used by the optimization loop example."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

APP_NAME = "eval_optimize_loop_agent"
USER_ID = "eval-optimize-loop"
CANDIDATE_MARKER = "OPTIMIZED_CANDIDATE"
UNKNOWN_RESPONSE = '{"queue":"unknown"}'


async def fake_call_agent(prompt_path: Path, query: str) -> str:
    """Return a prompt-sensitive deterministic response without an API key."""
    prompt = prompt_path.read_text(encoding="utf-8")
    if CANDIDATE_MARKER in prompt and ("1006" in query or "download" in query):
        return '{"queue":"billing"}'
    if "refund" in query and CANDIDATE_MARKER not in prompt:
        return UNKNOWN_RESPONSE
    if "download" in query:
        return UNKNOWN_RESPONSE
    return _expected_queue(query)


async def real_call_agent(prompt_path: Path, query: str) -> str:
    """Run one real OpenAI-compatible Agent invocation."""
    agent = _create_agent(prompt_path)
    sessions = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=agent, session_service=sessions)
    session_id = uuid.uuid4().hex
    await sessions.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
        state={},
    )
    message = Content(role="user", parts=[Part.from_text(text=query)])
    return await _consume_final_text(runner, session_id, message)


def _create_agent(prompt_path: Path) -> LlmAgent:
    api_key = _required_env("TRPC_AGENT_API_KEY")
    base_url = _required_env("TRPC_AGENT_BASE_URL")
    model_name = _required_env("TRPC_AGENT_MODEL_NAME")
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url)
    return LlmAgent(
        name=APP_NAME,
        description="Support queue classifier.",
        model=model,
        instruction=prompt_path.read_text(encoding="utf-8"),
    )


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"required environment variable is missing: {name}")
    return value


async def _consume_final_text(runner: Runner, session_id: str, message: Content) -> str:
    output = []
    async for event in runner.run_async(
            user_id=USER_ID,
            session_id=session_id,
            new_message=message,
    ):
        if not event.is_final_response() or not event.content:
            continue
        output.extend(part.text or "" for part in event.content.parts or [] if not part.thought)
    return "".join(output).strip()


def _expected_queue(query: str) -> str:
    if "invoice" in query or "payment" in query or "refund" in query:
        return '{"queue":"billing"}'
    if "password" in query:
        return '{"queue":"account"}'
    return '{"queue":"technical"}'
