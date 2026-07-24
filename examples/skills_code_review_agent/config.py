# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Review pipeline configuration for the code review agent.

Holds all settings for a single review run: input source, sandbox type,
output paths, dry-run mode, etc. Read from environment variables with
sensible defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ReviewAgentConfig:
    """Configuration for a single code review pipeline run.

    Attributes:
        input_source: Type of input ("diff_file", "fixture", "repo_path").
        input_value: The actual input — raw diff text, fixture name, or repo path.
        output_dir: Directory for output reports and working files.
        sandbox_type: Sandbox executor type ("local", "container", "cube").
        dry_run: If True, skip sandbox execution and LLM calls.
        fake_model: If True, use DryRunEngine instead of LLM.
        db_path: Path to the SQLite database file.
        sandbox_timeout: Max seconds for each sandbox execution (default 30).
        sandbox_max_output: Max bytes for sandbox output (default 1MB).
    """
    input_source: str = "diff_file"
    input_value: str = ""
    output_dir: str = ""
    sandbox_type: str = "local"
    dry_run: bool = False
    fake_model: bool = False
    db_path: str = "reviewmind.db"
    sandbox_timeout: int = 30
    sandbox_max_output: int = 1_048_576  # 1MB

    @classmethod
    def from_env(cls, **overrides: str | bool | int) -> ReviewAgentConfig:
        """Create config from environment variables with optional overrides."""
        return cls(
            sandbox_type=os.getenv("REVIEWMIND_SANDBOX", overrides.get("sandbox_type", "local")),
            dry_run=os.getenv("REVIEWMIND_DRY_RUN", str(overrides.get("dry_run", "false"))).lower() == "true",
            db_path=os.getenv("REVIEWMIND_DB_PATH", overrides.get("db_path", "reviewmind.db")),
            sandbox_timeout=int(os.getenv("REVIEWMIND_SANDBOX_TIMEOUT", str(overrides.get("sandbox_timeout", 30)))),
            **{k: v for k, v in overrides.items() if k not in ("sandbox_type", "dry_run", "db_path", "sandbox_timeout")},
        )