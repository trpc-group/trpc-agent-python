# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Remote skill installation helpers for Skills Hub bundles.

Skill Hub adapters fetch skills as in-memory :class:`SkillBundle` objects.
This module owns the shared policy for installing those bundles into a local
filesystem skill root that :class:`FsSkillRepository` can scan.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Literal
from typing import Sequence

from trpc_agent_sdk.log import logger

from ._source import SkillSource
from ._types import validate_bundle_rel_path
from ._types import validate_category_name
from ._types import validate_skill_name

_DEFAULT_CATEGORY = "hub"


@dataclass(frozen=True)
class SkillSpec:
    """Declares one remote skill to fetch into a local skills directory."""

    source: SkillSource
    """Source to fetch the skill from, e.g. ``ClawHubSource()``."""

    identifier: str
    """Source-specific identifier passed to ``source.fetch``."""

    name: str
    """Directory name and existence-check key under any category directory."""

    category: str | None = None
    """Category directory to place the skill under.

    Defaults to the fetched bundle's ``metadata["category"]`` value, falling
    back to ``"hub"``.
    """

    replace_if_exists: bool = False
    """Whether to overwrite an already-installed skill with the same name."""

    on_error: Literal["skip", "raise"] = "skip"
    """Whether installation errors should be logged and skipped or raised."""

    def __post_init__(self) -> None:
        validate_skill_name(self.name)


def _default_install_path() -> str:
    return str(Path(tempfile.gettempdir()) / "trpc_agent_skills")


@dataclass(frozen=True)
class SkillSpecsConfig:
    """A batch of remote skills plus where to install them locally.

    ``install_path`` defaults to a stable directory under the system temp
    directory, so callers that only care about *what* to install can omit
    *where*. Reusing a stable path also lets already-installed skills be
    skipped on later runs.
    """

    specs: list[SkillSpec]
    """Remote skills to fetch."""

    install_path: str = field(default_factory=_default_install_path)
    """Writable local directory the skills are fetched into.

    Defaults to ``<system-temp>/trpc_agent_skills``.
    """


def _find_existing_skill_dirs(skills_path: Path, name: str) -> list[Path]:
    if not skills_path.is_dir():
        return []
    found: list[Path] = []
    for category_dir in skills_path.iterdir():
        if not category_dir.is_dir() or category_dir.name.startswith("."):
            continue
        candidate = category_dir / name
        if candidate.is_dir():
            found.append(candidate)
    return found


def _write_bundle_files(
    *,
    skills_path: Path,
    category: str,
    name: str,
    files: dict[str, str | bytes],
) -> None:
    safe_category = validate_category_name(category)
    target_dir = skills_path / safe_category / name

    tmp_root = skills_path / ".tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(prefix=f"{name}-", dir=tmp_root))
    try:
        for rel_path, content in files.items():
            safe_rel_path = validate_bundle_rel_path(rel_path)
            dest = staging_dir / safe_rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                dest.write_bytes(content)
            else:
                dest.write_text(content, encoding="utf-8")

        target_dir.parent.mkdir(parents=True, exist_ok=True)
        if target_dir.exists():
            shutil.rmtree(target_dir)
        staging_dir.rename(target_dir)
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        try:
            tmp_root.rmdir()
        except OSError:
            pass  # non-empty: another concurrent fetch may still be staging


def _fetch_remote_skill(remote_skill: SkillSpec, skills_path: Path) -> None:
    name = remote_skill.name

    existing = _find_existing_skill_dirs(skills_path, name)
    if existing and not remote_skill.replace_if_exists:
        logger.debug("Skipping remote skill %r: already present at %s", name, existing[0])
        return

    bundle = remote_skill.source.fetch(remote_skill.identifier)
    if bundle is None:
        raise ValueError(f"Skill source {remote_skill.source.source_id()!r} could not fetch "
                         f"identifier {remote_skill.identifier!r}.")

    category = remote_skill.category or bundle.metadata.get("category") or _DEFAULT_CATEGORY

    if existing and remote_skill.replace_if_exists:
        for existing_dir in existing:
            shutil.rmtree(existing_dir, ignore_errors=True)

    _write_bundle_files(skills_path=skills_path, category=category, name=name, files=bundle.files)
    logger.info(
        "Fetched remote skill %r from source %r into %s/%s",
        name,
        remote_skill.source.source_id(),
        category,
        name,
    )


def sync_remote_skills(remote_skills: Sequence[SkillSpec], install_root: Path) -> None:
    """Download declared-but-missing remote skills into ``install_root``.

    An existing skill under ``install_root/<any category>/<name>/`` is left
    untouched unless its declaration sets ``replace_if_exists=True``. Errors
    are handled per declaration via ``on_error``.
    """
    if not remote_skills:
        return

    install_root.mkdir(parents=True, exist_ok=True)
    for remote_skill in remote_skills:
        try:
            _fetch_remote_skill(remote_skill, install_root)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            if remote_skill.on_error == "raise":
                raise
            logger.warning(
                "Skipping remote skill (identifier=%r, source=%r): %s",
                remote_skill.identifier,
                remote_skill.source.source_id(),
                exc,
            )


async def async_sync_remote_skills(remote_skills: Sequence[SkillSpec], install_root: Path) -> None:
    """Async wrapper around :func:`sync_remote_skills`."""
    await asyncio.to_thread(sync_remote_skills, remote_skills, install_root)
