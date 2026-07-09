# Copyright (C) 2026 Tencent. All rights reserved.
# tRPC-Agent-Python is licensed under Apache-2.0.
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from trpc_agent_sdk.tools.safety._policy import load_policy
from trpc_agent_sdk.tools.safety._scanner import scan
from trpc_agent_sdk.tools.safety._types import Decision

_MANIFEST = Path(__file__).parent / "samples" / "manifest.yaml"


def _samples():
    data = yaml.safe_load(_MANIFEST.read_text(encoding="utf-8"))
    return [(s["name"], s) for s in data["samples"]]


@pytest.mark.parametrize("name,sample", _samples(), ids=[n for n, _ in _samples()])
def test_sample(name, sample):
    policy = load_policy()
    report = scan(policy, sample["script"], language=sample["language"])
    assert report.decision == Decision[sample["expected_decision"]], (
        f"{name}: expected {sample['expected_decision']}, got {report.decision.name}; "
        f"findings={[f.rule_id for f in report.findings]}")
    hit = {f.rule_id for f in report.findings}
    for rid in sample["required_rule_ids"]:
        assert rid in hit, f"{name}: expected rule {rid} to fire; got {hit}"