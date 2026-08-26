# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Select and preload long-term memories for the current model request."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from html import escape
from typing import Protocol
from typing import TYPE_CHECKING

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.memory import InMemoryMemoryService
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from ._formats import memory_freshness
from ._formats import parse_memory_updated_at
from ._runtime import AdvancedMemoryRuntime

if TYPE_CHECKING:
    from trpc_agent_sdk.context import InvocationContext

_FRONTMATTER_FIELD = re.compile(r"^(?P<field>[A-Za-z_]+):\s*(?P<value>.*)$", re.MULTILINE)


@dataclass(frozen=True)
class MemoryCandidate:
    """Describe one topic file using only its frontmatter metadata."""

    filename: str
    name: str
    description: str
    memory_type: str
    updated_at: datetime | None

    def to_selector_dict(self) -> dict[str, str]:
        """Render the metadata passed to the relevance selector."""
        return {
            "filename": self.filename,
            "name": self.name,
            "description": self.description,
            "type": self.memory_type,
            "freshness": memory_freshness(self.updated_at),
        }


class MemoryRelevanceSelector(Protocol):
    """Select relevant topic filenames for a user query."""

    async def select(
        self,
        query: str,
        candidates: list[MemoryCandidate],
        ctx: "InvocationContext",
        *,
        limit: int,
    ) -> list[str]:
        """Return at most ``limit`` filenames from the candidate list."""


def _frontmatter(content: str) -> dict[str, str]:
    """Parse the simple single-line frontmatter used by MemoryDocument."""
    if not content.startswith("---\n"):
        return {}
    end = content.find("\n---", 4)
    if end < 0:
        return {}
    return {match.group("field"): match.group("value").strip() for match in _FRONTMATTER_FIELD.finditer(content[4:end])}


def _candidate_from_content(filename: str, content: str) -> MemoryCandidate:
    """Build candidate metadata from a topic document."""
    metadata = _frontmatter(content)
    return MemoryCandidate(
        filename=filename,
        name=metadata.get("name", filename),
        description=metadata.get("description", ""),
        memory_type=metadata.get("type", ""),
        updated_at=parse_memory_updated_at(content),
    )


class ModelMemoryRelevanceSelector:
    """Use an isolated lightweight Agent to select relevant topic files."""

    def __init__(self, model: object | None = None) -> None:
        """Store an optional dedicated selector model."""
        self._model = model

    def _resolve_model(self, ctx: "InvocationContext") -> object:
        """Prefer a dedicated selector model and fall back to the main model."""
        model = self._model if self._model is not None else getattr(ctx.agent, "model", None)
        if model is None:
            raise ValueError("Memory relevance selector cannot resolve an LLM model")
        return model

    @staticmethod
    def _build_prompt(query: str, candidates: list[MemoryCandidate], limit: int) -> str:
        """Build the strict JSON selection prompt."""
        candidate_payload = [candidate.to_selector_dict() for candidate in candidates]
        return ("Select the long-term memory files that are clearly relevant to the user's query.\n"
                f"Return at most {limit} filenames. If none are clearly relevant, return an empty list.\n"
                "Use freshness as one relevance signal, but do not discard an older memory solely because it is old.\n"
                "Only return filenames from the candidate list. Do not explain your choices.\n"
                'Return exactly one JSON object: {"selected_memories": ["filename.md"]}\n\n'
                f"User query:\n{query}\n\n"
                f"Candidate memories:\n{json.dumps(candidate_payload, ensure_ascii=False, indent=2)}")

    @staticmethod
    def _parse_selection(
        text: str,
        candidates: list[MemoryCandidate],
        limit: int,
    ) -> list[str]:
        """Parse and validate the selector's JSON response."""
        payload = None
        decoder = json.JSONDecoder()
        for index, character in enumerate(text):
            if character != "{":
                continue
            try:
                value, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                payload = value
                break
        if payload is None:
            raise ValueError("Memory relevance selector returned no JSON object")
        selected = payload.get("selected_memories")
        if not isinstance(selected, list):
            raise ValueError("Memory relevance selector returned an invalid selected_memories list")
        valid_filenames = {candidate.filename for candidate in candidates}
        result: list[str] = []
        for filename in selected:
            if isinstance(filename, str) and filename in valid_filenames and filename not in result:
                result.append(filename)
            if len(result) >= limit:
                break
        return result

    async def select(
        self,
        query: str,
        candidates: list[MemoryCandidate],
        ctx: "InvocationContext",
        *,
        limit: int,
    ) -> list[str]:
        """Run the isolated selector Agent and validate its result."""
        app_name = f"{ctx.app_name}_advanced_memory_selector"
        agent = LlmAgent(
            name="advanced_memory_relevance_selector",
            description="Select relevant long-term memories.",
            instruction=("You are a strict long-term memory relevance selector. "
                         "Follow the user's query and output format exactly."),
            model=self._resolve_model(ctx),
            tools=[],
            add_name_to_instruction=False,
        )
        runner = Runner(
            app_name=app_name,
            agent=agent,
            session_service=InMemorySessionService(),
            memory_service=InMemoryMemoryService(),
            enable_post_turn_processing=False,
        )
        try:
            session = await runner.session_service.create_session(
                app_name=app_name,
                user_id="advanced-memory-selector",
                state={},
            )
            content = Content(
                role="user",
                parts=[Part.from_text(text=self._build_prompt(query, candidates, limit))],
            )
            last_event = None
            async for event in runner.run_async(
                    user_id=session.user_id,
                    session_id=session.id,
                    new_message=content,
            ):
                if not event.partial:
                    last_event = event
            if not last_event or not last_event.content or not last_event.content.parts:
                raise ValueError("Memory relevance selector returned no final content")
            text = "\n".join(part.text for part in last_event.content.parts if part.text)
            return self._parse_selection(text, candidates, limit)
        finally:
            await runner.close()


async def select_relevant_memory_filenames(
    query: str,
    candidates: list[MemoryCandidate],
    ctx: "InvocationContext",
    *,
    selector: MemoryRelevanceSelector,
    limit: int,
) -> list[str]:
    """Select relevant memory filenames behind a replaceable screening boundary."""
    selected = await selector.select(query, candidates, ctx, limit=limit)
    valid_filenames = {candidate.filename for candidate in candidates}
    return list(dict.fromkeys(filename for filename in selected if filename in valid_filenames))[:limit]


class MemoryPreloader:
    """Find and render relevant topic files for automatic prompt injection."""

    def __init__(
        self,
        runtime: AdvancedMemoryRuntime,
        selector: MemoryRelevanceSelector | None = None,
    ) -> None:
        """Store the runtime and replaceable relevance selector."""
        self._runtime = runtime
        self._selector = selector or ModelMemoryRelevanceSelector()

    async def _candidates(self) -> list[MemoryCandidate]:
        """Read and sort bounded topic metadata for selection."""
        candidates: list[MemoryCandidate] = []
        for path in await self._runtime.long_term_memory.list_topics():
            frontmatter = await self._runtime.long_term_memory.read_topic_frontmatter(path.name)
            if frontmatter is not None:
                candidates.append(_candidate_from_content(path.name, frontmatter))
        candidates.sort(
            key=lambda candidate: candidate.updated_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return candidates[:self._runtime.config.preload_memory_candidate_limit]

    async def preload(self, query: str, ctx: "InvocationContext") -> str | None:
        """Select and render relevant topic bodies within the configured budget."""
        config = self._runtime.config
        if not config.enabled or not config.preload_memory_enabled or not query.strip():
            return None
        try:
            candidates = await self._candidates()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Advanced Memory preload candidate loading failed: %s", exc)
            return None
        if not candidates:
            return None
        try:
            selected = await select_relevant_memory_filenames(
                query,
                candidates,
                ctx,
                selector=self._selector,
                limit=config.preload_memory_max_topics,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Advanced Memory preload selection failed: %s", exc)
            return None
        by_filename = {candidate.filename: candidate for candidate in candidates}
        sections: list[str] = []
        used_chars = 0
        for filename in selected:
            candidate = by_filename.get(filename)
            if candidate is None:
                continue
            try:
                full_content = await self._runtime.long_term_memory.read_topic(filename)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Advanced Memory preload topic loading failed for %s: %s", filename, exc)
                continue
            if full_content is None:
                continue
            remaining = config.preload_memory_max_chars - used_chars
            if remaining <= 0:
                break
            truncated = len(full_content) > remaining
            content = full_content[:remaining]
            safe_filename = escape(filename, quote=True)
            sections.append(f'<memory filename="{safe_filename}" freshness="{memory_freshness(candidate.updated_at)}" '
                            f'truncated="{str(truncated).lower()}">\n'
                            f"{content}\n"
                            "</memory>")
            used_chars += len(content)
        if not sections:
            return None
        return (
            "<advanced-memory-preload>\n"
            "The following memories were automatically selected for the current request. "
            "They are historical observations, not guaranteed current facts. Verify them when necessary "
            "and update them if they are outdated or incorrect. Each memory includes its source filename "
            "and has already been read for this request. You may read or update these files again when needed.\n\n" +
            "\n\n".join(sections) + "\n</advanced-memory-preload>")
