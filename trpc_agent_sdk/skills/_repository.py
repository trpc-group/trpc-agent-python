# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
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
from typing import Callable
from typing import List
from typing import Optional
from typing_extensions import override

import yaml
from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.code_executors import create_local_workspace_runtime
from trpc_agent_sdk.log import logger

from ._constants import SKILL_FILE
from ._hot_reload import SkillHotReloadTracker
from ._types import Skill
from ._types import SkillResource
from ._types import SkillSummary
from ._url_root import SkillRootResolver
from ._utils import is_doc_file
from ._utils import is_script_file

BASE_DIR_PLACEHOLDER = "__BASE_DIR__"
VisibilityFilter = Callable[[SkillSummary], bool]


class BaseSkillRepository(abc.ABC):
    """
    Base class for a source of skills.

    Defines the public contract that all skill repository implementations
    must satisfy.  Parsing internals are left entirely to subclasses.
    """

    def __init__(self, workspace_runtime: BaseWorkspaceRuntime, visibility_filter: VisibilityFilter | None = None):
        self._workspace_runtime = workspace_runtime
        self._visibility_filter = visibility_filter

    @property
    def workspace_runtime(self) -> BaseWorkspaceRuntime:
        return self._workspace_runtime

    @property
    def visibility_filter(self) -> VisibilityFilter | None:
        """Return the filter function."""
        return self._visibility_filter

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
    def skill_list(self, mode: str = 'all') -> list[str]:
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
        if self._visibility_filter is not None:
            if not self._skill_visible_by_name(skill_name, self.summaries()):
                raise ValueError(f"skill '{skill_name}' not found")
        return {}

    def _filter_summaries(
        self,
        summaries: list[SkillSummary],
    ) -> list[SkillSummary]:
        if not summaries:
            return []
        if self._visibility_filter is None:
            return [SkillSummary(name=s.name, description=s.description) for s in summaries]
        return [s for s in summaries if self._visibility_filter(s)]

    def _skill_visible_by_name(self, name: str, summaries: list[SkillSummary]) -> bool:
        return any(summary.name == name for summary in summaries)


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
        enable_hot_reload: bool = False,
    ):
        """
        Create a FsSkillRepository scanning the given roots.

        Args:
            *roots: Variable number of root directory paths (local paths,
                    ``file://`` URLs, or ``http(s)://`` archive URLs).
            workspace_runtime: Optional workspace runtime to use.
            resolver: Optional skill root resolver to use.
            enable_hot_reload: Whether to enable skill hot reload checks.
        """
        if workspace_runtime is None:
            workspace_runtime = create_local_workspace_runtime()
        super().__init__(workspace_runtime)
        self._resolver = resolver or SkillRootResolver()
        self._skill_paths: dict[str, str] = {}  # name -> base dir
        self._all_descriptions: dict[str, str] = {}  # name -> description
        self._discovered_skill_files: set[str] = set()
        self._tracked_dirs_by_root: dict[str, set[str]] = {}
        self._dir_mtime_ns: dict[str, int] = {}
        self._hot_reload_tracker = SkillHotReloadTracker(skill_file_name=SKILL_FILE, )
        self._enable_hot_reload = enable_hot_reload

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

    @property
    def hot_reload_enabled(self) -> bool:
        """Whether hot reload checks are enabled."""
        return self._enable_hot_reload

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
        self._discovered_skill_files = set()
        self._tracked_dirs_by_root = {}
        self._dir_mtime_ns = {}
        self._hot_reload_tracker.clear()
        seen: set[str] = set()

        for root in self._skill_roots:
            if not root:
                continue
            root_path = Path(root).resolve()
            root_key = str(root_path)
            if root_key in seen:
                continue
            seen.add(root_key)

            try:
                self._scan_root(root_path, root_key=root_key)
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("Error scanning root %s: %s", root_path, ex)
        if self._enable_hot_reload:
            self._hot_reload_tracker.start_watcher_if_possible(self._skill_roots)

    def _scan_root(self, root_path: Path, root_key: str, start_path: Optional[Path] = None) -> None:
        """Scan a full root or one of its changed subtrees."""
        target = start_path or root_path
        for dirpath, _dirs, _files in os.walk(target):
            self._track_dir(root_key, Path(dirpath))
            skill_file_path = Path(dirpath) / SKILL_FILE
            if not skill_file_path.is_file():
                continue
            try:
                self._index_one(dirpath, skill_file_path)
            except Exception as ex:  # pylint: disable=broad-except
                logger.debug("Failed to index skill at %s: %s dirpath: %s, _files: %s", skill_file_path, ex, _dirs,
                             _files)

    def _track_dir(self, root_key: str, path: Path) -> None:
        """Store directory metadata used by incremental hot reload."""
        dir_key = str(path.resolve())
        self._tracked_dirs_by_root.setdefault(root_key, set()).add(dir_key)
        self._dir_mtime_ns[dir_key] = self._safe_mtime_ns(path)

    @staticmethod
    def _safe_mtime_ns(path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return -1

    def _mark_changed_dir_for_hot_reload(self, path: Path) -> None:
        """Queue a changed directory; consumed by _scan_changed_dirs."""
        if not self._enable_hot_reload:
            return
        self._hot_reload_tracker.mark_changed_dir(path, self._skill_roots)

    def _index_one(self, dirpath: str, skill_file_path: Path) -> bool:
        """Index a single skill directory found at *dirpath*."""
        skill_file_key = str(skill_file_path.resolve())
        if skill_file_key in self._discovered_skill_files:
            return False
        self._discovered_skill_files.add(skill_file_key)

        front_matter, _ = self._read_skill_file(skill_file_path)
        name = front_matter.get("name", "").strip()
        if not name:
            name = Path(dirpath).name.strip()
        if not name:
            return False
        # First occurrence wins.
        if name in self._skill_paths:
            return False

        self._all_descriptions[name] = front_matter.get("description", "").strip()
        self._skill_paths[name] = dirpath
        logger.debug("Found skill '%s' at %s", name, dirpath)
        return True

    def _scan_changed_dirs(self) -> None:
        """Fast probe + incremental scan for newly added SKILL.md files."""
        if not self._enable_hot_reload:
            return
        seen_roots: set[str] = set()
        for root in self._skill_roots:
            if not root:
                continue
            root_path = Path(root).resolve()
            root_key = str(root_path)
            if root_key in seen_roots:
                continue
            seen_roots.add(root_key)

            if not root_path.is_dir():
                continue

            tracked_dirs = self._tracked_dirs_by_root.get(root_key, {root_key})
            changed_dirs = self._hot_reload_tracker.collect_changed_dirs(
                root_key=root_key,
                tracked_dirs=tracked_dirs,
                dir_mtime_ns=self._dir_mtime_ns,
                mtime_reader=self._safe_mtime_ns,
            )
            if not changed_dirs:
                continue

            for target in changed_dirs:
                self._scan_root(root_path=root_path, root_key=root_key, start_path=target)
        self._prune_deleted_skills()

    def _prune_deleted_skills(self) -> None:
        """Remove indexed skills whose directory or SKILL.md no longer exists."""
        removed_names: list[str] = []
        for name, dirpath in list(self._skill_paths.items()):
            skill_file_path = Path(dirpath) / SKILL_FILE
            if skill_file_path.is_file():
                continue
            removed_names.append(name)

        for name in removed_names:
            dirpath = self._skill_paths.pop(name, "")
            self._all_descriptions.pop(name, None)
            if dirpath:
                removed_file_key = str((Path(dirpath) / SKILL_FILE).resolve(strict=False))
                self._discovered_skill_files.discard(removed_file_key)
            logger.debug("Pruned deleted skill '%s' from repository index", name)

        stale_files = {path for path in self._discovered_skill_files if not Path(path).is_file()}
        if stale_files:
            self._discovered_skill_files.difference_update(stale_files)

    @classmethod
    def _read_skill_file(cls, path: Path) -> tuple[dict[str, str], str]:
        """Read the skill file and return the front matter and body."""
        content = path.read_text(encoding="utf-8")
        return cls.from_markdown(content)

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
            logger.warning("skill '%s' not found, refreshing repository", name)
            self.refresh()
            if key not in self._skill_paths:
                raise ValueError(f"skill '{name}' not found")
        if self._visibility_filter is not None:
            if not self._skill_visible_by_name(key, self.summaries()):
                raise ValueError(f"skill '{key}' not found")
        return self._skill_paths[key]

    @override
    def summaries(self) -> List[SkillSummary]:
        """Return summaries for all indexed skills, sorted by name."""
        self._scan_changed_dirs()
        out: list[SkillSummary] = []
        for name in sorted(self._skill_paths):
            skill_file_path = Path(self._skill_paths[name]) / SKILL_FILE
            try:
                front_matter, _ = self._read_skill_file(skill_file_path)
                summary = SkillSummary(
                    name=front_matter.get("name", "").strip(),
                    description=front_matter.get("description", "").strip(),
                )
                if not summary.name:
                    summary.name = name
                out.append(summary)
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("Failed to parse summary for skill '%s': %s", name, ex)
        if self._visibility_filter is not None:
            out = self._filter_summaries(out)
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
        front_matter, body = self._read_skill_file(dir_path / SKILL_FILE)
        skill = Skill()
        skill.base_dir = str(dir_path)
        skill.summary.name = front_matter.get("name", "").strip() or name
        skill.summary.description = front_matter.get("description", "").strip()
        skill.body = body
        skill.tools = self._parse_tools_from_body(skill.body)

        if skill.base_dir:
            skill.body = skill.body.replace(BASE_DIR_PLACEHOLDER, skill.base_dir)
        skill.resources.extend(self._read_docs(dir_path, skill.base_dir))

        return skill

    @override
    def skill_list(self, mode: str = 'all') -> list[str]:
        """Return the names of all indexed skills, sorted.

        Args:
            mode: The mode to list skills.
        Returns:
            A list of skill names.
        """
        self._scan_changed_dirs()
        return sorted(self._skill_paths)

    @override
    def refresh(self) -> None:
        """Refresh the skill repository."""
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
            if not is_doc_file(entry.name) and not is_script_file(entry.name):
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

    @staticmethod
    def _parse_tools_from_body(body: str) -> list[str]:
        """Parse tool names from the ``Tools:`` section.

        .. deprecated::
            Prefer repository-native tool parser directly.
        """
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
                        stripped.startswith(s)
                        for s in ["Overview", "Examples", "Usage", "Description", "Installation"])):
                    break
            if stripped.startswith("#"):
                continue
            if stripped.startswith("-"):
                tool_name = stripped[1:].strip()
                if tool_name and not tool_name.startswith("#"):
                    tool_names.append(tool_name)
        return tool_names


def create_default_skill_repository(
    *roots: str,
    workspace_runtime: Optional[BaseWorkspaceRuntime] = None,
    enable_hot_reload: bool = True,
) -> FsSkillRepository:
    """Create a new filesystem skill repository.

    Args:
        roots: Root directories (or URLs) to scan for skills.
        workspace_runtime: Optional workspace runtime.
        enable_hot_reload: Whether to enable skill hot reload checks.
    Returns:
        A configured :class:`FsSkillRepository`.
    """
    if workspace_runtime is None:
        workspace_runtime = create_local_workspace_runtime()
    return FsSkillRepository(
        *roots,
        workspace_runtime=workspace_runtime,
        enable_hot_reload=enable_hot_reload,
    )
