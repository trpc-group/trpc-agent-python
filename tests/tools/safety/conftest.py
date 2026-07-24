"""Shared fixtures for tool-safety tests.

The fixtures here build minimal in-memory policies so tests do not touch
the filesystem. They also expose the sample scripts shipped under
``trpc_agent_sdk/tools/safety/examples/samples`` so integration tests can
re-use them without copying the bodies.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trpc_agent_sdk.tools.safety._policy import (
    POLICY_VERSION,
    load_safety_policy_dict,
)
from trpc_agent_sdk.tools.safety._models import (
    SafetyScanRequest,
    ScriptLanguage,
    ToolKind,
)


SAMPLES_DIR = Path(__file__).resolve().parents[3] \
    / "trpc_agent_sdk" / "tools" / "safety" / "examples" / "samples"


def _policy_dict(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "version": POLICY_VERSION,
        "network": {
            "allow_domains": ["api.example.com", "*.example.com"]
        },
        "commands": {
            "allow": ["python", "ls"],
            "deny": ["rm"],
        },
        "paths": {
            "deny": ["/etc/**", "~/.ssh/**", "/root/**"]
        },
        "limits": {
            "max_timeout_seconds": 60.0,
            "max_output_bytes": 1024,
            "max_script_bytes": 262144,
            "max_sleep_seconds": 30.0,
            "max_parallel_tasks": 16,
            "max_processes": 8,
            "max_file_write_bytes": 1024,
        },
        "defaults": {
            "unknown_construct": "needs_human_review",
            "guard_error": "deny",
            "human_review_blocks_execution": True,
        },
        "dependencies": {
            "decision": "deny"
        },
        "audit": {
            "enabled": False,
            "required": False
        },
    }
    for key, value in overrides.items():
        data[key] = value
    return data


@pytest.fixture()
def policy_dict() -> dict[str, Any]:
    return _policy_dict()


@pytest.fixture()
def policy_factory():
    """Callable that builds a fresh policy from overrides."""

    return load_safety_policy_dict


@pytest.fixture()
def make_policy():
    """Build a policy with overrides applied."""

    def _make(**overrides: Any):
        return load_safety_policy_dict(_policy_dict(**overrides))

    return _make


@pytest.fixture()
def default_policy(make_policy):
    return make_policy()


@pytest.fixture()
def scan_request_factory():
    """Build a SafetyScanRequest with sensible defaults."""

    def _make(
        *,
        tool_name: str = "test_tool",
        tool_kind: ToolKind = ToolKind.UNKNOWN,
        language: ScriptLanguage = ScriptLanguage.UNKNOWN,
        script: str = "",
        argv: tuple[str, ...] = (),
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
        requested_timeout_seconds: float | None = None,
        requested_output_bytes: int | None = None,
    ) -> SafetyScanRequest:
        return SafetyScanRequest(
            tool_name=tool_name,
            tool_kind=tool_kind,
            language=language,
            script=script,
            argv=argv,
            cwd=cwd,
            env=env or {},
            metadata=metadata or {},
            requested_timeout_seconds=requested_timeout_seconds,
            requested_output_bytes=requested_output_bytes,
        )

    return _make


@pytest.fixture()
def samples_dir() -> Path:
    return SAMPLES_DIR


@pytest.fixture()
def sample_script(samples_dir):
    """Return the body of a sample script by file name."""

    def _read(name: str) -> str:
        return (samples_dir / name).read_text(encoding="utf-8")

    return _read
