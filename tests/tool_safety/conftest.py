"""Shared fixtures for safety guard tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from trpc_agent_sdk.tools.safety._guard import ToolSafetyGuard
from trpc_agent_sdk.tools.safety._policy import load_safety_policy


REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_POLICY = (
    REPO_ROOT / "trpc_agent_sdk" / "tools" / "safety" / "examples"
    / "tool_safety_policy.yaml"
)


@pytest.fixture
def example_policy_path() -> str:
    return str(EXAMPLE_POLICY)


@pytest.fixture
def example_policy():
    return load_safety_policy(EXAMPLE_POLICY)


@pytest.fixture
def guard(example_policy):
    return ToolSafetyGuard(example_policy)


@pytest.fixture
def strict_policy_dict():
    return {
        "network": {"allow_domains": ["api.github.com", "*.internal.example.com"]},
        "commands": {"allow": ["python", "python3", "pytest", "git"],
                     "deny": ["sudo", "su", "doas"]},
        "paths": {"deny": ["~/.ssh", "/etc", "/root", ".env",
                            "**/*credentials*"]},
        "limits": {
            "max_timeout_seconds": 30,
            "max_output_bytes": 1024,
            "max_script_bytes": 65536,
            "max_sleep_seconds": 5,
            "max_parallel_tasks": 4,
            "max_processes": 4,
            "max_file_write_bytes": 4096,
        },
    }
