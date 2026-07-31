# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Load the example code-review Skill as deterministic local metadata."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class SkillLoadError(ValueError):
    """Raised when a local Skill bundle is missing or invalid."""


@dataclass(frozen=True)
class SkillManifest:
    """Stable resource manifest for a local Skill bundle."""

    name: str
    description: str
    root: str
    skill_md: str
    rules: list[str]
    scripts: list[str]
    docs: list[str]
    digest: str
    frontmatter: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable manifest."""
        return asdict(self)


@dataclass(frozen=True)
class LoadedSkill:
    """A loaded Skill manifest plus readable resources."""

    manifest: SkillManifest
    body: str
    resources: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable loaded-skill view."""
        return {
            "manifest": self.manifest.to_dict(),
            "body": self.body,
            "resources": dict(sorted(self.resources.items())),
        }


def load_skill(skill_dir: str | Path) -> LoadedSkill:
    """Read a Skill bundle without registering or executing it."""
    root = Path(skill_dir).expanduser().resolve()
    if not root.is_dir():
        raise SkillLoadError(f"skill directory not found: {root}")
    skill_path = root / "SKILL.md"
    if not skill_path.is_file():
        raise SkillLoadError(f"SKILL.md not found: {skill_path}")

    frontmatter, body = _parse_skill_markdown(_read_text(skill_path, root))
    name = str(frontmatter.get("name", "")).strip()
    description = str(frontmatter.get("description", "")).strip()
    if not name:
        raise SkillLoadError("SKILL.md front matter must define a non-empty name")
    if not description:
        raise SkillLoadError("SKILL.md front matter must define a non-empty description")
    if not _is_safe_skill_name(name):
        raise SkillLoadError(f"invalid skill name: {name!r}")
    _validate_frontmatter_paths(frontmatter)

    resources = _collect_resources(root)
    rules = [path for path in resources if path.startswith("rules/") and path.endswith(".md")]
    scripts = [path for path in resources if path.startswith("scripts/")]
    docs = [path for path in resources if path.endswith(".md") and path not in {"SKILL.md", *rules}]
    digest = _compute_digest({"SKILL.md": _read_text(skill_path, root), **resources})
    manifest = SkillManifest(
        name=name,
        description=description,
        root=str(root),
        skill_md="SKILL.md",
        rules=rules,
        scripts=scripts,
        docs=docs,
        digest=digest,
        frontmatter=frontmatter,
    )
    return LoadedSkill(
        manifest=manifest,
        body=body.replace("__BASE_DIR__", str(root)),
        resources={
            path: content.replace("__BASE_DIR__", str(root))
            for path, content in resources.items()
        },
    )


def _parse_skill_markdown(content: str) -> tuple[dict[str, Any], str]:
    text = content.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        raise SkillLoadError("SKILL.md must start with YAML front matter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise SkillLoadError("SKILL.md front matter is not closed")
    try:
        frontmatter = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as ex:
        raise SkillLoadError(f"invalid SKILL.md YAML front matter: {ex}") from ex
    if not isinstance(frontmatter, dict):
        raise SkillLoadError("SKILL.md front matter must be a mapping")
    return {str(key): value for key, value in frontmatter.items()}, text[end + 5:]


def _collect_resources(root: Path) -> dict[str, str]:
    resources: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = _relative_resource_path(root, path)
        if relative is None or relative == "SKILL.md":
            continue
        if path.is_dir():
            continue
        if relative in resources:
            raise SkillLoadError(f"duplicate skill resource path: {relative}")
        resources[relative] = _read_text(path, root)
    return dict(sorted(resources.items()))


def _relative_resource_path(root: Path, path: Path) -> str | None:
    rel = path.relative_to(root)
    parts = rel.parts
    if "__pycache__" in parts or path.suffix in {".pyc", ".pyo"}:
        return None
    if any(part.startswith(".") for part in parts):
        return None
    if any(part == ".." for part in parts) or rel.is_absolute():
        raise SkillLoadError(f"unsafe skill resource path: {rel}")
    _assert_inside(root, path)
    return rel.as_posix()


def _assert_inside(root: Path, path: Path) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as ex:
        raise SkillLoadError(f"skill resource escapes root: {path}") from ex


def _read_text(path: Path, root: Path) -> str:
    _assert_inside(root, path)
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as ex:
        raise SkillLoadError(f"skill resource is not UTF-8 text: {path}") from ex


def _compute_digest(resources: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for path, content in sorted(resources.items()):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _validate_frontmatter_paths(frontmatter: dict[str, Any]) -> None:
    for key in ("rules", "scripts", "docs"):
        values = frontmatter.get(key, [])
        if values in (None, ""):
            continue
        if not isinstance(values, list):
            raise SkillLoadError(f"SKILL.md front matter field {key} must be a list")
        for value in values:
            if not isinstance(value, str):
                raise SkillLoadError(f"SKILL.md front matter field {key} contains a non-string path")
            path = Path(value)
            if path.is_absolute() or ".." in path.parts:
                raise SkillLoadError(f"unsafe path in SKILL.md front matter: {value}")


def _is_safe_skill_name(name: str) -> bool:
    return all(char.isalnum() or char in {"-", "_", "."} for char in name) and "/" not in name and "\\" not in name
