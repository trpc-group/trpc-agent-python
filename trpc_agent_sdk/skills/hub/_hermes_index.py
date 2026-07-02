# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Centralized Hermes Skills Index source adapter.

The index is a JSON catalog published to a docs site and rebuilt daily by
CI. It contains metadata + resolved GitHub paths for every skill, eliminating
the need to hit the GitHub API for search or path discovery.
"""

from __future__ import annotations

import json

import httpx
from trpc_agent_sdk.log import logger

from ._github import GitHubAuth
from ._github import GitHubSource
from ._source import SkillSource
from ._types import SkillBundle
from ._types import SkillMeta

HERMES_INDEX_URL = "https://hermes-agent.nousresearch.com/docs/api/skills-index.json"


def _load_hermes_index() -> dict | None:
    """Fetch the centralized skills index."""
    try:
        resp = httpx.get(HERMES_INDEX_URL, timeout=15, follow_redirects=True)
        if resp.status_code != 200:
            logger.debug("Hermes index fetch returned %d", resp.status_code)
            return None
        data = resp.json()
    except (httpx.HTTPError, json.JSONDecodeError) as e:
        logger.debug("Hermes index fetch failed: %s", e)
        return None

    if not isinstance(data, dict) or "skills" not in data:
        return None

    return data


class HermesIndexSource(SkillSource):
    """Skill source backed by the centralized Hermes Skills Index.

    When the index is unavailable, all methods return empty / None so
    downstream sources take over transparently.
    """

    def __init__(self, auth: GitHubAuth):
        self._index: dict | None = None
        self._loaded = False
        self.auth = auth
        # Lazily create GitHubSource for fetch — only used when actually
        # downloading files, which requires real GitHub API calls.
        self._github: GitHubSource | None = None

    def _ensure_loaded(self) -> dict:
        if not self._loaded:
            self._index = _load_hermes_index()
            self._loaded = True
        return self._index or {}

    def _get_github(self) -> GitHubSource:
        if self._github is None:
            self._github = GitHubSource(auth=self.auth)
        return self._github

    def source_id(self) -> str:
        return "hermes-index"

    @property
    def is_available(self) -> bool:
        """Whether the index is loaded and has skills."""
        index = self._ensure_loaded()
        return bool(index.get("skills"))

    def search(self, query: str, limit: int = 10) -> list[SkillMeta]:
        """Search the cached index.  Zero API calls."""
        index = self._ensure_loaded()
        skills = index.get("skills", [])
        if not skills:
            return []

        if not query.strip():
            # No query — return featured/popular
            return [self._to_meta(s) for s in skills[:limit]]

        query_lower = query.lower()
        results: list[SkillMeta] = []
        for s in skills:
            searchable = f"{s.get('name', '')} {s.get('description', '')} {' '.join(s.get('tags', []))}".lower()
            if query_lower in searchable:
                results.append(self._to_meta(s))
                if len(results) >= limit:
                    break
        return results

    def fetch(self, identifier: str) -> SkillBundle | None:
        """Fetch a skill using the resolved path from the index.

        If the index has a ``resolved_github_id`` for this skill, we skip
        the entire candidate/discovery chain and go directly to GitHub
        with the exact path.  This reduces install from ~31 API calls to
        just the file content downloads (~5-22 depending on skill size).
        """
        index = self._ensure_loaded()
        entry = self._find_entry(identifier, index)
        if not entry:
            return None

        # Use resolved path if available
        resolved = entry.get("resolved_github_id")
        if resolved:
            bundle = self._get_github().fetch(resolved)
            if bundle:
                bundle.source = entry.get("source", "hermes-index")
                bundle.identifier = identifier
                return bundle

        # Fall back to identifier-based fetch via repo/path
        repo = entry.get("repo", "")
        path = entry.get("path", "")
        if repo and path:
            github_id = f"{repo}/{path}"
            bundle = self._get_github().fetch(github_id)
            if bundle:
                bundle.source = entry.get("source", "hermes-index")
                bundle.identifier = identifier
                return bundle

        return None

    def inspect(self, identifier: str) -> SkillMeta | None:
        """Return metadata from the index.  Zero API calls."""
        index = self._ensure_loaded()
        entry = self._find_entry(identifier, index)
        if entry:
            return self._to_meta(entry)
        return None

    def _find_entry(self, identifier: str, index: dict) -> dict | None:
        """Look up a skill in the index by identifier or name."""
        skills = index.get("skills", [])

        # Exact identifier match
        for s in skills:
            if s.get("identifier") == identifier:
                return s

        # Try without source prefix (e.g. "skills-sh/" stripped)
        normalized = identifier
        for prefix in ("skills-sh/", "skills.sh/", "official/", "github/", "clawhub/"):
            if identifier.startswith(prefix):
                normalized = identifier[len(prefix):]
                break

        # Match on normalized identifier or name
        for s in skills:
            sid = s.get("identifier", "")
            # Strip prefix from stored identifier too
            stored_normalized = sid
            for prefix in ("skills-sh/", "skills.sh/", "official/", "github/", "clawhub/"):
                if sid.startswith(prefix):
                    stored_normalized = sid[len(prefix):]
                    break
            if stored_normalized == normalized:
                return s

        return None

    @staticmethod
    def _to_meta(entry: dict) -> SkillMeta:
        return SkillMeta(
            name=entry.get("name", ""),
            description=entry.get("description", ""),
            source=entry.get("source", "hermes-index"),
            identifier=entry.get("identifier", ""),
            repo=entry.get("repo"),
            path=entry.get("path"),
            tags=entry.get("tags", []),
            extra=entry.get("extra", {}),
        )
