# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Configuration management for the code review agent.

Provides a centralized configuration object that can be loaded from
CLI arguments, environment variables, or a config file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ReviewAgentConfig:
    """Central configuration for the code review agent.

    Attributes:
        input_source: One of "diff_file", "repo_path", "fixture".
        input_value: The value for the input source (path or name).
        output_dir: Directory for output reports.
        db_path: Path to SQLite database file.
        sandbox_type: "local", "container", or "cube".
        sandbox_timeout: Sandbox execution timeout in seconds.
        sandbox_max_output: Max output size in bytes.
        sandbox_image: Docker image for container sandbox.
        dry_run: If True, skip sandbox execution and LLM calls.
        fake_model: If True, use simulated LLM results.
        disable_filters: If True, skip all filter checks.
        model_name: LLM model name (for future use).
        api_key: API key (for future use, falls back to env var).
        base_url: API base URL (for future use, falls back to env var).
        block_all_network: If True, deny all network access in sandbox.
        max_executions: Maximum script executions per review.
        max_total_time_ms: Maximum total sandbox time in ms.
        list_fixtures: If True, list available fixtures and exit.
        fixtures_dir: Path to fixtures directory.
    """

    # Input
    input_source: str = ""
    input_value: str = ""

    # Output
    output_dir: str = "."
    output_json: Optional[str] = None
    output_md: Optional[str] = None

    # Database
    db_path: str = "review.db"

    # Sandbox
    sandbox_type: str = "local"
    sandbox_timeout: int = 30
    sandbox_max_output: int = 1_048_576
    sandbox_image: str = "python:3.12-slim"

    # Execution mode
    dry_run: bool = False
    fake_model: bool = False

    # Filter
    disable_filters: bool = False
    block_all_network: bool = True
    max_executions: int = 10
    max_total_time_ms: float = 60_000.0

    # Model (for future LLM mode)
    model_name: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None

    # Fixtures
    list_fixtures: bool = False
    fixtures_dir: Optional[str] = None

    @classmethod
    def from_args(cls, args) -> ReviewAgentConfig:
        """Build config from parsed CLI arguments."""
        # Determine input source
        if args.diff_file:
            input_source = "diff_file"
            input_value = args.diff_file
        elif args.repo_path:
            input_source = "repo_path"
            input_value = args.repo_path
        elif args.fixture:
            input_source = "fixture"
            input_value = args.fixture
        else:
            input_source = ""
            input_value = ""

        # Resolve API key from env if not provided
        api_key = args.api_key or os.getenv("TRPC_AGENT_API_KEY", "")
        base_url = args.base_url or os.getenv("TRPC_AGENT_BASE_URL", "")

        return cls(
            input_source=input_source,
            input_value=input_value,
            output_dir=args.output_dir,
            output_json=args.output_json,
            output_md=args.output_md,
            db_path=args.db_path,
            sandbox_type=args.sandbox,
            sandbox_timeout=args.sandbox_timeout,
            dry_run=args.dry_run,
            fake_model=args.fake_model,
            disable_filters=args.disable_filters,
            model_name=args.model,
            api_key=api_key or None,
            base_url=base_url or None,
            list_fixtures=args.list_fixtures,
        )

    @property
    def is_fake_mode(self) -> bool:
        """True if running in dry-run or fake-model mode."""
        return self.dry_run or self.fake_model