# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""GitHub repo source adapter (Contents / Git Trees API)."""

from __future__ import annotations

import re

import httpx
import yaml
from trpc_agent_sdk.log import logger

from ._source import SkillSource
from ._types import SkillBundle
from ._types import SkillMeta


class GitHubAuth:
    """GitHub API authentication via a personal access token (PAT).

    The token must be passed explicitly by the caller (e.g. sourced from your
    own secrets manager) rather than auto-detected from the environment or a
    local `gh` CLI — this SDK may run multi-tenant, unlike a single-tenant CLI
    tool. Requests are unauthenticated (60 req/hr, public repos only) when no
    token is provided.
    """

    def __init__(self, token: str | None = None):
        self._token = token

    def get_headers(self) -> dict[str, str]:
        """Return authorization headers for GitHub API requests."""
        headers = {"Accept": "application/vnd.github.v3+json"}
        if self._token:
            headers["Authorization"] = f"token {self._token}"
        return headers

    def is_authenticated(self) -> bool:
        return bool(self._token)


class GitHubSource(SkillSource):
    """Fetch skills from GitHub repos via the Contents API.

    `search` only looks at repos the caller explicitly declares via `taps`
    (there is no built-in default tap list) — each tap is a
    `{"repo": "owner/repo", "path": "skills/"}` mapping. `fetch`/`inspect`
    don't need `taps` at all since they take a full identifier directly.
    """

    def __init__(self, auth: GitHubAuth, taps: list[dict] | None = None):
        self.auth = auth
        self.taps = list(taps) if taps else []
        # Set when GitHub returns 403 with rate limit exhausted
        self._rate_limited: bool = False

    def source_id(self) -> str:
        return "github"

    @property
    def is_rate_limited(self) -> bool:
        """Whether GitHub API rate limit was hit during operations."""
        return self._rate_limited

    def search(self, query: str, limit: int = 10) -> list[SkillMeta]:
        """Search all taps for skills matching the query."""
        results: list[SkillMeta] = []
        query_lower = query.lower()

        for tap in self.taps:
            try:
                skills = self._list_skills_in_repo(tap["repo"], tap.get("path", ""))
                for skill in skills:
                    searchable = f"{skill.name} {skill.description} {' '.join(skill.tags)}".lower()
                    if query_lower in searchable:
                        results.append(skill)
            except Exception as e:
                logger.debug(f"Failed to search {tap['repo']}: {e}")
                continue

        # Deduplicate by name, keeping the first match.
        seen: dict[str, SkillMeta] = {}
        for r in results:
            if r.name not in seen:
                seen[r.name] = r
        results = list(seen.values())

        return results[:limit]

    def fetch(self, identifier: str) -> SkillBundle | None:
        """
        Download a skill from GitHub.
        identifier format: "owner/repo/path/to/skill-dir"
        """
        parts = identifier.split("/", 2)
        if len(parts) < 3:
            return None

        repo = f"{parts[0]}/{parts[1]}"
        skill_path = parts[2]

        files = self._download_directory(repo, skill_path)
        if not files or "SKILL.md" not in files:
            return None

        skill_name = skill_path.rstrip("/").split("/")[-1]

        return SkillBundle(
            name=skill_name,
            files=files,
            source="github",
            identifier=identifier,
        )

    def inspect(self, identifier: str) -> SkillMeta | None:
        """Fetch just the SKILL.md metadata for preview."""
        parts = identifier.split("/", 2)
        if len(parts) < 3:
            return None

        repo = f"{parts[0]}/{parts[1]}"
        skill_path = parts[2].rstrip("/")
        skill_md_path = f"{skill_path}/SKILL.md"

        content = self._fetch_file_content(repo, skill_md_path)
        if not content:
            return None

        fm = self._parse_frontmatter_quick(content)
        skill_name = fm.get("name", skill_path.split("/")[-1])
        description = fm.get("description", "")

        tags = []
        metadata = fm.get("metadata", {})
        if isinstance(metadata, dict):
            hermes_meta = metadata.get("hermes", {})
            if isinstance(hermes_meta, dict):
                tags = hermes_meta.get("tags", [])
        if not tags:
            raw_tags = fm.get("tags", [])
            tags = raw_tags if isinstance(raw_tags, list) else []

        return SkillMeta(
            name=skill_name,
            description=str(description),
            source="github",
            identifier=identifier,
            repo=repo,
            path=skill_path,
            tags=[str(t) for t in tags],
        )

    # -- Internal helpers --

    def _list_skills_in_repo(self, repo: str, path: str) -> list[SkillMeta]:
        """List skill directories in a GitHub repo path."""
        url = f"https://api.github.com/repos/{repo}/contents/{path.rstrip('/')}"
        try:
            resp = httpx.get(url, headers=self.auth.get_headers(), timeout=15, follow_redirects=True)
            if resp.status_code != 200:
                return []
        except httpx.HTTPError:
            return []

        entries = resp.json()
        if not isinstance(entries, list):
            return []

        skills: list[SkillMeta] = []
        for entry in entries:
            if entry.get("type") != "dir":
                continue

            dir_name = entry["name"]
            if dir_name.startswith((".", "_")):
                continue

            prefix = path.rstrip("/")
            skill_identifier = f"{repo}/{prefix}/{dir_name}" if prefix else f"{repo}/{dir_name}"
            meta = self.inspect(skill_identifier)
            if meta:
                skills.append(meta)

        return skills

    # -- Repo tree (Git Trees API) --

    def _get_repo_tree(self, repo: str) -> tuple[str, list[dict]] | None:
        """Fetch the recursive repo tree via the Git Trees API.

        Returns ``(default_branch, tree_entries)`` or ``None``.
        """
        headers = self.auth.get_headers()

        # Resolve default branch
        try:
            resp = httpx.get(
                f"https://api.github.com/repos/{repo}",
                headers=headers,
                timeout=15,
                follow_redirects=True,
            )
            if resp.status_code != 200:
                self._check_rate_limit_response(resp)
                return None
            default_branch = resp.json().get("default_branch", "main")
        except (httpx.HTTPError, ValueError):
            return None

        # Fetch recursive tree
        try:
            resp = httpx.get(
                f"https://api.github.com/repos/{repo}/git/trees/{default_branch}",
                params={"recursive": "1"},
                headers=headers,
                timeout=30,
                follow_redirects=True,
            )
            if resp.status_code != 200:
                self._check_rate_limit_response(resp)
                return None
            tree_data = resp.json()
            if tree_data.get("truncated"):
                logger.debug("Git tree truncated for %s, cannot use tree API", repo)
                return None
        except (httpx.HTTPError, ValueError):
            return None

        entries = tree_data.get("tree", [])
        return (default_branch, entries)

    def _check_rate_limit_response(self, resp: "httpx.Response") -> None:
        """Flag the instance as rate-limited when GitHub returns 403 + exhausted quota."""
        if resp.status_code == 403:
            remaining = resp.headers.get("X-RateLimit-Remaining", "")
            if remaining == "0":
                self._rate_limited = True
                logger.warning("GitHub API rate limit exhausted (unauthenticated: 60 req/hr). "
                               "Set GITHUB_TOKEN or install the gh CLI to raise the limit to 5,000/hr.")

    def _download_directory(self, repo: str, path: str) -> dict[str, str]:
        """Recursively download all text files from a GitHub directory.

        Uses the Git Trees API first (single call for the entire tree) to
        avoid per-directory rate limiting that causes silent subdirectory
        loss.  Falls back to the recursive Contents API when the tree
        endpoint is unavailable or the response is truncated.
        """
        files = self._download_directory_via_tree(repo, path)
        if files is not None:
            return files
        logger.debug("Tree API unavailable for %s/%s, falling back to Contents API", repo, path)
        return self._download_directory_recursive(repo, path)

    def _download_directory_via_tree(self, repo: str, path: str) -> dict[str, str] | None:
        """Download an entire directory using the Git Trees API (single request).

        Returns:
            dict of files if the path exists and has content,
            empty dict ``{}`` if the tree was fetched but the path doesn't exist
            (prevents unnecessary Contents API fallback),
            ``None`` if the tree couldn't be fetched (triggers Contents API fallback).
        """
        path = path.rstrip("/")

        tree_result = self._get_repo_tree(repo)
        if tree_result is None:
            return None
        _default_branch, tree_entries = tree_result

        # Check if ANY entry lives under the target path
        prefix = f"{path}/"
        has_entries = any(item.get("path", "").startswith(prefix) for item in tree_entries)
        if not has_entries:
            # Path definitively doesn't exist in the repo — return empty
            # instead of None to skip the Contents API fallback.
            return {}

        # Filter to blobs under our target path and fetch content
        files: dict[str, str] = {}
        for item in tree_entries:
            if item.get("type") != "blob":
                continue
            item_path = item.get("path", "")
            if not item_path.startswith(prefix):
                continue
            rel_path = item_path[len(prefix):]
            content = self._fetch_file_content(repo, item_path)
            if content is not None:
                files[rel_path] = content
            else:
                logger.debug("Skipped file (fetch failed): %s/%s", repo, item_path)

        return files if files else None

    def _download_directory_recursive(self, repo: str, path: str) -> dict[str, str]:
        """Recursively download via Contents API (fallback)."""
        url = f"https://api.github.com/repos/{repo}/contents/{path.rstrip('/')}"
        try:
            resp = httpx.get(url, headers=self.auth.get_headers(), timeout=15, follow_redirects=True)
            if resp.status_code != 200:
                logger.debug("Contents API returned %d for %s/%s", resp.status_code, repo, path)
                return {}
        except httpx.HTTPError:
            return {}

        entries = resp.json()
        if not isinstance(entries, list):
            return {}

        files: dict[str, str] = {}
        for entry in entries:
            name = entry.get("name", "")
            entry_type = entry.get("type", "")

            if entry_type == "file":
                content = self._fetch_file_content(repo, entry.get("path", ""))
                if content is not None:
                    rel_path = name
                    files[rel_path] = content
            elif entry_type == "dir":
                sub_files = self._download_directory_recursive(repo, entry.get("path", ""))
                if not sub_files:
                    logger.debug("Empty or failed subdirectory: %s/%s", repo, entry.get("path", ""))
                for sub_name, sub_content in sub_files.items():
                    files[f"{name}/{sub_name}"] = sub_content

        return files

    def _find_skill_in_repo_tree(self, repo: str, skill_name: str) -> str | None:
        """Use the GitHub Trees API to find a skill directory anywhere in the repo.

        Returns the full identifier (``repo/path/to/skill``) or ``None``.
        This is a single API call regardless of repo depth, so it efficiently
        handles deeply nested directory structures like
        ``cli-tool/components/skills/development/<skill>/SKILL.md``.
        """
        tree_result = self._get_repo_tree(repo)
        if tree_result is None:
            return None
        _default_branch, tree_entries = tree_result

        # Look for SKILL.md files inside directories named <skill_name>
        skill_md_suffix = f"/{skill_name}/SKILL.md"
        for entry in tree_entries:
            if entry.get("type") != "blob":
                continue
            path = entry.get("path", "")
            if path.endswith(skill_md_suffix) or path == f"{skill_name}/SKILL.md":
                # Strip /SKILL.md to get the skill directory path
                skill_dir = path[:-len("/SKILL.md")]
                return f"{repo}/{skill_dir}"

        return None

    def _fetch_file_content(self, repo: str, path: str) -> str | None:
        """Fetch a single file's content from GitHub."""
        url = f"https://api.github.com/repos/{repo}/contents/{path}"
        try:
            resp = httpx.get(
                url,
                headers={
                    **self.auth.get_headers(), "Accept": "application/vnd.github.v3.raw"
                },
                timeout=15,
                follow_redirects=True,
            )
            if resp.status_code == 200:
                return resp.text
            self._check_rate_limit_response(resp)
        except httpx.HTTPError as e:
            logger.debug("GitHub contents API fetch failed: %s", e)
        return None

    @staticmethod
    def _parse_frontmatter_quick(content: str) -> dict:
        """Parse YAML frontmatter from SKILL.md content."""
        if not content.startswith("---"):
            return {}
        match = re.search(r'\n---\s*\n', content[3:])
        if not match:
            return {}
        yaml_text = content[3:match.start() + 3]
        try:
            parsed = yaml.safe_load(yaml_text)
            return parsed if isinstance(parsed, dict) else {}
        except yaml.YAMLError:
            return {}
