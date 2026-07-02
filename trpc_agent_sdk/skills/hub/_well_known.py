# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Well-known Agent Skills endpoint source adapter."""

from __future__ import annotations

import json
from urllib.parse import urlparse
from urllib.parse import urlunparse

import httpx
from trpc_agent_sdk.log import logger

from ._github import GitHubSource
from ._source import SkillSource
from ._types import SkillBundle
from ._types import SkillMeta
from ._types import validate_bundle_rel_path
from ._types import validate_skill_name

DEFAULT_BASE_PATH = "/.well-known/skills"


class WellKnownSkillSource(SkillSource):
    """Read skills from a domain exposing a well-known skills index endpoint."""

    def __init__(self, base_path: str | None = None) -> None:
        raw = (base_path or DEFAULT_BASE_PATH).strip()
        if not raw.startswith("/"):
            raw = f"/{raw}"
        self._base_path = raw.rstrip("/")

    def source_id(self) -> str:
        return "well-known"

    def search(self, query: str, limit: int = 10) -> list[SkillMeta]:
        index_url = self._query_to_index_url(query)
        if not index_url:
            return []

        parsed = self._parse_index(index_url)
        if not parsed:
            return []

        results: list[SkillMeta] = []
        for entry in parsed["skills"][:limit]:
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                continue
            description = entry.get("description", "")
            files = entry.get("files", ["SKILL.md"])
            results.append(
                SkillMeta(
                    name=name,
                    description=str(description),
                    source="well-known",
                    identifier=self._wrap_identifier(parsed["base_url"], name),
                    path=name,
                    extra={
                        "index_url": parsed["index_url"],
                        "base_url": parsed["base_url"],
                        "files": files if isinstance(files, list) else ["SKILL.md"],
                    },
                ))
        return results

    def inspect(self, identifier: str) -> SkillMeta | None:
        parsed = self._parse_identifier(identifier)
        if not parsed:
            return None

        entry = self._index_entry(parsed["index_url"], parsed["skill_name"])
        if not entry:
            return None

        skill_md = self._fetch_text(f"{parsed['skill_url']}/SKILL.md")
        if skill_md is None:
            return None

        fm = GitHubSource._parse_frontmatter_quick(skill_md)
        description = str(fm.get("description") or entry.get("description") or "")
        name = str(fm.get("name") or parsed["skill_name"])
        return SkillMeta(
            name=name,
            description=description,
            source="well-known",
            identifier=self._wrap_identifier(parsed["base_url"], parsed["skill_name"]),
            path=parsed["skill_name"],
            extra={
                "index_url": parsed["index_url"],
                "base_url": parsed["base_url"],
                "files": entry.get("files", ["SKILL.md"]),
                "endpoint": parsed["skill_url"],
            },
        )

    def fetch(self, identifier: str) -> SkillBundle | None:
        parsed = self._parse_identifier(identifier)
        if not parsed:
            return None

        try:
            skill_name = validate_skill_name(parsed["skill_name"])
        except ValueError:
            logger.warning("Well-known skill identifier contained unsafe skill name: %s", identifier)
            return None

        entry = self._index_entry(parsed["index_url"], parsed["skill_name"])
        if not entry:
            return None

        files = entry.get("files", ["SKILL.md"])
        if not isinstance(files, list) or not files:
            files = ["SKILL.md"]

        downloaded: dict[str, str] = {}
        for rel_path in files:
            if not isinstance(rel_path, str) or not rel_path:
                continue
            try:
                safe_rel_path = validate_bundle_rel_path(rel_path)
            except ValueError:
                logger.warning(
                    "Well-known skill %s advertised unsafe file path: %r",
                    identifier,
                    rel_path,
                )
                return None
            text = self._fetch_text(f"{parsed['skill_url']}/{safe_rel_path}")
            if text is None:
                return None
            downloaded[safe_rel_path] = text

        if "SKILL.md" not in downloaded:
            return None

        return SkillBundle(
            name=skill_name,
            files=downloaded,
            source="well-known",
            identifier=self._wrap_identifier(parsed["base_url"], skill_name),
            metadata={
                "index_url": parsed["index_url"],
                "base_url": parsed["base_url"],
                "endpoint": parsed["skill_url"],
                "files": files,
            },
        )

    def _query_to_index_url(self, query: str) -> str | None:
        query = query.strip()
        if not query.startswith(("http://", "https://")):
            return None
        if query.endswith("/index.json"):
            return query
        if f"{self._base_path}/" in query:
            base_url = query.split(f"{self._base_path}/", 1)[0] + self._base_path
            return f"{base_url}/index.json"
        return query.rstrip("/") + f"{self._base_path}/index.json"

    def _parse_identifier(self, identifier: str) -> dict | None:
        raw = identifier[len("well-known:"):] if identifier.startswith("well-known:") else identifier
        if not raw.startswith(("http://", "https://")):
            return None

        parsed_url = urlparse(raw)
        clean_url = urlunparse(parsed_url._replace(fragment=""))
        fragment = parsed_url.fragment

        if clean_url.endswith("/index.json"):
            if not fragment:
                return None
            base_url = clean_url[:-len("/index.json")]
            skill_name = fragment
            skill_url = f"{base_url}/{skill_name}"
            return {
                "index_url": clean_url,
                "base_url": base_url,
                "skill_name": skill_name,
                "skill_url": skill_url,
            }

        if clean_url.endswith("/SKILL.md"):
            skill_url = clean_url[:-len("/SKILL.md")]
        else:
            skill_url = clean_url.rstrip("/")

        if f"{self._base_path}/" not in skill_url:
            return None

        base_url, skill_name = skill_url.rsplit("/", 1)
        return {
            "index_url": f"{base_url}/index.json",
            "base_url": base_url,
            "skill_name": skill_name,
            "skill_url": skill_url,
        }

    def _parse_index(self, index_url: str) -> dict | None:
        try:
            resp = httpx.get(index_url, timeout=20, follow_redirects=True)
            if resp.status_code != 200:
                return None
            data = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError):
            return None

        skills = data.get("skills", []) if isinstance(data, dict) else []
        if not isinstance(skills, list):
            return None

        return {
            "index_url": index_url,
            "base_url": index_url[:-len("/index.json")],
            "skills": skills,
        }

    def _index_entry(self, index_url: str, skill_name: str) -> dict | None:
        parsed = self._parse_index(index_url)
        if not parsed:
            return None
        for entry in parsed["skills"]:
            if isinstance(entry, dict) and entry.get("name") == skill_name:
                return entry
        return None

    @staticmethod
    def _fetch_text(url: str) -> str | None:
        try:
            resp = httpx.get(url, timeout=20, follow_redirects=True)
            if resp.status_code == 200:
                return resp.text
        except httpx.HTTPError:
            return None
        return None

    @staticmethod
    def _wrap_identifier(base_url: str, skill_name: str) -> str:
        return f"well-known:{base_url.rstrip('/')}/{skill_name}"
