# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Claude Code marketplace source adapter."""

from __future__ import annotations

import json

import httpx

from ._github import GitHubAuth
from ._github import GitHubSource
from ._source import SkillSource
from ._types import SkillBundle
from ._types import SkillMeta

DEFAULT_KNOWN_MARKETPLACES = (
    "anthropics/skills",
    "aiskillstore/marketplace",
)


class ClaudeMarketplaceSource(SkillSource):
    """
    Discover skills from Claude Code marketplace repos.
    Marketplace repos contain .claude-plugin/marketplace.json with plugin listings.
    """

    def __init__(
        self,
        auth: GitHubAuth,
        marketplaces: list[str] | None = None,
    ) -> None:
        self.auth = auth
        self._marketplaces = list(marketplaces or DEFAULT_KNOWN_MARKETPLACES)

    def source_id(self) -> str:
        return "claude-marketplace"

    def search(self, query: str, limit: int = 10) -> list[SkillMeta]:
        results: list[SkillMeta] = []
        query_lower = query.lower()

        for marketplace_repo in self._marketplaces:
            plugins = self._fetch_marketplace_index(marketplace_repo)
            for plugin in plugins:
                searchable = f"{plugin.get('name', '')} {plugin.get('description', '')}".lower()
                if query_lower in searchable:
                    source_path = plugin.get("source", "")
                    if source_path.startswith("./"):
                        identifier = f"{marketplace_repo}/{source_path[2:]}"
                    elif "/" in source_path:
                        identifier = source_path
                    else:
                        identifier = f"{marketplace_repo}/{source_path}"

                    results.append(
                        SkillMeta(
                            name=plugin.get("name", ""),
                            description=plugin.get("description", ""),
                            source="claude-marketplace",
                            identifier=identifier,
                            repo=marketplace_repo,
                        ))

        return results[:limit]

    def fetch(self, identifier: str) -> SkillBundle | None:
        # Delegate to GitHub Contents API since marketplace skills live in GitHub repos
        gh = GitHubSource(auth=self.auth)
        bundle = gh.fetch(identifier)
        if bundle:
            bundle.source = "claude-marketplace"
        return bundle

    def inspect(self, identifier: str) -> SkillMeta | None:
        gh = GitHubSource(auth=self.auth)
        meta = gh.inspect(identifier)
        if meta:
            meta.source = "claude-marketplace"
        return meta

    def _fetch_marketplace_index(self, repo: str) -> list[dict]:
        """Fetch and parse .claude-plugin/marketplace.json from a repo."""
        url = f"https://api.github.com/repos/{repo}/contents/.claude-plugin/marketplace.json"
        try:
            resp = httpx.get(
                url,
                headers={
                    **self.auth.get_headers(), "Accept": "application/vnd.github.v3.raw"
                },
                timeout=15,
            )
            if resp.status_code != 200:
                return []
            data = json.loads(resp.text)
        except (httpx.HTTPError, json.JSONDecodeError):
            return []

        return data.get("plugins", [])
