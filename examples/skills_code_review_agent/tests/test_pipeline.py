# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""End-to-end pipeline tests over the 8 fixture scenarios (local runtime, dry-run)."""
import json
import shutil
import time
from pathlib import Path

from review.pipeline import CHECKERS, ReviewOptions, run_review
from storage.store import ReviewStore

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = EXAMPLE_ROOT / "fixtures"


def _opts(tmp_path, fixture, **kw):
    return ReviewOptions(
        diff_text=(FIXTURES / fixture).read_text(),
        input_type="fixture", input_ref=fixture,
        runtime="local", dry_run=True,
        db_url=f"sqlite:///{tmp_path}/cr.db",
        output_dir=str(tmp_path / "out"), **kw)


async def _bundle(tmp_path, task_id):
    store = ReviewStore(db_url=f"sqlite:///{tmp_path}/cr.db")
    bundle = await store.get_task_bundle(task_id)
    await store.close()
    return bundle


async def test_clean_diff_passes(tmp_path):
    result = await run_review(_opts(tmp_path, "clean.diff"))
    assert result.report["conclusion"] == "pass"
    assert result.report["findings"] == []
    assert Path(result.json_path).exists() and Path(result.md_path).exists()
    bundle = await _bundle(tmp_path, result.task_id)
    assert bundle["task"]["status"] == "completed"
    assert len(bundle["sandbox_runs"]) == len(CHECKERS) + 1  # + parse_diff


async def test_security_diff_blocked(tmp_path):
    result = await run_review(_opts(tmp_path, "security_eval.diff"))
    assert result.report["conclusion"] == "blocked"
    cats = {f["category"] for f in result.report["findings"]}
    assert "security" in cats


async def test_async_leak_detected(tmp_path):
    result = await run_review(_opts(tmp_path, "async_leak.diff"))
    assert any(f["category"] == "async_resource_leak" for f in result.report["findings"])


async def test_db_lifecycle_detected(tmp_path):
    result = await run_review(_opts(tmp_path, "db_lifecycle.diff"))
    assert any(f["category"] == "db_lifecycle" for f in result.report["findings"])
    assert any(f["confidence"] < 0.6 for f in result.report["needs_human_review"])


async def test_missing_test_detected(tmp_path):
    result = await run_review(_opts(tmp_path, "missing_test.diff"))
    assert any(f["category"] == "missing_test" for f in result.report["findings"])


async def test_duplicate_finding_deduplicated(tmp_path):
    result = await run_review(_opts(tmp_path, "duplicate_finding.diff"))
    security = [f for f in result.report["findings"] if f["category"] == "security"]
    assert len(security) == 1
    assert result.report["summary"]["deduplicated"] >= 1
    bundle = await _bundle(tmp_path, result.task_id)
    assert any(f["status"] == "deduped" for f in bundle["findings"])


async def test_sandbox_failure_does_not_crash(tmp_path):
    skill_copy = tmp_path / "code-review"
    shutil.copytree(EXAMPLE_ROOT / "skills" / "code-review", skill_copy)
    (skill_copy / "scripts" / "check_broken.py").write_text("import sys\nsys.exit(3)\n")
    checkers = list(CHECKERS) + [("check_broken.py", "broken")]
    allowed = tuple(s for s, _ in checkers) + ("parse_diff.py",)
    result = await run_review(_opts(tmp_path, "sandbox_failure.diff",
                                    skill_root=str(skill_copy), checkers=checkers,
                                    allowed_scripts=allowed))
    bundle = await _bundle(tmp_path, result.task_id)
    failed = [r for r in bundle["sandbox_runs"] if r["status"] == "failed"]
    assert failed and failed[0]["script"] == "check_broken.py"
    assert bundle["task"]["status"] == "completed"


async def test_secret_leak_redacted_everywhere(tmp_path):
    result = await run_review(_opts(tmp_path, "secret_leak.diff"))
    assert any(f["category"] == "secret_leak" for f in result.report["findings"])
    secret = "sk-fakefakefakefakefakefake123456"
    all_text = Path(result.json_path).read_text() + Path(result.md_path).read_text()
    assert secret not in all_text
    bundle = await _bundle(tmp_path, result.task_id)
    assert secret not in json.dumps(bundle, default=str)


async def test_denied_checker_never_reaches_sandbox(tmp_path):
    checkers = list(CHECKERS) + [("evil.py", "evil")]
    result = await run_review(_opts(tmp_path, "clean.diff", checkers=checkers))
    bundle = await _bundle(tmp_path, result.task_id)
    assert all(r["script"] != "evil.py" for r in bundle["sandbox_runs"])
    denies = [e for e in bundle["filter_events"] if e["decision"] == "deny"]
    assert any("evil.py" in e["target"] for e in denies)
    assert result.report["conclusion"] in ("needs_attention", "pass")


async def test_metrics_recorded_and_fast(tmp_path):
    start = time.monotonic()
    result = await run_review(_opts(tmp_path, "security_eval.diff"))
    elapsed = time.monotonic() - start
    assert elapsed < 120
    m = result.report["metrics"]
    assert m["tool_calls"] >= len(CHECKERS) + 1
    assert m["total_duration_ms"] > 0
    bundle = await _bundle(tmp_path, result.task_id)
    assert bundle["metrics"]["findings_total"] == len(result.report["findings"])
