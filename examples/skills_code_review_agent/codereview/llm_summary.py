# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Optional LLM summary stage (fake | real | off).

``fake`` (default) needs NO API key: :class:`FakeReviewModel` is a real
``LLMModel`` subclass driven through the real ``LlmAgent`` + ``Runner`` +
``InMemorySessionService`` stack, so the whole agent path is exercised
offline — it just renders a deterministic summary from the request text.
``real`` uses the repo-wide ``TRPC_AGENT_API_KEY`` / ``TRPC_AGENT_BASE_URL`` /
``TRPC_AGENT_MODEL_NAME`` convention with ``OpenAIModel``.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Dict
from typing import List
from typing import Optional

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

_APP_NAME = "skills_code_review_agent"

_SUMMARY_INSTRUCTION = (
    "You are a senior code reviewer. Given a JSON digest of static-analysis "
    "findings for one changeset, write a concise review summary: overall risk "
    "level, the most important issues (file:line), and what to fix first. "
    "Do not invent issues that are not in the digest.")


def _extract_digest(request) -> Dict:
    for content in reversed(request.contents or []):
        for part in content.parts or []:
            if part.text and "{" in part.text:
                try:
                    return json.loads(part.text[part.text.index("{"):])
                except (ValueError, json.JSONDecodeError):
                    return {}
    return {}


def _render_fake_summary(request) -> str:
    """Deterministic summary rendered from the digest embedded in the request."""
    digest = _extract_digest(request)

    finding_count = digest.get("finding_count", 0)
    review_count = digest.get("needs_human_review_count", 0)
    severities = digest.get("severity_distribution", {})
    top = digest.get("top_findings", [])

    if finding_count == 0 and review_count == 0:
        return ("No issues detected by the review rules. The changeset looks clean; "
                "standard human review is still recommended for logic-level concerns.")
    ordered = [f"{name}={count}" for name, count in severities.items() if count]
    lines = [
        f"Automated review found {finding_count} finding(s) "
        f"({', '.join(ordered) if ordered else 'no severity data'}) "
        f"and {review_count} item(s) needing human review.",
    ]
    for item in top[:3]:
        lines.append(f"- [{item.get('severity')}] {item.get('file')}:{item.get('line')} — "
                     f"{item.get('title')}")
    if any(sev in severities and severities[sev] for sev in ("critical", "high")):
        lines.append("Fix the critical/high items before merging; see per-finding "
                     "recommendations in the report.")
    return "\n".join(lines)


class FakeReviewModel(LLMModel):
    """Deterministic offline model — makes dry-run work without any API key."""

    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"fake-review-.*"]

    async def _generate_async_impl(self, request, stream=False, ctx=None):
        yield LlmResponse(content=Content(role="model", parts=[Part.from_text(text=_render_fake_summary(request))]))

    def validate_request(self, request) -> None:
        pass


def build_summary_model(model_mode: str, model_name: str) -> Optional[LLMModel]:
    if model_mode == "off":
        return None
    if model_mode == "fake":
        return FakeReviewModel(model_name=model_name)
    if model_mode == "real":
        return OpenAIModel(
            model_name=os.getenv("TRPC_AGENT_MODEL_NAME", model_name),
            api_key=os.getenv("TRPC_AGENT_API_KEY", ""),
            base_url=os.getenv("TRPC_AGENT_BASE_URL", ""),
        )
    raise ValueError(f"unknown model mode: {model_mode!r}")


async def summarize(digest: Dict, model_mode: str, model_name: str) -> str:
    """Produce the report summary text; empty string when mode is ``off``."""
    model = build_summary_model(model_mode, model_name)
    if model is None:
        return ""
    agent = LlmAgent(
        name="code_review_summarizer",
        model=model,
        instruction=_SUMMARY_INSTRUCTION,
        description="Summarizes structured code-review findings.",
    )
    session_service = InMemorySessionService()
    runner = Runner(app_name=_APP_NAME, agent=agent, session_service=session_service)
    session_id = uuid.uuid4().hex
    await session_service.create_session(app_name=_APP_NAME, user_id="review-pipeline",
                                         session_id=session_id)
    message = Content(role="user",
                      parts=[Part.from_text(text="Findings digest:\n" + json.dumps(digest, ensure_ascii=False))])
    chunks: List[str] = []
    async for event in runner.run_async(user_id="review-pipeline", session_id=session_id,
                                        new_message=message):
        if event.content and event.content.parts and not event.partial:
            for part in event.content.parts:
                if part.text and not part.thought:
                    chunks.append(part.text)
    return "\n".join(chunks).strip()
