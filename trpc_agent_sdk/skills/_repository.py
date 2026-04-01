# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""
Skill repository module.

This module provides a model-agnostic Agent Skills repository.
A skill is a folder containing a SKILL.md file with optional YAML front
matter and a Markdown body, plus optional doc files.

This file implements filesystem scanning plus SKILL.md parsing directly.
"""

from __future__ import annotations

import abc
import os
from pathlib import Path
from typing import List
from typing import Optional
from typing_extensions import override

import yaml
from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.code_executors import create_local_workspace_runtime
from trpc_agent_sdk.log import logger

from ._constants import SKILL_FILE
from ._types import Skill
from ._types import SkillResource
from ._types import SkillSummary
from ._url_root import SkillRootResolver

BASE_DIR_PLACEHOLDER = "__BASE_DIR__"


def _split_front_matter(content: str) -> tuple[dict[str, str], str]:
    """Split markdown into (front matter dict, body) with optional YAML front matter."""
    text = content.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}, text
    idx = text.find("\n---\n", 4)
    if idx < 0:
        return {}, text
    raw_yaml = text[4:idx]
    body = text[idx + 5:]
    try:
        parsed = yaml.safe_load(raw_yaml) or {}
        if not isinstance(parsed, dict):
            return {}, body
    except Exception:
        return {}, body
    out: dict[str, str] = {}
    for k, v in parsed.items():
        key = str(k).strip()
        if not key:
            continue
        if v is None:
            out[key] = ""
        else:
            out[key] = str(v)
    return out, body


def _parse_tools_from_body(body: str) -> list[str]:
    """Parse tool names from the Tools section in body text."""
    tool_names: list[str] = []
    in_tools_section = False
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.lower().startswith("tools:"):
            in_tools_section = True
            continue
        if not in_tools_section:
            continue
        if stripped and not stripped.startswith("-") and not stripped.startswith("#"):
            if ":" in stripped or (stripped[0].isupper() and any(
                    stripped.startswith(s) for s in ["Overview", "Examples", "Usage", "Description", "Installation"])):
                break
        if stripped.startswith("#"):
            continue
        if stripped.startswith("-"):
            tool_name = stripped[1:].strip()
            if tool_name and not tool_name.startswith("#"):
                tool_names.append(tool_name)
    return tool_names


def _is_doc_file(name: str) -> bool:
    name_lower = name.lower()
    return name_lower.endswith(".md") or name_lower.endswith(".txt")


def _read_skill_file(path: Path) -> tuple[dict[str, str], str]:
    content = path.read_text(encoding="utf-8")
    return _split_front_matter(content)


class BaseSkillRepository(abc.ABC):
    """
    Base class for a source of skills.

    Defines the public contract that all skill repository implementations
    must satisfy.  Parsing internals are left entirely to subclasses.
    """

    def __init__(self, workspace_runtime: BaseWorkspaceRuntime):
        self._workspace_runtime = workspace_runtime

    @property
    def workspace_runtime(self) -> BaseWorkspaceRuntime:
        return self._workspace_runtime

    def user_prompt(self) -> str:
        return ""

    @abc.abstractmethod
    def summaries(self) -> List[SkillSummary]:
        """Return summaries for all indexed skills."""
        raise NotImplementedError

    @abc.abstractmethod
    def get(self, name: str) -> Skill:
        """Return a full :class:`Skill` by name.

        Raises:
            ValueError: If the skill is not found.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def skill_list(self) -> list[str]:
        """Return the names of all indexed skills."""
        raise NotImplementedError

    @abc.abstractmethod
    def path(self, name: str) -> str:
        """Return the directory path that contains the given skill.

        Raises:
            ValueError: If the skill is not found.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def refresh(self) -> None:
        """Refresh skill roots and rebuild repository index."""
        raise NotImplementedError

    def skill_run_env(self, skill_name: str) -> dict[str, str]:
        """Return the environment variables for the given skill.
        """
        return {}


class FsSkillRepository(BaseSkillRepository):
    """
    Implements :class:`BaseSkillRepository` backed by filesystem roots.

    Scans one or more root directories for ``SKILL.md`` files.

    The special placeholder ``__BASE_DIR__`` inside skill body and doc files
    is replaced at :meth:`get` time with the skill's absolute directory path.
    """

    def __init__(
        self,
        *roots: str,
        workspace_runtime: Optional[BaseWorkspaceRuntime] = None,
        resolver: Optional[SkillRootResolver] = None,
    ):
        """
        Create a FsSkillRepository scanning the given roots.

        Args:
            *roots: Variable number of root directory paths (local paths,
                    ``file://`` URLs, or ``http(s)://`` archive URLs).
            workspace_runtime: Optional workspace runtime to use.
            resolver: Optional skill root resolver to use.
        """
        if workspace_runtime is None:
            workspace_runtime = create_local_workspace_runtime()
        super().__init__(workspace_runtime)
        self._resolver = resolver or SkillRootResolver()
        self._skill_paths: dict[str, str] = {}  # name -> base dir
        self._all_descriptions: dict[str, str] = {}  # name -> description

        self._skill_roots: list[str] = []
        flat_roots: list[str] = []
        for root in roots:
            if isinstance(root, str):
                flat_roots.append(root)
            elif isinstance(root, list):
                flat_roots.extend(root)
        self._resolve_skill_roots(flat_roots)
        self.refresh()

    # ------------------------------------------------------------------
    # Root resolution
    # ------------------------------------------------------------------

    def _resolve_skill_roots(self, roots: list[str]) -> None:
        """
        Resolve a skill root string to a local directory path.

        Args:
            roots: The list of skill root strings to resolve.

        Returns:
            The local directory path.
        """
        for root in roots:
            try:
                path = self._resolver.resolve(root)
                self._skill_roots.append(path)
            except Exception as ex:
                logger.warning("Failed to resolve skill root %s: %s", root, ex)
                continue

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def _index(self) -> None:
        """Scan all roots and index available skills."""
        self._skill_paths = {}
        self._all_descriptions = {}
        seen: set[str] = set()

        for root in self._skill_roots:
            if not root:
                continue
            root_path = Path(root).resolve()
            if str(root_path) in seen:
                continue
            seen.add(str(root_path))

            try:
                for dirpath, _dirs, _files in os.walk(root_path):
                    skill_file_path = Path(dirpath) / SKILL_FILE
                    if not skill_file_path.is_file():
                        continue
                    try:
                        self._index_one(dirpath, skill_file_path)
                    except Exception as ex:  # pylint: disable=broad-except
                        logger.debug("Failed to index skill at %s: %s", skill_file_path, ex)

            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("Error scanning root %s: %s", root_path, ex)

    def _index_one(self, dirpath: str, skill_file_path: Path) -> None:
        """Index a single skill directory found at *dirpath*."""
        front_matter, _ = _read_skill_file(skill_file_path)
        name = front_matter.get("name", "").strip()
        if not name:
            name = Path(dirpath).name.strip()
        if not name:
            return
        # First occurrence wins.
        if name in self._skill_paths:
            return

        self._all_descriptions[name] = front_matter.get("description", "").strip()
        self._skill_paths[name] = dirpath
        logger.debug("Found skill '%s' at %s", name, dirpath)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @override
    def path(self, name: str) -> str:
        """Return the directory path that contains the given skill.

        Raises:
            ValueError: If the skill is not found.
        """
        key = name.strip()
        if key not in self._skill_paths:
            raise ValueError(f"skill '{name}' not found")
        return self._skill_paths[key]

    @override
    def summaries(self) -> List[SkillSummary]:
        """Return summaries for all indexed skills, sorted by name."""
        out: list[SkillSummary] = []
        for name in sorted(self._skill_paths):
            skill_file_path = Path(self._skill_paths[name]) / SKILL_FILE
            try:
                front_matter, _ = _read_skill_file(skill_file_path)
                summary = SkillSummary(
                    name=front_matter.get("name", "").strip(),
                    description=front_matter.get("description", "").strip(),
                )
                if not summary.name:
                    summary.name = name
                out.append(summary)
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("Failed to parse summary for skill '%s': %s", name, ex)
        return out

    @override
    def get(self, name: str) -> Skill:
        """Return a full :class:`Skill` by name.

        The ``__BASE_DIR__`` placeholder inside the skill body and all doc
        files is replaced with the skill's absolute directory path.

        Raises:
            ValueError: If the skill is not found.
        """
        dir_path = Path(self.path(name))
        front_matter, body = _read_skill_file(dir_path / SKILL_FILE)
        skill = Skill()
        skill.base_dir = str(dir_path)
        skill.summary.name = front_matter.get("name", "").strip() or name
        skill.summary.description = front_matter.get("description", "").strip()
        skill.body = body
        skill.tools = _parse_tools_from_body(skill.body)

        if skill.base_dir:
            skill.body = skill.body.replace(BASE_DIR_PLACEHOLDER, skill.base_dir)
        skill.resources.extend(self._read_docs(dir_path, skill.base_dir))

        return skill

    @override
    def skill_list(self) -> list[str]:
        """Return the names of all indexed skills, sorted."""
        return sorted(self._skill_paths)

    @override
    def refresh(self) -> None:
        self._index()

    def _read_docs(self, dir_path: Path, base_dir: str) -> list[SkillResource]:
        """Read auxiliary docs (readDocs equivalent in Go repository)."""
        docs: list[SkillResource] = []
        for entry in dir_path.rglob("*"):
            if not entry.is_file():
                continue
            if entry.name.startswith(".") or ".git" in entry.parts:
                continue
            if entry.name.lower() == SKILL_FILE.lower():
                continue
            if not _is_doc_file(entry.name):
                continue
            try:
                content = entry.read_text(encoding="utf-8")
                if base_dir:
                    content = content.replace(BASE_DIR_PLACEHOLDER, base_dir)
                rel_path = entry.relative_to(dir_path).as_posix()
                docs.append(SkillResource(path=rel_path, content=content))
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("Failed to read doc file %s: %s", entry, ex)
        docs.sort(key=lambda d: d.path)
        return docs

    # ------------------------------------------------------------------
    # Backward-compatible shims
    # ------------------------------------------------------------------

    @classmethod
    def from_markdown(cls, content: str) -> tuple[dict[str, str], str]:
        """Split SKILL.md content into ``(frontmatter dict, body)``.

        .. deprecated::
            Prefer repository-native front matter splitting directly.
        """
        return _split_front_matter(content)

    @staticmethod
    def _parse_tools_from_body(body: str) -> list[str]:
        """Parse tool names from the ``Tools:`` section.

        .. deprecated::
            Prefer repository-native tool parser directly.
        """
        return _parse_tools_from_body(body)


def create_default_skill_repository(
    *roots: str,
    workspace_runtime: Optional[BaseWorkspaceRuntime] = None,
) -> FsSkillRepository:
    """Create a new filesystem skill repository.

    Args:
        roots: Root directories (or URLs) to scan for skills.
        workspace_runtime: Optional workspace runtime.
    Returns:
        A configured :class:`FsSkillRepository`.
    """
    if workspace_runtime is None:
        workspace_runtime = create_local_workspace_runtime()
    return FsSkillRepository(
        *roots,
        workspace_runtime=workspace_runtime,
    )
