# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for tool safety policy."""

from __future__ import annotations

from trpc_agent_sdk.tools.safety import ToolSafetyPolicy


def test_loads_policy_from_dict():
    policy = ToolSafetyPolicy.from_dict({
        "allowed_domains": ["api.example.com"],
        "allowed_commands": ["python3"],
        "denied_paths": [".env"],
        "max_timeout_seconds": 10,
        "max_output_bytes": 1024,
    })

    assert policy.is_domain_allowed("api.example.com")
    assert policy.is_domain_allowed("v1.api.example.com")
    assert not policy.is_domain_allowed("evil.example")
    assert policy.is_command_allowed("python3")
    assert policy.is_path_denied(".env")


def test_policy_file_changes_allowlist_without_code_changes(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        "\n".join([
            "allowed_domains:",
            "  - trusted.example",
            "allowed_commands:",
            "  - ls",
            "denied_paths:",
            "  - secrets.env",
            "max_timeout_seconds: 5",
            "max_output_bytes: 512",
        ]),
        encoding="utf-8",
    )

    policy = ToolSafetyPolicy.from_file(policy_path)

    assert policy.is_domain_allowed("trusted.example")
    assert policy.is_path_denied("secrets.env")
    assert policy.max_timeout_seconds == 5


def test_empty_policy_file_uses_defaults(tmp_path):
    policy_path = tmp_path / "empty.yaml"
    policy_path.write_text("", encoding="utf-8")

    policy = ToolSafetyPolicy.from_file(policy_path)

    assert policy.is_command_allowed("ls")
    assert policy.is_path_denied("~/.ssh/id_rsa")
    assert policy.max_output_bytes == 1024 * 1024


def test_url_and_path_matching_boundaries():
    policy = ToolSafetyPolicy.from_dict({
        "allowed_domains": ["api.example.com"],
        "denied_paths": ["~/.ssh", ".env", "*/.env", "*.pem", "*.key", "/root"],
    })

    assert policy.is_url_allowed("https://v1.api.example.com/status")
    assert not policy.is_url_allowed("https://api.example.com.evil/status")
    assert not policy.is_url_allowed("not-a-url")
    assert policy.is_path_denied("nested/.env")
    assert policy.is_path_denied("certs/client.pem")
    assert policy.is_path_denied("/root/.config/token")
    assert not policy.is_path_denied("")
    assert not policy.is_command_allowed("python3")
