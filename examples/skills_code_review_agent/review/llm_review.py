# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""LLM enrichment step: run the agent once over the diff + static findings."""
import json
import re
import uuid

from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part

from agent.prompts import REVIEW_REQUEST_TEMPLATE

from .findings import Finding
from .redaction import redact_text

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def parse_llm_output(text: str) -> tuple[list[dict], str]:
    """Extract (findings_dicts, summary) from model text; ([], "") on failure."""
    candidates = [text.strip()]
    fence = _FENCE_RE.search(text)
    if fence:
        candidates.insert(0, fence.group(1))
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(payload, dict):
            return list(payload.get("findings") or []), str(payload.get("summary") or "")
    return [], ""


async def run_llm_review(agent, diff_text: str, static_findings) -> tuple[list[Finding], str, list[str]]:
    """Run one review turn. Returns (llm_findings, summary, warnings)."""
    warnings: list[str] = []
    runner = Runner(app_name="code_review_agent", agent=agent,
                    session_service=InMemorySessionService())
    prompt = REVIEW_REQUEST_TEMPLATE.format(
        findings_json=json.dumps([f.model_dump() for f in static_findings]),
        diff=redact_text(diff_text))
    message = Content(role="user", parts=[Part.from_text(text=prompt)])
    final_text = ""
    try:
        async for event in runner.run_async(user_id="cr_user",
                                            session_id=uuid.uuid4().hex,
                                            new_message=message):
            if event.partial or not event.content or not event.content.parts:
                continue
            for part in event.content.parts:
                if getattr(part, "text", None) and not getattr(part, "thought", None):
                    final_text += part.text
    finally:
        await runner.close()
    raw_findings, summary = parse_llm_output(final_text)
    if not summary and final_text:
        warnings.append("llm output was not valid JSON; ignored")
    findings = []
    for raw in raw_findings:
        try:
            findings.append(Finding(
                severity=str(raw.get("severity", "info")),
                category=str(raw.get("category", "security")),
                file=str(raw.get("file", "")),
                line=int(raw.get("line", 0)),
                title=str(raw.get("title", "")),
                evidence=str(raw.get("evidence", "")),
                recommendation=str(raw.get("recommendation", "")),
                confidence=float(raw.get("confidence", 0.5)),
                source="llm"))
        except (TypeError, ValueError):
            warnings.append(f"skipped malformed llm finding: {raw!r}")
    return findings, summary, warnings
