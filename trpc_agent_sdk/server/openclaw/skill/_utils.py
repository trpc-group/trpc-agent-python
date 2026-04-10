# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Utils for trpc_claw skill."""

import re
import shutil
import tarfile
import zipfile
from pathlib import Path
from typing import Any
from typing import Optional

import requests


def prepare_dir(path: Path) -> None:
    """Prepare a directory.

    Args:
        path: The path to prepare.
    """
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)


def skill_file_in_dir(path: Path) -> Optional[Path]:
    """Find the skill file in the directory.

    Args:
        path: The path to find the skill file.
    """
    for name in ("SKILL.md", "skill.md"):
        candidate = path / name
        if candidate.is_file():
            return candidate
    return None


def download_file(url: str, dest: Path) -> None:
    """Download a file from a URL.

    Args:
        url: The URL to download the file from.
        dest: The path to save the file to.
    """
    response = requests.get(url, stream=True, timeout=30)
    response.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)


def extract_archive(src: Path, dest: Path) -> None:
    """Extract an archive file.

    Args:
        src: The path to the archive file.
        dest: The path to extract the archive to.
    """
    lower = src.name.lower()
    if lower.endswith(".zip"):
        with zipfile.ZipFile(src, "r") as zf:
            zf.extractall(dest)
        return
    if lower.endswith(".tar"):
        with tarfile.open(src, "r:") as tf:
            tf.extractall(dest)
        return
    if lower.endswith(".tar.gz") or lower.endswith(".tgz"):
        with tarfile.open(src, "r:gz") as tf:
            tf.extractall(dest)
        return
    raise ValueError(f"unsupported archive type: {src}")


def strip_frontmatter(content: str) -> str:
    """Remove the YAML frontmatter block from *content*.

    Args:
        content: The content to strip the frontmatter from.

    Returns:
        The content with the frontmatter stripped.
    """
    if content.startswith("---"):
        match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
        if match:
            return content[match.end():].strip()
    return content


def normalize_bundled_root(raw: Optional[str]) -> str:
    """Normalize bundled root path (Go EvalSymlinks-like behavior)."""
    value = str(raw).strip() if raw is not None else ""
    if not value:
        return ""
    try:
        # Keep original cleaned value when symlink resolution fails.
        return str(Path(value).expanduser().resolve(strict=True))
    except Exception:  # pylint: disable=broad-except
        return value


def normalize_config_key(raw: str) -> str:
    """Normalize config key (trim + lowercase)."""
    return str(raw).strip().lower()


def normalize_config_keys(keys: list[str]) -> set[str]:
    """Python equivalent of Go ``normalizeConfigKeys``."""
    if len(keys) == 0:
        return set()
    out: set[str] = set()
    for raw in keys:
        key = normalize_config_key(raw)
        if key == "":
            continue
        out.add(key)
    return out


def normalize_allowlist(allow: object) -> set[str]:
    """Python equivalent of Go ``normalizeAllowlist``."""
    if not isinstance(allow, list) or len(allow) == 0:
        return set()
    out: set[str] = set()
    for raw in allow:
        key = str(raw).strip()
        if key == "":
            continue
        out.add(key)
    return out


def normalize_skill_configs(cfg: object) -> dict[str, dict[str, Any]]:
    """Python equivalent of Go ``normalizeSkillConfigs``."""
    if not isinstance(cfg, dict) or len(cfg) == 0:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for raw_key, raw_cfg in cfg.items():
        key = str(raw_key).strip()
        if key == "":
            continue
        if hasattr(raw_cfg, "model_dump"):
            data = raw_cfg.model_dump()
        elif isinstance(raw_cfg, dict):
            data = dict(raw_cfg)
        else:
            continue
        env_in = data.get("env", {}) or {}
        env_out: dict[str, str] = {}
        if isinstance(env_in, dict):
            for env_key, env_val in env_in.items():
                k = str(env_key).strip()
                v = str(env_val).strip()
                if not k or not v:
                    continue
                env_out[k] = v
        out[key] = {
            "enabled": data.get("enabled"),
            "env": env_out,
        }
    return out


def camel_to_snake(name):
    return re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()
