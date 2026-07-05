# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Smoke tests for the skills_code_review_agent example.

Deterministic and fast: no real model, no Docker. Verifies the dry-run pipeline detects issues,
never leaks a plaintext secret, dedups correctly, and persists a task queryable by id.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "skills_code_review_agent"
if str(_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_DIR))

from pipeline import report as report_mod  # noqa: E402
from pipeline.dedup import dedup_and_denoise  # noqa: E402
from pipeline.engine import run_review  # noqa: E402
from pipeline.redaction import redact  # noqa: E402
from pipeline.types import Finding  # noqa: E402

_FIXTURES = _EXAMPLE_DIR / "fixtures" / "diffs"
_SECRETS = ["hunter2supersecret", "AKIA1234567890ABCDEF"]


def test_detects_issues_across_categories() -> None:
    result = run_review(diff_text=(_FIXTURES / "0001_insecure.diff").read_text())
    cats = {f.category for f in result.report.findings}
    assert "security" in cats
    assert result.report.findings_summary["total"] >= 3


def test_clean_diff_has_no_active_findings() -> None:
    result = run_review(diff_text=(_FIXTURES / "0005_clean.diff").read_text())
    assert result.report.findings_summary["total"] == 0


def test_no_plaintext_secret_in_rendered_report() -> None:
    result = run_review(diff_text=(_FIXTURES / "0001_insecure.diff").read_text())
    blob = report_mod.render_json(result.report) + report_mod.render_md(result.report)
    for secret in _SECRETS:
        assert secret not in blob


def test_redact_masks_common_secrets() -> None:
    assert "hunter2" not in redact('password = "hunter2supersecret"')
    assert "AKIA1234567890ABCDEF" not in redact('key = "AKIA1234567890ABCDEF"')


def test_dedup_collapses_same_file_line_category() -> None:
    a = Finding(severity="high",
                category="security",
                file="x.py",
                line=1,
                title="t",
                evidence="e",
                recommendation="r",
                confidence=0.9,
                source="static")
    b = a.model_copy(update={"confidence": 0.5})
    out = dedup_and_denoise([a, b])
    active = [f for f in out if f.status == "active"]
    dupes = [f for f in out if f.status == "duplicate"]
    assert len(active) == 1 and active[0].confidence == 0.9
    assert len(dupes) == 1


def test_low_confidence_routed_to_human_review() -> None:
    f = Finding(severity="low",
                category="security",
                file="x.py",
                line=2,
                title="t",
                evidence="e",
                recommendation="r",
                confidence=0.2,
                source="static")
    out = dedup_and_denoise([f])
    assert out[0].status == "needs_human_review"


@pytest.mark.asyncio
async def test_persist_and_query_no_secret_leak(tmp_path) -> None:
    from storage.dao import ReviewStore

    result = run_review(diff_text=(_FIXTURES / "0001_insecure.diff").read_text())
    db_file = tmp_path / "cr.db"
    store = ReviewStore(f"sqlite+aiosqlite:///{db_file}")
    await store.init()
    try:
        await store.persist(result)
        got = await store.get_by_task_id(result.task_id)
        assert got is not None
        assert got["task"].finding_count >= 3
        assert len(got["findings"]) >= 3
    finally:
        await store.close()

    raw = db_file.read_bytes()
    for secret in _SECRETS:
        assert secret.encode() not in raw


@pytest.mark.asyncio
async def test_agent_path_calls_tool_and_summarizes() -> None:
    """The fake-model agent loop drives the review_code tool and summarizes — no API key."""
    import uuid

    from trpc_agent_sdk.runners import Runner
    from trpc_agent_sdk.sessions import InMemorySessionService
    from trpc_agent_sdk.types import Content, Part

    from agent.agent import create_agent

    runner = Runner(app_name="cr_test", agent=create_agent(), session_service=InMemorySessionService())
    sid = str(uuid.uuid4())
    await runner.session_service.create_session(app_name="cr_test", user_id="u", session_id=sid)

    diff = (_FIXTURES / "0001_insecure.diff").read_text()
    saw_tool_call = False
    final_text = ""
    async for event in runner.run_async(user_id="u",
                                        session_id=sid,
                                        new_message=Content(role="user", parts=[Part(text=diff)])):
        for part in (event.content.parts if event.content else []) or []:
            if part.function_call:
                saw_tool_call = True
            if part.text:
                final_text += part.text

    assert saw_tool_call
    assert "Review complete" in final_text
    for secret in _SECRETS:
        assert secret not in final_text


def test_local_sandbox_records_run_and_finds_issues() -> None:
    result = run_review(diff_text=(_FIXTURES / "0001_insecure.diff").read_text(), runtime="local")
    assert result.report.findings_summary["total"] >= 3
    assert len(result.report.sandbox_summary) == 1
    run = result.report.sandbox_summary[0]
    assert run.script == "run_checks.py"
    assert run.exit_code in (0, 1)  # 1 = scanners found issues
    assert not run.timed_out
    assert result.monitoring["sandbox_sec"] > 0


def test_sandbox_timeout_does_not_crash_the_task() -> None:
    # An impossibly small timeout must mark the run timed-out but still complete the review.
    result = run_review(diff_text=(_FIXTURES / "0001_insecure.diff").read_text(),
                        runtime="local",
                        sandbox_timeout=0.001)
    assert result.task_id is not None
    run = result.report.sandbox_summary[0]
    assert run.timed_out is True
    assert result.monitoring["exception_dist"].get("sandbox_failure") == 1


def test_sandbox_output_byte_accounting() -> None:
    from pipeline.sandbox import _truncate

    text, n = _truncate("x" * 5000, 10)
    assert n == 5000  # records the true size
    assert len(text.encode()) <= 10 + len("\n...[truncated]")
