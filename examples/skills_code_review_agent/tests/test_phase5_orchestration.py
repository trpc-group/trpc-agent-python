# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Phase 5 (Orchestration & report) acceptance tests.

Covers the Phase-5 DoD:
  1. CLI supports --diff-file / --repo-path / --fixture.
  2. Full pipeline runs: parse → filter → sandbox → dedupe → persist → report.
  3. dry-run needs no API key (FakeRunner).
  4. Produces review_report.json + review_report.md.
  5. Report has all eight sections.
  6. DB stores a complete record queryable by task_id.

Run:
    C:/Users/douzhenyu/.workbuddy/binaries/python/envs/cr_agent/Scripts/python.exe \\
        examples/skills_code_review_agent/tests/test_phase5_orchestration.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

_EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_EXAMPLE_ROOT))

import agent  # noqa: E402
from agent.db import SQLiteStore  # noqa: E402

_SKILL_DIR = _EXAMPLE_ROOT / "skills" / "code-review"

_REPORT_KEYS = {
    "task_id", "1_findings", "2_severity_stats", "3_needs_human_review",
    "4_filter_blocks", "5_monitor", "6_sandbox_runs", "7_recommendations",
    "8_warnings",
}
_MD_SECTIONS = [
    "## 1. Findings", "## 2. 严重级别统计", "## 3. 人工复核项",
    "## 4. Filter 拦截摘要", "## 5. 监控指标", "## 6. 沙箱执行摘要",
    "## 7. 可执行修复建议", "## 8. Warnings",
]


def _args(**kw) -> SimpleNamespace:
    base = dict(
        skill_dir=str(_SKILL_DIR), diff_file=None, repo_path=None, fixture=None,
        db_path="", mode="dry-run", output_dir="", print_changeset=False,
        telemetry=False, dry_run=False,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _run_pipeline(**kw) -> tuple[str, str, str]:
    """Run agent._async_main with temp db + output dir. Returns (db, outdir, task_id-from-report)."""
    db = tempfile.mktemp(suffix=".db", prefix="cr_p5_")
    out = tempfile.mkdtemp(prefix="cr_p5_out_")
    ns = _args(db_path=db, output_dir=out, **kw)
    import asyncio
    asyncio.run(agent._async_main(ns))
    return db, out, None


class TestFullPipeline(unittest.IsolatedAsyncioTestCase):
    """DoD #1-3 — full pipeline, three inputs, dry-run."""

    async def test_dry_run_fixture_security_full_chain(self):
        db, out, _ = await _async_run("security")
        self.assertTrue(Path(out, "review_report.json").exists())
        self.assertTrue(Path(out, "review_report.md").exists())
        # DB: task done, findings present
        store = SQLiteStore(db)
        try:
            rec = await store.get_task(_first_task_id(db))
            self.assertEqual(rec["task"]["status"], "done")
            self.assertGreater(len(rec["findings"]), 0)
            self.assertIsNotNone(rec["report"])
            self.assertIsNotNone(rec["monitor_summary"])
        finally:
            await store.close()
        _cleanup(db, out)

    async def test_diff_file_input(self):
        fd, diff_path = tempfile.mkstemp(suffix=".diff")
        os.close(fd)
        Path(diff_path).write_text(
            "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
            "@@ -1,2 +1,2 @@\n-old\n+new\n ctx\n", encoding="utf-8")
        try:
            db, out, _ = await _async_run(diff_file=diff_path)
            self.assertTrue(Path(out, "review_report.json").exists())
            _cleanup(db, out)
        finally:
            os.unlink(diff_path)

    async def test_dry_run_flag_equiv_mode(self):
        # --dry-run flag should behave like --mode dry-run (overrides --mode real).
        db, out, _ = await _async_run(fixture="security", dry_run=True, mode="real")
        # dry_run overrides mode → FakeRunner, no API call.
        self.assertTrue(Path(out, "review_report.json").exists())
        _cleanup(db, out)

    async def test_clean_fixture_no_findings(self):
        db, out, _ = await _async_run("clean")
        report = json.loads(Path(out, "review_report.json").read_text(encoding="utf-8"))
        self.assertEqual(len(report["1_findings"]), 0)
        _cleanup(db, out)


class TestReport(unittest.IsolatedAsyncioTestCase):
    """DoD #4-5 — report files + eight sections."""

    async def asyncSetUp(self):
        self.db, self.out, _ = await _async_run("security")

    async def asyncTearDown(self):
        _cleanup(self.db, self.out)

    def test_json_has_eight_sections(self):
        report = json.loads(Path(self.out, "review_report.json").read_text(encoding="utf-8"))
        self.assertTrue(_REPORT_KEYS.issubset(report.keys()))

    def test_md_has_eight_section_headers(self):
        md = Path(self.out, "review_report.md").read_text(encoding="utf-8")
        for section in _MD_SECTIONS:
            self.assertIn(section, md)

    def test_json_and_md_consistent_findings_count(self):
        report = json.loads(Path(self.out, "review_report.json").read_text(encoding="utf-8"))
        md = Path(self.out, "review_report.md").read_text(encoding="utf-8")
        # md mentions the finding count in section 1 header.
        self.assertIn(str(len(report["1_findings"])), md)


class TestDbComplete(unittest.IsolatedAsyncioTestCase):
    """DoD #8 — DB queryable by task_id with a complete record."""

    async def test_get_task_returns_full_record(self):
        db, out, _ = await _async_run("security")
        store = SQLiteStore(db)
        try:
            tid = _first_task_id(db)
            rec = await store.get_task(tid)
            # All seven sections present.
            for key in ("task", "input_diffs", "sandbox_runs", "findings",
                        "filter_blocks", "monitor_summary", "report"):
                self.assertIn(key, rec, f"missing {key} in get_task result")
            self.assertEqual(rec["task"]["id"], tid)
            self.assertEqual(rec["task"]["status"], "done")
            self.assertGreater(len(rec["input_diffs"]), 0)  # security fixture has 2 files
            self.assertGreater(len(rec["findings"]), 0)
        finally:
            await store.close()
        _cleanup(db, out)


class TestSandboxFailureDegrades(unittest.IsolatedAsyncioTestCase):
    """DoD #7 — sandbox failure degrades to FakeRunner, task still completes."""

    async def test_real_mode_degrades_on_sandbox_error(self):
        # Force the sandbox path to fail by pointing skill-dir scripts at a
        # non-existent path — the pipeline should fall back to FakeRunner.
        db = tempfile.mktemp(suffix=".db")
        out = tempfile.mkdtemp(prefix="cr_p5_deg_")
        # Monkey-patch LocalRuntime.run to raise, simulating sandbox failure.
        from agent.sandbox import LocalRuntime
        original = LocalRuntime.run

        async def _boom(self, script_path, input, policy):
            raise RuntimeError("sandbox unavailable")

        LocalRuntime.run = _boom
        try:
            ns = _args(fixture="security", db_path=db, output_dir=out, mode="real")
            await agent._async_main(ns)
            # Task still completes (degraded to FakeRunner).
            store = SQLiteStore(db)
            try:
                rec = await store.get_task(_first_task_id(db))
                self.assertEqual(rec["task"]["status"], "done")
                self.assertGreater(len(rec["findings"]), 0)  # FakeRunner still produced findings
            finally:
                await store.close()
        finally:
            LocalRuntime.run = original
            _cleanup(db, out)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
async def _async_run(fixture=None, **kw) -> tuple[str, str, None]:
    """Run the pipeline with a temp db + output dir, return (db, out, None)."""
    import asyncio
    db = tempfile.mktemp(suffix=".db", prefix="cr_p5_")
    out = tempfile.mkdtemp(prefix="cr_p5_out_")
    ns = _args(db_path=db, output_dir=out, fixture=fixture, **kw)
    await agent._async_main(ns)
    return db, out, None


def _first_task_id(db_path: str) -> str:
    import sqlite3
    conn = sqlite3.connect(db_path)
    tid = conn.execute("SELECT id FROM review_task ORDER BY created_at LIMIT 1").fetchone()[0]
    conn.close()
    return tid


def _cleanup(db: str, out: str) -> None:
    if os.path.exists(db):
        try:
            os.unlink(db)
        except PermissionError:
            pass
    shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
