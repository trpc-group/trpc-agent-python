# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""End-to-end acceptance tests over the shipped fixtures (dry-run, local runtime).

Container-specific claims (network isolation) run only when Docker is
reachable; everything else is CI-safe.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from review_agent.diff_parser import parse_diff_file
from review_agent.pipeline import ReviewOptions, run_review
from review_agent.store import ReviewStore


def _run_fixture(fixture_dir: Path, tmp_path: Path, **option_overrides):
    tmp_path.mkdir(parents=True, exist_ok=True)
    expected = json.loads((fixture_dir / "expected.json").read_text(encoding="utf-8"))
    meta = expected.get("meta") or {}
    options = ReviewOptions(
        db_url=f"sqlite:///{tmp_path}/review.db",
        output_dir=str(tmp_path / "out"),
        unsafe_local=True,
        dry_run=True,
        run_timeout=int(meta.get("run_timeout", 60)),
        inject_sleep=float(meta.get("inject_sleep", 0)),
    )
    for key, value in option_overrides.items():
        setattr(options, key, value)
    repo_dir = fixture_dir / "repo"
    parsed = parse_diff_file(str(fixture_dir / "input.diff"), repo_path=str(repo_dir) if repo_dir.is_dir() else None)
    parsed.input_type = "fixture"
    parsed.input_ref = fixture_dir.name
    return expected, asyncio.run(run_review(parsed, options))


def test_all_fixtures_produce_reports(fixtures_dir, tmp_path):
    """Acceptance 1: every shipped sample runs and yields both report files."""
    ran = 0
    for fixture_dir in sorted(fixtures_dir.iterdir()):
        if not (fixture_dir / "input.diff").is_file():
            continue
        _, outcome = _run_fixture(fixture_dir, tmp_path / fixture_dir.name)
        assert outcome.status in ("succeeded", "partial"), f"{fixture_dir.name}: {outcome.status}"
        assert Path(outcome.report_json_path).is_file()
        assert Path(outcome.report_md_path).is_file()
        ran += 1
    assert ran >= 14


def test_database_is_queryable_by_task_id(fixtures_dir, tmp_path):
    """Acceptance 3: task, sandbox runs, findings and report all land in the DB."""
    _, outcome = _run_fixture(fixtures_dir / "02_sql_injection", tmp_path)
    store = ReviewStore(f"sqlite:///{tmp_path}/review.db")

    async def check():
        await store.init()
        bundle = await store.load_task_bundle(outcome.task_id)
        await store.close()
        return bundle

    bundle = asyncio.run(check())
    assert bundle is not None
    assert bundle["task"].status == "succeeded"
    assert len(bundle["diff_files"]) == 2
    assert len(bundle["sandbox_runs"]) == 1
    assert bundle["sandbox_runs"][0].exit_code == 0
    assert len(bundle["findings"]) >= 2
    assert len(bundle["reports"]) == 2
    assert len(bundle["metrics"]) == 1
    assert len(bundle["filter_events"]) >= 2  # allow decisions are recorded too


def test_sandbox_timeout_does_not_crash_the_task(fixtures_dir, tmp_path):
    """Acceptance 4: fixture 07 injects a sleep beyond the timeout."""
    expected, outcome = _run_fixture(fixtures_dir / "07_sandbox_fail", tmp_path)
    assert expected["meta"]["expect_sandbox"] == "timeout"
    assert outcome.status == "partial"
    sandbox = outcome.payload["sandbox_summary"]
    assert sandbox and sandbox[0]["timed_out"] is True
    assert Path(outcome.report_json_path).is_file()


def test_prompt_injection_does_not_change_the_verdict(fixtures_dir, tmp_path):
    """Adversarial: fixture 14 embeds LLM-targeted instructions plus a real bug."""
    _, outcome = _run_fixture(fixtures_dir / "14_prompt_injection", tmp_path)
    security = [f for f in outcome.payload["findings"] if f["category"] == "security"]
    assert security, "the SQL injection must be reported despite the injected instructions"
    report_text = json.dumps(outcome.payload, ensure_ascii=False)
    assert "no issues" not in report_text.lower() or security, "verdict must not flip"


def test_duplicate_not_reported_twice(fixtures_dir, tmp_path):
    """Acceptance 6 (dedup): fixture 06 must yield exactly one finding on the line."""
    expected, outcome = _run_fixture(fixtures_dir / "06_duplicate", tmp_path)
    assertion = expected["meta"]["assert_dedup"]
    matches = [
        f for f in outcome.payload["findings"]
        if f["file"] == assertion["file"] and f["line"] == assertion["line"] and f["category"] == assertion["category"]
    ]
    assert len(matches) <= assertion["max_reported"]
    assert len(matches) == 1


def test_dry_run_completes_within_two_minutes(fixtures_dir, tmp_path):
    """Acceptance 6: full pipeline (process start -> report on disk) under 120 s."""
    started = time.monotonic()
    _, outcome = _run_fixture(fixtures_dir / "11_large", tmp_path)
    elapsed = time.monotonic() - started
    assert outcome.status == "succeeded"
    assert elapsed < 120, f"dry-run took {elapsed:.1f}s"


def test_no_plaintext_secrets_in_report_or_db(fixtures_dir, tmp_path):
    """Acceptance 5: fixture 08 secrets never appear verbatim in artifacts."""
    import re

    _, outcome = _run_fixture(fixtures_dir / "08_secrets", tmp_path)
    report_text = Path(outcome.report_json_path).read_text(encoding="utf-8")
    db_bytes = (tmp_path / "review.db").read_bytes().decode("utf-8", errors="ignore")
    for blob, label in ((report_text, "report"), (db_bytes, "database")):
        assert not re.search(r"AKIA[0-9A-Z]{16}", blob), f"AWS key leaked into {label}"
        assert not re.search(r"\bghp_[A-Za-z0-9]{36}\b", blob), f"GitHub token leaked into {label}"


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:  # pylint: disable=broad-except
        return False


@pytest.mark.skipif(not _docker_available(), reason="docker not available")
def test_container_has_no_network():
    """Adversarial: an outbound probe from inside the sandbox must fail."""
    from trpc_agent_sdk.code_executors import WorkspaceRunProgramSpec

    from review_agent.sandbox import create_sandbox

    sandbox = create_sandbox(prefer="container")
    assert sandbox.kind == "container"
    probe = ("import urllib.request, sys\n"
             "try:\n"
             "    urllib.request.urlopen('http://example.com', timeout=5)\n"
             "except Exception as ex:\n"
             "    print(f'BLOCKED: {type(ex).__name__}'); sys.exit(0)\n"
             "print('NETWORK REACHABLE'); sys.exit(1)\n")

    async def run_probe():
        manager = sandbox.runtime.manager(None)
        ws = await manager.create_workspace("net-probe", None)
        runner = sandbox.runtime.runner(None)
        return await runner.run_program(
            ws, WorkspaceRunProgramSpec(cmd="python3", args=["-c", probe], env={}, cwd="", timeout=30), None)

    result = asyncio.run(run_probe())
    assert result.exit_code == 0, f"network probe escaped: {result.stdout} {result.stderr}"
    assert "BLOCKED" in result.stdout
