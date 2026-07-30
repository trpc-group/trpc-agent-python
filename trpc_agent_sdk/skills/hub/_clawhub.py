# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""ClawHub (clawhub.ai) source adapter."""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
from trpc_agent_sdk.log import logger

from ._source import SkillSource
from ._types import SkillBundle
from ._types import SkillMeta
from ._types import validate_bundle_rel_path


class ClawHubSource(SkillSource):
    """
    Fetch skills from ClawHub (clawhub.ai) via their HTTP API.
    All skills are treated as community trust — ClawHavoc incident showed
    their vetting is insufficient (341 malicious skills found Feb 2026).
    """

    BASE_URL = "https://clawhub.ai/api/v1"

    def source_id(self) -> str:
        return "clawhub"

    @staticmethod
    def _normalize_tags(tags: Any) -> list[str]:
        if isinstance(tags, list):
            return [str(t) for t in tags]
        if isinstance(tags, dict):
            return [str(k) for k in tags if str(k) != "latest"]
        return []

    @staticmethod
    def _coerce_skill_payload(data: Any) -> dict[str, Any] | None:
        if not isinstance(data, dict):
            return None
        nested = data.get("skill")
        if isinstance(nested, dict):
            merged = dict(nested)
            latest_version = data.get("latestVersion")
            if latest_version is not None and "latestVersion" not in merged:
                merged["latestVersion"] = latest_version
            return merged
        return data

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        return [term for term in re.split(r"[^a-z0-9]+", query.lower()) if term]

    @classmethod
    def _search_score(cls, query: str, meta: SkillMeta) -> int:
        query_norm = query.strip().lower()
        if not query_norm:
            return 1

        identifier = (meta.identifier or "").lower()
        name = (meta.name or "").lower()
        description = (meta.description or "").lower()
        normalized_identifier = " ".join(cls._query_terms(identifier))
        normalized_name = " ".join(cls._query_terms(name))
        query_terms = cls._query_terms(query_norm)
        identifier_terms = cls._query_terms(identifier)
        name_terms = cls._query_terms(name)
        score = 0

        if query_norm == identifier:
            score += 140
        if query_norm == name:
            score += 130
        if normalized_identifier == query_norm:
            score += 125
        if normalized_name == query_norm:
            score += 120
        if normalized_identifier.startswith(query_norm):
            score += 95
        if normalized_name.startswith(query_norm):
            score += 90
        if query_terms and identifier_terms[:len(query_terms)] == query_terms:
            score += 70
        if query_terms and name_terms[:len(query_terms)] == query_terms:
            score += 65
        if query_norm in identifier:
            score += 40
        if query_norm in name:
            score += 35
        if query_norm in description:
            score += 10

        for term in query_terms:
            if term in identifier_terms:
                score += 15
            if term in name_terms:
                score += 12
            if term in description:
                score += 3

        return score

    @staticmethod
    def _dedupe_results(results: list[SkillMeta]) -> list[SkillMeta]:
        seen: set[str] = set()
        deduped: list[SkillMeta] = []
        for result in results:
            key = (result.identifier or result.name).lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(result)
        return deduped

    def _exact_slug_meta(self, query: str) -> SkillMeta | None:
        slug = query.strip().split("/")[-1]
        query_terms = self._query_terms(query)
        candidates: list[str] = []

        if slug and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", slug):
            candidates.append(slug)

        if query_terms:
            base_slug = "-".join(query_terms)
            if len(query_terms) >= 2:
                candidates.extend([
                    f"{base_slug}-agent",
                    f"{base_slug}-skill",
                    f"{base_slug}-tool",
                    f"{base_slug}-assistant",
                    f"{base_slug}-playbook",
                    base_slug,
                ])
            else:
                candidates.append(base_slug)

        seen: set[str] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            meta = self.inspect(candidate)
            if meta:
                return meta

        return None

    def _finalize_search_results(self, query: str, results: list[SkillMeta], limit: int) -> list[SkillMeta]:
        query_norm = query.strip()
        if not query_norm:
            return self._dedupe_results(results)[:limit]

        filtered = [meta for meta in results if self._search_score(query_norm, meta) > 0]
        filtered.sort(key=lambda meta: (
            -self._search_score(query_norm, meta),
            meta.name.lower(),
            meta.identifier.lower(),
        ))
        filtered = self._dedupe_results(filtered)

        exact = self._exact_slug_meta(query_norm)
        if exact:
            filtered = [meta for meta in filtered if self._search_score(query_norm, meta) >= 20]
            filtered = self._dedupe_results([exact] + filtered)

        if filtered:
            return filtered[:limit]

        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", query_norm):
            return []

        return self._dedupe_results(results)[:limit]

    def search(self, query: str, limit: int = 10) -> list[SkillMeta]:
        query = query.strip()

        if query:
            query_terms = self._query_terms(query)
            if len(query_terms) >= 2:
                direct = self._exact_slug_meta(query)
                if direct:
                    return [direct]

            results = self._search_catalog(query, limit=limit)
            if results:
                return results

        # Empty query or catalog fallback failure: use the lightweight listing API.
        try:
            resp = httpx.get(
                f"{self.BASE_URL}/skills",
                params={
                    "search": query,
                    "limit": limit
                },
                timeout=15,
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError):
            return []

        skills_data = data.get("items", data) if isinstance(data, dict) else data
        if not isinstance(skills_data, list):
            return []

        results = []
        for item in skills_data[:limit]:
            slug = item.get("slug")
            if not slug:
                continue
            display_name = item.get("displayName") or item.get("name") or slug
            summary = item.get("summary") or item.get("description") or ""
            tags = self._normalize_tags(item.get("tags", []))
            results.append(
                SkillMeta(
                    name=display_name,
                    description=summary,
                    source="clawhub",
                    identifier=slug,
                    tags=tags,
                ))

        return self._finalize_search_results(query, results, limit)

    def fetch(self, identifier: str) -> SkillBundle | None:
        slug = identifier.split("/")[-1]

        skill_data = self._get_json(f"{self.BASE_URL}/skills/{slug}")
        if not isinstance(skill_data, dict):
            return None

        latest_version = self._resolve_latest_version(slug, skill_data)
        if not latest_version:
            logger.warning("ClawHub fetch failed for %s: could not resolve latest version", slug)
            return None

        # Primary method: download the skill as a ZIP bundle from /download
        files = self._download_zip(slug, latest_version)

        # Fallback: try the version metadata endpoint for inline/raw content
        if "SKILL.md" not in files:
            version_data = self._get_json(f"{self.BASE_URL}/skills/{slug}/versions/{latest_version}")
            if isinstance(version_data, dict):
                # Files may be nested under version_data["version"]["files"]
                files = self._extract_files(version_data) or files
                if "SKILL.md" not in files:
                    nested = version_data.get("version", {})
                    if isinstance(nested, dict):
                        files = self._extract_files(nested) or files

        if "SKILL.md" not in files:
            logger.warning(
                "ClawHub fetch for %s resolved version %s but could not retrieve file content",
                slug,
                latest_version,
            )
            return None

        return SkillBundle(
            name=slug,
            files=files,
            source="clawhub",
            identifier=slug,
        )

    def inspect(self, identifier: str) -> SkillMeta | None:
        slug = identifier.split("/")[-1]
        data = self._coerce_skill_payload(self._get_json(f"{self.BASE_URL}/skills/{slug}"))
        if not isinstance(data, dict):
            return None

        tags = self._normalize_tags(data.get("tags", []))

        return SkillMeta(
            name=data.get("displayName") or data.get("name") or data.get("slug") or slug,
            description=data.get("summary") or data.get("description") or "",
            source="clawhub",
            identifier=data.get("slug") or slug,
            tags=tags,
        )

    def _search_catalog(self, query: str, limit: int = 10) -> list[SkillMeta]:
        catalog = self._load_catalog_index()
        if not catalog:
            return []

        return self._finalize_search_results(query, catalog, limit)

    def _load_catalog_index(self) -> list[SkillMeta]:
        cursor: str | None = None
        results: list[SkillMeta] = []
        seen: set[str] = set()
        max_pages = 50

        for _ in range(max_pages):
            params: dict[str, Any] = {"limit": 200}
            if cursor:
                params["cursor"] = cursor

            try:
                resp = httpx.get(f"{self.BASE_URL}/skills", params=params, timeout=30)
                if resp.status_code != 200:
                    break
                data = resp.json()
            except (httpx.HTTPError, json.JSONDecodeError):
                break

            items = data.get("items", []) if isinstance(data, dict) else []
            if not isinstance(items, list) or not items:
                break

            for item in items:
                slug = item.get("slug")
                if not isinstance(slug, str) or not slug or slug in seen:
                    continue
                seen.add(slug)
                display_name = item.get("displayName") or item.get("name") or slug
                summary = item.get("summary") or item.get("description") or ""
                tags = self._normalize_tags(item.get("tags", []))
                results.append(
                    SkillMeta(
                        name=display_name,
                        description=summary,
                        source="clawhub",
                        identifier=slug,
                        tags=tags,
                    ))

            cursor = data.get("nextCursor") if isinstance(data, dict) else None
            if not isinstance(cursor, str) or not cursor:
                break

        return results

    def _get_json(self, url: str, timeout: int = 20) -> Any | None:
        try:
            resp = httpx.get(url, timeout=timeout)
            if resp.status_code != 200:
                return None
            return resp.json()
        except (httpx.HTTPError, json.JSONDecodeError):
            return None

    def _resolve_latest_version(self, slug: str, skill_data: dict[str, Any]) -> str | None:
        latest = skill_data.get("latestVersion")
        if isinstance(latest, dict):
            version = latest.get("version")
            if isinstance(version, str) and version:
                return version

        tags = skill_data.get("tags")
        if isinstance(tags, dict):
            latest_tag = tags.get("latest")
            if isinstance(latest_tag, str) and latest_tag:
                return latest_tag

        versions_data = self._get_json(f"{self.BASE_URL}/skills/{slug}/versions")
        if isinstance(versions_data, list) and versions_data:
            first = versions_data[0]
            if isinstance(first, dict):
                version = first.get("version")
                if isinstance(version, str) and version:
                    return version
        return None

    def _extract_files(self, version_data: dict[str, Any]) -> dict[str, str]:
        files: dict[str, str] = {}
        file_list = version_data.get("files")

        if isinstance(file_list, dict):
            return {k: v for k, v in file_list.items() if isinstance(v, str)}

        if not isinstance(file_list, list):
            return files

        for file_meta in file_list:
            if not isinstance(file_meta, dict):
                continue

            fname = file_meta.get("path") or file_meta.get("name")
            if not fname or not isinstance(fname, str):
                continue

            inline_content = file_meta.get("content")
            if isinstance(inline_content, str):
                files[fname] = inline_content
                continue

            raw_url = file_meta.get("rawUrl") or file_meta.get("downloadUrl") or file_meta.get("url")
            if isinstance(raw_url, str) and raw_url.startswith("http"):
                content = self._fetch_text(raw_url)
                if content is not None:
                    files[fname] = content

        return files

    def _download_zip(self, slug: str, version: str) -> dict[str, str]:
        """Download skill as a ZIP bundle from the /download endpoint and extract text files."""
        import io
        import zipfile

        files: dict[str, str] = {}
        max_retries = 3
        for attempt in range(max_retries):
            try:
                resp = httpx.get(
                    f"{self.BASE_URL}/download",
                    params={
                        "slug": slug,
                        "version": version
                    },
                    timeout=30,
                    follow_redirects=True,
                )
                if resp.status_code == 429:
                    try:
                        retry_after = int(resp.headers.get("retry-after", "5"))
                    except (ValueError, TypeError):
                        retry_after = 5
                    retry_after = min(retry_after, 15)  # Cap wait time
                    logger.debug(
                        "ClawHub download rate-limited for %s, retrying in %ds (attempt %d/%d)",
                        slug,
                        retry_after,
                        attempt + 1,
                        max_retries,
                    )
                    time.sleep(retry_after)
                    continue
                if resp.status_code != 200:
                    logger.debug("ClawHub ZIP download for %s v%s returned %s", slug, version, resp.status_code)
                    return files

                with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        try:
                            name = validate_bundle_rel_path(info.filename)
                        except ValueError:
                            logger.debug("Skipping unsafe ZIP member path: %s", info.filename)
                            continue
                        # Only extract text-sized files (skip large binaries)
                        if info.file_size > 500_000:
                            logger.debug("Skipping large file in ZIP: %s (%d bytes)", name, info.file_size)
                            continue
                        try:
                            raw = zf.read(info.filename)
                            files[name] = raw.decode("utf-8")
                        except (UnicodeDecodeError, KeyError):
                            logger.debug("Skipping non-text file in ZIP: %s", name)
                            continue

                return files

            except zipfile.BadZipFile:
                logger.warning("ClawHub returned invalid ZIP for %s v%s", slug, version)
                return files
            except httpx.HTTPError as exc:
                logger.debug("ClawHub ZIP download failed for %s v%s: %s", slug, version, exc)
                return files

        logger.debug("ClawHub ZIP download exhausted retries for %s v%s", slug, version)
        return files

    def _fetch_text(self, url: str) -> str | None:
        try:
            resp = httpx.get(url, timeout=20)
            if resp.status_code == 200:
                return resp.text
        except httpx.HTTPError:
            return None
        return None
