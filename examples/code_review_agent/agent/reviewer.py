"""Adapter from ReviewContext to the tRPC Agent runtime."""

from __future__ import annotations

import json
from uuid import uuid4

from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part

from ..code_review.context_builder import ReviewContext
from ..code_review.models import ReviewOutput
from .agent import create_agent


async def review_with_llm(context: ReviewContext) -> ReviewOutput:
    """Run one isolated Agent session and return its structured response."""
    agent = create_agent()
    session_service = InMemorySessionService()
    runner = Runner(
        app_name="code_review_agent",
        agent=agent,
        session_service=session_service,
    )
    session_id = str(uuid4())
    payload = {
        "task": "Review only the supplied Git diff.",
        "included_files": list(context.included_files),
        "skipped_files": list(context.skipped_files),
        "diff": context.text,
        "static_analysis": context.static_analysis,
    }
    content = Content(parts=[Part.from_text(text=json.dumps(payload, ensure_ascii=False))])
    try:
        async for _ in runner.run_async(
                user_id="local-review",
                session_id=session_id,
                new_message=content,
        ):
            pass
        session = await session_service.get_session(
            app_name="code_review_agent",
            user_id="local-review",
            session_id=session_id,
        )
        if session is None:
            raise RuntimeError("Review session disappeared before output collection")
        raw_output = session.state.get(agent.output_key or "")
        if not raw_output:
            raise RuntimeError("The model did not produce structured review output")
        if isinstance(raw_output, str):
            return ReviewOutput.model_validate_json(raw_output)
        return ReviewOutput.model_validate(raw_output)
    finally:
        await runner.close()
