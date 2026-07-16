# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the fake model and the LLM review step."""
from pathlib import Path

from trpc_agent_sdk.skills import create_default_skill_repository

from agent.agent import create_review_agent
from agent.fake_model import FakeReviewModel
from review.findings import Finding
from review.llm_review import parse_llm_output, run_llm_review

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = EXAMPLE_ROOT / "skills"


def test_parse_llm_output_plain_json():
    findings, summary = parse_llm_output(
        '{"summary": "ok", "findings": [{"severity": "high", "category": "security",'
        ' "file": "a.py", "line": 3, "title": "x", "confidence": 0.8}]}')
    assert summary == "ok"
    assert findings[0]["line"] == 3


def test_parse_llm_output_fenced_json():
    text = 'Here you go:\n```json\n{"summary": "s", "findings": []}\n```\n'
    findings, summary = parse_llm_output(text)
    assert summary == "s" and findings == []


def test_parse_llm_output_garbage_returns_empty():
    findings, summary = parse_llm_output("I could not review this.")
    assert findings == [] and summary == ""


def test_fake_model_supported_models():
    assert FakeReviewModel.supported_models() == [r"fake-review-.*"]


async def test_run_llm_review_with_fake_model():
    repository = create_default_skill_repository(str(SKILLS_DIR))
    agent = create_review_agent(repository, dry_run=True, tool_filters=[])
    static = [Finding(severity="high", category="security", file="a.py", line=1,
                      title="eval", confidence=0.9)]
    llm_findings, summary, warnings = await run_llm_review(agent, "diff text", static)
    assert llm_findings == []
    assert "Dry-run review complete" in summary
    assert warnings == []
