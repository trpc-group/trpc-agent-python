# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Configuration dataclasses for the code-review pipeline."""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from typing import FrozenSet
from typing import Tuple

EXAMPLE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_ROOT = os.path.join(EXAMPLE_ROOT, "skills")
SKILL_NAME = "code-review"
FIXTURES_DIR = os.path.join(EXAMPLE_ROOT, "fixtures")

DEFAULT_DB_FILENAME = "review.db"

#: Environment variables the sandbox is allowed to inherit from the host.
DEFAULT_ENV_WHITELIST: FrozenSet[str] = frozenset({
    "PATH",
    "LANG",
    "LC_ALL",
    "HOME",
    "TMPDIR",
    "PYTHONIOENCODING",
})


@dataclass
class SandboxConfig:
    """Sandbox execution limits and runtime selection.

    ``runtime_kind='container'`` is the default (Docker image, native
    isolation); ``'local'`` is the development fallback used by tests and
    dry-run on hosts without Docker, hardened with an env whitelist — the CLI
    resolves ``--sandbox auto`` via :func:`resolve_sandbox_kind`. ``'cube'``
    targets Cube/E2B cloud sandboxes.
    """

    runtime_kind: str = "container"  # container | local | cube
    timeout_sec: float = 30.0
    max_output_bytes: int = 64_000
    env_whitelist: FrozenSet[str] = DEFAULT_ENV_WHITELIST
    container_image: str = "python:3.12-slim"
    work_root: str = ""
    force_fail: bool = False  # test injection: sandbox check raises deterministically


@dataclass
class PolicyConfig:
    """Filter governance policy for sandbox runs."""

    allowed_cmds: Tuple[str, ...] = ("python3", "python")
    forbidden_paths: Tuple[str, ...] = ("/etc", "/root", "/var/run/docker.sock", ".ssh", "..", "~")
    allow_network: bool = False
    max_sandbox_runs: int = 8
    max_total_sandbox_seconds: float = 90.0


@dataclass
class NoiseConfig:
    """Dedup / noise-control thresholds."""

    min_confidence: float = 0.7  # findings below go to needs_human_review


@dataclass
class ReviewConfig:
    """Top-level pipeline configuration."""

    db_url: str = ""
    out_dir: str = "out"
    model_mode: str = "fake"  # fake | real | off
    model_name: str = "fake-review-v1"
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    noise: NoiseConfig = field(default_factory=NoiseConfig)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["sandbox"]["env_whitelist"] = sorted(self.sandbox.env_whitelist)
        return data


def resolve_sandbox_kind(kind: str = "auto") -> str:
    """Resolve the ``--sandbox`` choice: ``'auto'`` → ``'container'`` when
    Docker is available, otherwise fall back — clearly logged — to the LOCAL
    dev runtime. Explicit choices pass through unchanged."""
    if kind != "auto":
        return kind
    if shutil.which("docker"):
        return "container"
    logging.getLogger(__name__).warning(
        "sandbox 'auto': docker not found on PATH — falling back to the local "
        "dev runtime (env-whitelisted, dev fallback only). Install Docker or "
        "pass --sandbox container for production isolation.")
    return "local"


def default_db_url(base_dir: str) -> str:
    """SQLite (aiosqlite) URL under ``base_dir`` — the swappable default backend."""
    return f"sqlite+aiosqlite:///{os.path.join(os.path.abspath(base_dir), DEFAULT_DB_FILENAME)}"
