# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests that public tool safety examples remain runnable."""

from __future__ import annotations

from pathlib import Path

import yaml

from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_DIR = REPO_ROOT / "examples" / "tool_safety"


def _language_for(path: Path) -> str:
    if path.suffix == ".py":
        return "python"
    if path.suffix == ".sh":
        return "bash"
    return "unknown"


def test_public_examples_scan_to_expected_decisions():
    policy = ToolSafetyPolicy.from_file(EXAMPLE_DIR / "tool_safety_policy.yaml")
    scanner = ToolScriptSafetyScanner(policy)
    manifest = yaml.safe_load((EXAMPLE_DIR / "samples" / "manifest.yaml").read_text(encoding="utf-8"))
    samples = manifest["samples"]
    sample_names = {path.name for path in (EXAMPLE_DIR / "samples").iterdir() if path.is_file()}
    expected_names = {sample["file"] for sample in samples}

    assert sample_names == expected_names | {"manifest.yaml"}
    assert len(samples) >= 40

    for sample in samples:
        name = sample["file"]
        path = EXAMPLE_DIR / "samples" / name
        report = scanner.scan_file(path, language=_language_for(path), tool_name=name)
        rule_ids = {finding.rule_id for finding in report.findings}

        assert report.decision.value == sample["expected_decision"], name
        assert set(sample.get("required_rule_ids", [])) <= rule_ids, name
        assert "decision" in report.to_dict()
        assert "risk_level" in report.to_dict()
