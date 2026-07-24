# Tencent is pleased to support the open source community by making
# tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Safety policy definitions. Mirrors trpc-agent-go/tool/safety/safety.go."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from ._types import Policy


def default_policy() -> Policy:
    """Return a conservative default safety policy."""
    return Policy(
        denied_commands=[
            "dd",
            "mkfs",
            "mount",
            "umount",
            "shutdown",
            "reboot",
            "halt",
            "poweroff",
            "sudo",
            "su",
            "doas",
        ],
        denied_paths=[
            "/",
            "/bin",
            "/boot",
            "/dev",
            "/etc",
            "/lib",
            "/lib64",
            "/proc",
            "/root",
            "/sbin",
            "/sys",
            "/usr",
            "/var",
            "~/.ssh",
            ".ssh",
            ".env",
            ".npmrc",
            ".pypirc",
            "id_rsa",
            "id_ed25519",
            "credentials",
            "credential",
            "secrets",
            "secret",
        ],
        network_allowlist=[
            "api.github.com",
            "github.com",
            "proxy.golang.org",
            "sum.golang.org",
            "registry.npmjs.org",
            "pypi.org",
            "files.pythonhosted.org",
        ],
        env_allowlist=[
            "PATH",
            "HOME",
            "TMPDIR",
            "TEMP",
            "TMP",
            "LANG",
            "LC_ALL",
            "CGO_ENABLED",
            "GOCACHE",
            "GOMODCACHE",
            "GOPATH",
        ],
        review_commands=[
            "go install",
            "npm install",
            "npm ci",
            "pip install",
            "pip3 install",
            "apt install",
            "apt-get install",
            "brew install",
            "cargo install",
        ],
        max_timeout_seconds=300,
        max_output_bytes=4 * 1024 * 1024,
        review_shell_pipelines=True,
        deny_on_parse_error=True,
    )


def load_policy(path: str | Path) -> Policy:
    """Load a JSON or YAML policy file, merging with defaults.

    Args:
        path: Path to .json or .yaml/.yml policy file.

    Returns:
        A Policy with file values overlaid on defaults.
    """
    path = Path(path)
    raw_bytes = path.read_bytes()

    if path.suffix.lower() in (".yaml", ".yml", ""):
        raw = yaml.safe_load(raw_bytes) or {}
    elif path.suffix.lower() == ".json":
        raw = json.loads(raw_bytes)
    else:
        raise ValueError(f"Unsupported policy extension: {path.suffix}")

    # Start from safe defaults.
    policy = default_policy()

    for key, value in raw.items():
        if hasattr(policy, key):
            setattr(policy, key, value)

    return policy
