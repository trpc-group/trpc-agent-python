# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Sandbox security policy (Phase 3).

The five mandatory safety boundaries applied on every sandbox execution:
timeout, output-size cap, env-variable whitelist, secret masking, and
fail-safe (no crash on error). Built from the Skill's ``sandbox_config``.
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field


@dataclass
class SandboxPolicy:
    """Five mandatory sandbox safety boundaries.

    Defaults match the code-review Skill's ``sandbox:`` frontmatter
    (ARCHITECTURE.md §6.3). A policy is built per-run from the loaded
    Skill config so the boundaries are always enforced, never bypassed.
    """

    timeout_s: int = 30
    """Max execution time; over-run → ``timed_out=True``, degraded to empty."""

    max_output_bytes: int = 1_048_576
    """1 MB output cap; excess is truncated, ``status=truncated``."""

    env_whitelist: list[str] = field(
        default_factory=lambda: ["PATH", "HOME", "LANG"]
    )
    """Only these env vars are passed into the sandbox; others dropped."""

    mask_secrets: bool = True
    """Redact secrets in stdout/stderr before truncation & persistence."""

    @classmethod
    def from_config(cls, sandbox_config: dict | None) -> "SandboxPolicy":
        """Build a policy from the Skill's ``sandbox_config`` dict.

        Unknown keys are ignored; missing keys fall back to the safe defaults.
        """
        cfg = sandbox_config or {}
        return cls(
            timeout_s=int(cfg.get("timeout_s", 30)),
            max_output_bytes=int(cfg.get("max_output_bytes", 1_048_576)),
            env_whitelist=list(cfg.get("env_whitelist", ["PATH", "HOME", "LANG"])),
            mask_secrets=True,
        )
