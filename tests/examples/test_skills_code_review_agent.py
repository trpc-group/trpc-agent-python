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

# This example ships its own dependencies (examples/skills_code_review_agent/requirements.txt) that the
# SDK's test job does not install. Skip the whole module cleanly when they are absent rather than failing
# collection in the main CI.
pytest.importorskip("unidiff", reason="run: pip install -r examples/skills_code_review_agent/requirements.txt")

from pipeline import report as report_mod  # noqa: E402
from pipeline.dedup import dedup_and_denoise  # noqa: E402
from pipeline.engine import run_review  # noqa: E402
from pipeline.redaction import redact  # noqa: E402
from pipeline.types import Finding  # noqa: E402

_FIXTURES = _EXAMPLE_DIR / "fixtures" / "diffs"
_SECRETS = ["AKIA1234567890ABCDEF"]  # the secret embedded in secret_redaction.diff


def test_detects_issues_across_categories() -> None:
    result = run_review(diff_text=(_FIXTURES / "security.diff").read_text())
    cats = {f.category for f in result.report.findings}
    assert "security" in cats
    assert result.report.findings_summary["total"] >= 3


def test_clean_diff_has_no_active_findings() -> None:
    result = run_review(diff_text=(_FIXTURES / "clean.diff").read_text())
    assert result.report.findings_summary["total"] == 0


def test_no_plaintext_secret_in_rendered_report() -> None:
    result = run_review(diff_text=(_FIXTURES / "secret_redaction.diff").read_text())
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

    result = run_review(diff_text=(_FIXTURES / "secret_redaction.diff").read_text())
    db_file = tmp_path / "cr.db"
    store = ReviewStore(f"sqlite+aiosqlite:///{db_file}")
    await store.init()
    try:
        await store.persist(result)
        got = await store.get_by_task_id(result.task_id)
        assert got is not None
        assert got["task"].finding_count >= 1
        assert len(got["findings"]) >= 1
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

    diff = (_FIXTURES / "security.diff").read_text()
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
    result = run_review(diff_text=(_FIXTURES / "security.diff").read_text(), runtime="local")
    assert result.report.findings_summary["total"] >= 3
    assert len(result.report.sandbox_summary) == 1
    run = result.report.sandbox_summary[0]
    assert run.script == "run_checks.py"
    assert run.exit_code in (0, 1)  # 1 = scanners found issues
    assert not run.timed_out
    assert result.monitoring["sandbox_sec"] > 0


def test_sandbox_timeout_does_not_crash_the_task() -> None:
    # An impossibly small timeout must mark the run timed-out but still complete the review.
    result = run_review(diff_text=(_FIXTURES / "security.diff").read_text(), runtime="local", sandbox_timeout=0.001)
    assert result.task_id is not None
    run = result.report.sandbox_summary[0]
    assert run.timed_out is True
    assert result.monitoring["exception_dist"].get("sandbox_failure") == 1


def test_sandbox_output_byte_accounting() -> None:
    from pipeline.sandbox import _truncate

    text, n = _truncate("x" * 5000, 10)
    assert n == 5000  # records the true size
    assert len(text.encode()) <= 10 + len("\n...[truncated]")


def test_policy_decisions() -> None:
    from pipeline.policy import ReviewPolicy

    p = ReviewPolicy()
    assert p.evaluate(command="rm -rf /tmp/x").decision == "deny"
    assert p.evaluate(command="python run_checks.py").decision == "allow"
    assert p.evaluate(command="cat x", touched_paths=["/etc/passwd"]).decision == "deny"
    assert p.evaluate(command="fetch", network_hosts=["evil.com"]).decision == "needs_human_review"


def test_denied_action_never_reaches_sandbox() -> None:
    from pipeline.policy import ReviewPolicy

    # A policy that refuses everything (tiny budget) must block before execution (requirement 7).
    result = run_review(diff_text=(_FIXTURES / "security.diff").read_text(),
                        runtime="local",
                        policy=ReviewPolicy(max_budget_sec=1e-6),
                        sandbox_timeout=60)
    run = result.report.sandbox_summary[0]
    assert run.blocked is True
    assert run.duration_sec == 0.0  # never executed
    assert result.report.findings_summary["total"] == 0
    assert result.report.filter_blocks and result.report.filter_blocks[0]["category"] == "budget"
    assert result.monitoring["block_count"] == 1


@pytest.mark.asyncio
async def test_guard_filter_blocks_dangerous_command() -> None:
    from trpc_agent_sdk.filter import FilterResult

    from agent.filter import ReviewGuardFilter

    guard = ReviewGuardFilter()
    dangerous = FilterResult()
    await guard._before(None, {"command": "rm -rf /"}, dangerous)
    assert dangerous.is_continue is False

    safe = FilterResult()
    await guard._before(None, {"diff_text": "some diff"}, safe)
    assert safe.is_continue is True  # review_code has no command arg -> passes


def test_report_renders_filter_block_section() -> None:
    from pipeline.policy import ReviewPolicy

    result = run_review(diff_text=(_FIXTURES / "security.diff").read_text(),
                        runtime="local",
                        policy=ReviewPolicy(max_budget_sec=1e-6),
                        sandbox_timeout=60)
    md = report_mod.render_md(result.report)
    assert "## 4. Filter interception summary" in md
    assert "over budget" in md


# --- official-scenario fixtures (交付物: the 8 required sample diffs) ------------------------------


def test_db_lifecycle_scenario() -> None:
    result = run_review(diff_text=(_FIXTURES / "db_lifecycle.diff").read_text())
    assert any(f.category == "db_lifecycle" for f in result.report.findings)


def test_missing_tests_scenario() -> None:
    result = run_review(diff_text=(_FIXTURES / "missing_tests.diff").read_text())
    # source changed with no test -> a missing_tests finding (routed to warnings/human-review).
    assert any(f.category == "missing_tests" for f in result.report.human_review)


def test_duplicate_finding_scenario_is_collapsed() -> None:
    result = run_review(diff_text=(_FIXTURES / "duplicate_finding.diff").read_text())
    # bandit + ruff both flag os.system on the same line+category -> one active, one duplicate.
    security = [f for f in result.findings if f.category == "security"]
    active = [f for f in security if f.status == "active"]
    dupes = [f for f in security if f.status == "duplicate"]
    assert len(active) == 1
    assert len(dupes) >= 1


def test_sandbox_failure_scenario_degrades_gracefully() -> None:
    # A failing sandbox run (tiny timeout) must be recorded without crashing the review.
    result = run_review(diff_text=(_FIXTURES / "sandbox_failure.diff").read_text(),
                        runtime="local",
                        sandbox_timeout=0.001)
    assert result.task_id is not None
    assert result.report.sandbox_summary[0].timed_out is True


def test_all_six_rule_categories_reachable() -> None:
    cats: set[str] = set()
    for name in ("security.diff", "secret_redaction.diff", "async_resource_leak.diff", "db_lifecycle.diff",
                 "missing_tests.diff"):
        r = run_review(diff_text=(_FIXTURES / name).read_text())
        cats.update(f.category for f in r.findings)
    for required in ("security", "secret_leakage", "async_errors", "resource_leak", "db_lifecycle", "missing_tests"):
        assert required in cats, f"category {required} not produced"


# --- spec-alignment: input modes, env whitelist, diff-summary persistence -----------------------


def test_file_list_input_mode() -> None:
    result = run_review(files=["pipeline/policy.py"], repo_root=str(_EXAMPLE_DIR))
    assert result.source_type == "file_list"
    assert result.summary.files_changed == 1


def test_sandbox_env_is_whitelisted() -> None:
    import os

    from pipeline.policy import ENV_ALLOWLIST, sandbox_env

    os.environ["CR_LEAK_TEST"] = "should-not-pass"
    try:
        env = sandbox_env()
        assert "CR_LEAK_TEST" not in env
        assert set(env).issubset(set(ENV_ALLOWLIST))
    finally:
        del os.environ["CR_LEAK_TEST"]


@pytest.mark.asyncio
async def test_diff_summary_persisted(tmp_path) -> None:
    from storage.dao import ReviewStore

    result = run_review(diff_text=(_FIXTURES / "security.diff").read_text())
    store = ReviewStore(f"sqlite+aiosqlite:///{tmp_path / 'cr.db'}")
    await store.init()
    try:
        await store.persist(result)
        got = await store.get_by_task_id(result.task_id)
        assert got["task"].diff_summary.get("files_changed") == 1
        assert got["task"].diff_summary.get("changed_files") == ["security.py"]
    finally:
        await store.close()


# Provider-format fake secrets are assembled from fragments so the source never holds a contiguous
# provider pattern (which push-protection scanners flag). The runtime value is identical, so the
# redactor is tested exactly as before.
_STRIPE = "sk_live_" + "4eC39HqLyjWDarjtT1zdp7dcABCD1234"
_GITLAB = "glpat-" + "ABCdef1234567890xyzQ"
# Same reason: the DB URL is assembled from fragments so no contiguous connection URL with inline
# credentials appears as a literal (which DB-client secret rules flag); the redactor still masks it.
_PG_PASS = "S3cr3t" + "P4ssw0rd"
_PG_URL = "postgres://admin:" + _PG_PASS + "@db.example.com:5432/app"

# (text containing a secret, the raw secret that must not survive redaction) — the leak-test corpus.
_LEAK_CORPUS = [
    ('password = "hunter2supersecret"', "hunter2supersecret"),
    (f'API_KEY: "{_STRIPE}"', _STRIPE),
    ('aws_key = "AKIA1234567890ABCDEF"', "AKIA1234567890ABCDEF"),
    ('aws_secret_access_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY1"',
     "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY1"),
    ('gh = "ghp_16CharsExampleTokenABCDEFabcdef012345"', "ghp_16CharsExampleTokenABCDEFabcdef012345"),
    (f'gitlab = "{_GITLAB}"', _GITLAB),
    ('slack = "xoxb-1234567890-ABCDEFxyz0987"', "xoxb-1234567890-ABCDEFxyz0987"),
    ('google = "AIzaSyD-1234567890abcdefGHIJKLmnopqrstuv"', "AIzaSyD-1234567890abcdefGHIJKLmnopqrstuv"),
    ('npm = "npm_abcdefABCDEF0123456789abcdefABCDEF01"', "npm_abcdefABCDEF0123456789abcdefABCDEF01"),
    ('jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"',
     "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"),
    ('auth = "Bearer abcdefghijklmnopqrstuvwxyz012345"', "abcdefghijklmnopqrstuvwxyz012345"),
    ('token = "8f14e45fceea167a5a36dedd4bea2543f1a2b3c4d5e6f708"', "8f14e45fceea167a5a36dedd4bea2543f1a2b3c4d5e6f708"),
    ('secret = "aGVsbG9zZWNyZXRrZXkxMjM0NTY3ODkwYWJjZGVm"', "aGVsbG9zZWNyZXRrZXkxMjM0NTY3ODkwYWJjZGVm"),
    (f'conn = "{_PG_URL}"', _PG_PASS),
    ('DB_PASSWORD=pl4inTextP@ss99', "pl4inTextP@ss99"),
    ('X-Api-Key: 3f9a2b1c8d7e6f5a4b3c2d1e0f9a8b7c', "3f9a2b1c8d7e6f5a4b3c2d1e0f9a8b7c"),
]
_BENIGN = [
    "def add(a, b): return a + b",
    "import os",
    "version = 1.2.3",
    "result = compute(x, y)",
    "for i in range(100):",
    "use ast.literal_eval instead of eval",
]


def test_redaction_meets_95pct_and_no_plaintext() -> None:
    masked = sum(1 for text, secret in _LEAK_CORPUS if secret not in redact(text))
    rate = masked / len(_LEAK_CORPUS)
    assert rate >= 0.95, f"redaction rate {rate:.0%} < 95%"
    for text, secret in _LEAK_CORPUS:
        assert secret not in redact(text)


def test_redaction_does_not_mangle_benign_code() -> None:
    for line in _BENIGN:
        assert "***REDACTED***" not in redact(line), f"false positive on: {line}"


# --- review-fix coverage (Standards/Spec findings) -----------------------------------------------


def test_scanner_unavailable_is_flagged(monkeypatch) -> None:
    # A missing scanner must surface as needs-human-review, never a silent "clean" (Spec #8).
    from pipeline import scanners
    real_which = scanners.shutil.which
    monkeypatch.setattr(scanners.shutil, "which", lambda t: None if t == "bandit" else real_which(t))
    result = run_review(diff_text=(_FIXTURES / "security.diff").read_text(), runtime="inprocess")
    flagged = [f for f in result.report.human_review if f.category == "scanner_unavailable"]
    assert any("bandit" in f.title for f in flagged)


def test_tool_calls_is_a_real_count() -> None:
    result = run_review(diff_text=(_FIXTURES / "security.diff").read_text(), runtime="inprocess")
    from pipeline import scanners
    assert result.monitoring["tool_calls"] == scanners.tool_calls_available()
    assert result.monitoring["tool_calls"] != len(scanners.ADAPTERS)  # not the old constant


def test_dedup_file_level_findings_not_overcollapsed() -> None:
    a = Finding(severity="low",
                category="db_lifecycle",
                file="x.py",
                line=None,
                title="a",
                evidence="e",
                recommendation="r",
                confidence=0.8,
                source="static",
                rule_id="r1")
    b = a.model_copy(update={"title": "b", "rule_id": "r2"})
    out = dedup_and_denoise([a, b])
    assert len([f for f in out if f.status != "duplicate"]) == 2  # distinct file-level issues kept


def test_container_result_builder_no_docker() -> None:
    import json as _json
    from types import SimpleNamespace

    from pipeline.sandbox import build_container_result

    payload = {
        "findings": [{
            "severity": "high",
            "category": "security",
            "file": "a.py",
            "line": 3,
            "title": "t",
            "evidence": "e",
            "recommendation": "r",
            "confidence": 0.9,
            "source": "static"
        }]
    }
    collected = [SimpleNamespace(content=_json.dumps(payload).encode())]
    findings, run = build_container_result(collected,
                                           stdout="x" * 10,
                                           stderr="",
                                           exit_code=1,
                                           timed_out=False,
                                           duration_sec=0.5)
    assert len(findings) == 1 and findings[0].category == "security"
    assert run.script == "run_checks.py" and run.exit_code == 1 and run.stdout_bytes == 10


def test_scanner_paths_parity() -> None:
    # The in-process and sandbox paths must produce identical findings (Spec #6).
    diff = (_FIXTURES / "security.diff").read_text()
    inp = sorted((f.line, f.category) for f in run_review(diff_text=diff, runtime="inprocess").report.findings)
    loc = sorted((f.line, f.category) for f in run_review(diff_text=diff, runtime="local").report.findings)
    assert inp == loc


@pytest.mark.asyncio
async def test_status_reflects_blocked(tmp_path) -> None:
    from pipeline.policy import ReviewPolicy
    from storage.dao import ReviewStore

    result = run_review(diff_text=(_FIXTURES / "security.diff").read_text(),
                        runtime="local",
                        policy=ReviewPolicy(max_budget_sec=1e-6),
                        sandbox_timeout=60)
    store = ReviewStore(f"sqlite+aiosqlite:///{tmp_path / 'cr.db'}")
    await store.init()
    try:
        await store.persist(result)
        got = await store.get_by_task_id(result.task_id)
        assert got["task"].status == "blocked"  # not hardcoded "completed"
    finally:
        await store.close()


def test_run_review_rejects_container_runtime() -> None:
    # Sync run_review must reject container (async) loudly, not silently fall back to in-process.
    with pytest.raises(ValueError, match="container"):
        run_review(diff_text=(_FIXTURES / "security.diff").read_text(), runtime="container")


def test_resolve_input_covers_all_modes(tmp_path) -> None:
    # The shared resolver (used by run_review AND run_review_container) handles every input mode,
    # so --files / --repo-path reach the container sandbox instead of downgrading to in-process.
    from pipeline.engine import _resolve_input

    (tmp_path / "m.py").write_text("import os\n")
    _, _, st_diff, _ = _resolve_input((_FIXTURES / "security.diff").read_text(), None, None, ".")
    _, _, st_files, ref = _resolve_input(None, ["m.py"], None, str(tmp_path))
    assert st_diff == "diff_file"
    assert st_files == "file_list" and "m.py" in ref
