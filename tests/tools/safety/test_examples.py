# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for tool safety boundary sample fixtures."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import ToolSafetyGuard
from trpc_agent_sdk.tools.safety import ToolScriptScanRequest
from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_DIR = REPO_ROOT / "examples" / "tool_safety"
SAMPLE_DIR = Path(__file__).resolve().parent / "samples"


def _language_for(path: Path) -> str:
    if path.suffix == ".py":
        return "python"
    if path.suffix == ".sh":
        return "bash"
    return "unknown"


@pytest.mark.asyncio
async def test_public_examples_guard_results_and_audit_match_manifest(tmp_path):
    policy = ToolSafetyPolicy.from_file(EXAMPLE_DIR / "tool_safety_policy.yaml")
    manifest = yaml.safe_load((SAMPLE_DIR / "manifest.yaml").read_text(encoding="utf-8"))
    samples = manifest["samples"]
    sample_names = {path.name for path in SAMPLE_DIR.iterdir() if path.is_file()}
    expected_names = {sample["file"] for sample in samples}

    assert sample_names == expected_names | {"manifest.yaml"}
    assert len(samples) >= 40

    audit_path = tmp_path / "samples-audit.jsonl"
    guard = ToolSafetyGuard(
        scanner=ToolScriptSafetyScanner(policy),
        audit_log_path=audit_path,
    )
    execution_results = []
    for sample in samples:
        name = sample["file"]
        path = SAMPLE_DIR / name
        execute = AsyncMock(return_value={"status": "executed", "sample": name})
        result = await guard.run(
            ToolScriptScanRequest(
                script=path.read_text(encoding="utf-8"),
                language=_language_for(path),
                tool_name=name,
            ),
            execute,
        )
        report = result.report
        rule_ids = {finding.rule_id for finding in report.findings}

        assert report.decision.value == sample["expected_decision"], name
        assert set(sample.get("required_rule_ids", [])) <= rule_ids, name
        assert "decision" in report.to_dict()
        assert "risk_level" in report.to_dict()
        if sample["expected_decision"] == "deny":
            assert result.blocked is True, name
            assert result.result is None, name
            execute.assert_not_called()
        else:
            assert result.blocked is False, name
            assert result.result == {"status": "executed", "sample": name}
            execute.assert_awaited_once()
        execution_results.append(result)

    audit_events = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(audit_events) == len(samples)
    for sample, result, event in zip(samples, execution_results, audit_events):
        assert event["tool_name"] == sample["file"]
        assert event["decision"] == sample["expected_decision"]
        assert event["blocked"] is result.blocked
        assert set(sample.get("required_rule_ids", [])) <= set(event["rule_ids"])
        assert event["trace_attributes"]["tool.safety.blocked"] is result.blocked


def test_custom_policy_example_is_runnable():
    example_path = EXAMPLE_DIR / "custom_policy_example.py"
    spec = importlib.util.spec_from_file_location("custom_policy_example", example_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    scanner = module.build_scanner_from_dict()
    reports = module.scan_demo_cases(scanner)

    assert reports["allowed_domain"]["decision"] == "allow"
    assert reports["blocked_domain"]["decision"] == "deny"
    assert reports["custom_rule"]["decision"] == "deny"
    assert reports["prompt_bypass_attempt"]["decision"] == "deny"
    assert any(
        finding["rule_id"] == "CUSTOM_INTERNAL_ADMIN_COMMAND" for finding in reports["custom_rule"]["findings"]
    )
