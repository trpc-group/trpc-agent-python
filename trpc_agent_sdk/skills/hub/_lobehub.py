# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""LobeHub agent marketplace source adapter."""

from __future__ import annotations

import json
from typing import Any

import httpx
from trpc_agent_sdk.log import logger

from ._source import SkillSource
from ._types import SkillBundle
from ._types import SkillMeta


class LobeHubSource(SkillSource):
    """
    Fetch skills from LobeHub's agent marketplace (14,500+ agents).
    LobeHub agents are system prompt templates — we convert them to SKILL.md on fetch.
    Data lives in GitHub: lobehub/lobe-chat-agents.
    """

    INDEX_URL = "https://chat-agents.lobehub.com/index.json"

    def source_id(self) -> str:
        return "lobehub"

    def search(self, query: str, limit: int = 10) -> list[SkillMeta]:
        index = self._fetch_index()
        if not index:
            return []

        query_lower = query.lower()
        results: list[SkillMeta] = []

        agents = index.get("agents", index) if isinstance(index, dict) else index
        if not isinstance(agents, list):
            return []

        for agent in agents:
            meta = agent.get("meta", agent)
            title = meta.get("title", agent.get("identifier", ""))
            desc = meta.get("description", "")
            tags = meta.get("tags", [])

            searchable = f"{title} {desc} {' '.join(tags) if isinstance(tags, list) else ''}".lower()
            if query_lower in searchable:
                identifier = agent.get("identifier", title.lower().replace(" ", "-"))
                results.append(
                    SkillMeta(
                        name=identifier,
                        description=desc[:200],
                        source="lobehub",
                        identifier=f"lobehub/{identifier}",
                        tags=tags if isinstance(tags, list) else [],
                    ))

            if len(results) >= limit:
                break

        return results

    def fetch(self, identifier: str) -> SkillBundle | None:
        # Strip "lobehub/" prefix if present
        agent_id = identifier.split("/", 1)[-1] if identifier.startswith("lobehub/") else identifier

        agent_data = self._fetch_agent(agent_id)
        if not agent_data:
            return None

        skill_md = self._convert_to_skill_md(agent_data)
        return SkillBundle(
            name=agent_id,
            files={"SKILL.md": skill_md},
            source="lobehub",
            identifier=f"lobehub/{agent_id}",
        )

    def inspect(self, identifier: str) -> SkillMeta | None:
        agent_id = identifier.split("/", 1)[-1] if identifier.startswith("lobehub/") else identifier
        index = self._fetch_index()
        if not index:
            return None

        agents = index.get("agents", index) if isinstance(index, dict) else index
        if not isinstance(agents, list):
            return None

        for agent in agents:
            if agent.get("identifier") == agent_id:
                meta = agent.get("meta", agent)
                return SkillMeta(
                    name=agent_id,
                    description=meta.get("description", ""),
                    source="lobehub",
                    identifier=f"lobehub/{agent_id}",
                    tags=meta.get("tags", []) if isinstance(meta.get("tags"), list) else [],
                )
        return None

    def _fetch_index(self) -> Any | None:
        """Fetch the LobeHub agent index."""
        try:
            resp = httpx.get(self.INDEX_URL, timeout=30)
            if resp.status_code != 200:
                return None
            return resp.json()
        except (httpx.HTTPError, json.JSONDecodeError):
            return None

    def _fetch_agent(self, agent_id: str) -> dict | None:
        """Fetch a single agent's JSON file."""
        url = f"https://chat-agents.lobehub.com/{agent_id}.json"
        try:
            resp = httpx.get(url, timeout=15)
            if resp.status_code == 200:
                return resp.json()
        except (httpx.HTTPError, json.JSONDecodeError) as e:
            logger.debug("LobeHub agent fetch failed: %s", e)
        return None

    @staticmethod
    def _convert_to_skill_md(agent_data: dict) -> str:
        """Convert a LobeHub agent JSON into SKILL.md format."""
        meta = agent_data.get("meta", agent_data)
        identifier = agent_data.get("identifier", "lobehub-agent")
        title = meta.get("title", identifier)
        description = meta.get("description", "")
        tags = meta.get("tags", [])
        system_role = agent_data.get("config", {}).get("systemRole", "")

        tag_list = tags if isinstance(tags, list) else []
        fm_lines = [
            "---",
            f"name: {identifier}",
            f"description: {description[:500]}",
            "metadata:",
            "  hermes:",
            f"    tags: [{', '.join(str(t) for t in tag_list)}]",
            "  lobehub:",
            "    source: lobehub",
            "---",
        ]

        body_lines = [
            f"# {title}",
            "",
            description,
            "",
            "## Instructions",
            "",
            system_role if system_role else "(No system role defined)",
        ]

        return "\n".join(fm_lines) + "\n\n" + "\n".join(body_lines) + "\n"
