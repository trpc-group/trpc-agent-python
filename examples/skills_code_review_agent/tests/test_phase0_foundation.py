# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Phase 0 (Foundation) acceptance tests — SDK-backed async edition.

Replaces the original hand-rolled sqlite3 tests. The store now runs on the
SDK's :class:`SqlStorage` + SQLAlchemy ORM (``db.models.CRBase``), so all
operations are async. Run with the CR Agent venv that has SDK deps:

    C:/Users/douzhenyu/.workbuddy/binaries/python/envs/cr_agent/Scripts/python.exe \\
        examples/skills_code_review_agent/tests/test_phase0_foundation.py

Covers the Phase-0 DoD:
  1. init_db builds the DB and is idempotent.
  2. create_task / update_task_status.
  3. All seven add_*/set_* writers persist.
  4. get_task returns the fully joined record.
  5. SQLiteStore satisfies the ReviewStore protocol (backend-swappable).
  + foreign-key enforcement + bulk finding insert.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError

_EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_EXAMPLE_ROOT))

from agent.db import ReviewStore  # noqa: E402
from agent.db import SQLiteStore  # noqa: E402
from agent.db.init_db import init_db  # noqa: E402
from agent.db.models import CRBase  # noqa: E402

_EXPECTED_TABLES = {
    "review_task", "input_diff", "sandbox_run", "finding",
    "filter_block", "monitor_summary", "review_report",
}


def _new_store() -> SQLiteStore:
    """A store backed by a fresh on-disk temp DB (isolated per test)."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="cr_phase0_")
    os.close(fd)
    return SQLiteStore(path)


class TestInitDb(unittest.IsolatedAsyncioTestCase):
    """DoD #1 — init_db builds the DB and is idempotent."""

    async def _table_names(self, store) -> set:
        eng = store.storage._db_engine
        async with eng.connect() as conn:
            return await conn.run_sync(lambda c: set(sa_inspect(c).get_table_names()))

    async def _finding_indexes(self, store) -> set:
        eng = store.storage._db_engine
        async with eng.connect() as conn:
            return await conn.run_sync(
                lambda c: {i["name"] for i in sa_inspect(c).get_indexes("finding")}
            )

    async def test_init_creates_all_seven_tables_and_dedupe_index(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        store = None
        try:
            await init_db(path, verbose=False)
            store = SQLiteStore(path)
            await store._ensure_engine()
            tables = await self._table_names(store)
            self.assertTrue(_EXPECTED_TABLES.issubset(tables))
            idx = await self._finding_indexes(store)
            self.assertIn("idx_finding_dedup", idx)
        finally:
            if store is not None:
                await store.close()
            if os.path.exists(path):
                try:
                    os.unlink(path)
                except PermissionError:
                    pass  # Windows file lock on rare races; GC will release.

    async def test_init_is_idempotent(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        store = None
        try:
            await init_db(path, verbose=False)
            await init_db(path, verbose=False)  # second run must succeed
            store = SQLiteStore(path)
            await store._ensure_engine()
            tables = await self._table_names(store)
            self.assertTrue(_EXPECTED_TABLES.issubset(tables))
        finally:
            if store is not None:
                await store.close()
            if os.path.exists(path):
                try:
                    os.unlink(path)
                except PermissionError:
                    pass


class TestTaskLifecycle(unittest.IsolatedAsyncioTestCase):
    """DoD #2 — create_task / update_task_status."""

    async def test_create_task_returns_id_and_pending(self):
        store = _new_store()
        try:
            tid = await store.create_task("diff", "a.diff", "dry-run")
            self.assertIsInstance(tid, str)
            task = (await store.get_task(tid))["task"]
            self.assertEqual(task["status"], "pending")
            self.assertEqual(task["input_type"], "diff")
            self.assertEqual(task["mode"], "dry-run")
        finally:
            await store.close()

    async def test_update_task_status_transitions(self):
        store = _new_store()
        try:
            tid = await store.create_task("repo", ".", "real")
            await store.update_task_status(tid, "running")
            self.assertEqual((await store.get_task(tid))["task"]["status"], "running")
            await store.update_task_status(tid, "done", total_duration_ms=1234)
            task = (await store.get_task(tid))["task"]
            self.assertEqual(task["status"], "done")
            self.assertEqual(task["total_duration_ms"], 1234)
        finally:
            await store.close()


class TestChildWrites(unittest.IsolatedAsyncioTestCase):
    """DoD #3 — all seven add_*/set_* writers persist rows."""

    async def test_add_input_diff(self):
        store = _new_store()
        try:
            tid = await store.create_task("diff", "a.diff", "dry-run")
            did = await store.add_input_diff(tid, "src/a.py", "deadbeef", 3, 42, "3 hunks")
            self.assertTrue(did)
            diffs = (await store.get_task(tid))["input_diffs"]
            self.assertEqual(len(diffs), 1)
            self.assertEqual(diffs[0]["file_path"], "src/a.py")
            self.assertEqual(diffs[0]["hunk_count"], 3)
        finally:
            await store.close()

    async def test_add_sandbox_run(self):
        store = _new_store()
        try:
            tid = await store.create_task("diff", "a.diff", "dry-run")
            rid = await store.add_sandbox_run(
                tid, "container", "run_checks.py", "ok", 1200, 0, 2048, 0, 3)
            runs = (await store.get_task(tid))["sandbox_runs"]
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["runtime"], "container")
            self.assertEqual(runs[0]["masked_count"], 3)
        finally:
            await store.close()

    async def test_add_finding_single(self):
        store = _new_store()
        try:
            tid = await store.create_task("diff", "a.diff", "dry-run")
            fid = await store.add_finding(
                tid, "critical", "security", "src/a.py", 10, "SQL injection",
                "cursor.execute(q % x)", "Use parameterized query", 0.95, "rule", "findings")
            f = (await store.get_task(tid))["findings"][0]
            self.assertEqual(f["severity"], "critical")
            self.assertEqual(f["confidence"], 0.95)
            self.assertEqual(f["bucket"], "findings")
        finally:
            await store.close()

    async def test_add_finding_bulk(self):
        store = _new_store()
        try:
            tid = await store.create_task("diff", "a.diff", "dry-run")
            payload = [
                {"severity": "high", "category": "async", "file": "b.py", "line": i,
                 "title": f"unawaited {i}", "evidence": "foo()", "recommendation": "await it",
                 "confidence": 0.82, "source": "rule", "bucket": "findings"}
                for i in range(50)
            ]
            ids = await store.add_findings(tid, payload)
            self.assertEqual(len(ids), 50)
            self.assertEqual(len(set(ids)), 50)
            self.assertEqual(len((await store.get_task(tid))["findings"]), 50)
        finally:
            await store.close()

    async def test_add_filter_block(self):
        store = _new_store()
        try:
            tid = await store.create_task("diff", "a.diff", "dry-run")
            await store.add_filter_block(tid, "high-risk", "evil.py", "deny", "rm -rf /")
            blocks = (await store.get_task(tid))["filter_blocks"]
            self.assertEqual(blocks[0]["decision"], "deny")
        finally:
            await store.close()

    async def test_set_monitor_summary(self):
        store = _new_store()
        try:
            tid = await store.create_task("diff", "a.diff", "dry-run")
            await store.set_monitor_summary(tid, {
                "finding_count": 7, "sev_critical": 1, "sev_high": 2,
                "exception_types": {"timeout": 2, "oom": 1},
            })
            ms = (await store.get_task(tid))["monitor_summary"]
            self.assertEqual(ms["finding_count"], 7)
            self.assertEqual(ms["sev_critical"], 1)
            self.assertEqual(json.loads(ms["exception_types"]), {"timeout": 2, "oom": 1})
        finally:
            await store.close()

    async def test_set_monitor_summary_upsert(self):
        store = _new_store()
        try:
            tid = await store.create_task("diff", "a.diff", "dry-run")
            await store.set_monitor_summary(tid, {"finding_count": 3})
            await store.set_monitor_summary(tid, {"finding_count": 9})
            # 1:1 — second call replaces, not duplicates.
            ms = (await store.get_task(tid))["monitor_summary"]
            self.assertEqual(ms["finding_count"], 9)
        finally:
            await store.close()

    async def test_set_report_and_upsert(self):
        store = _new_store()
        try:
            tid = await store.create_task("diff", "a.diff", "dry-run")
            rid1 = await store.set_report(tid, "a.json", "a.md", "v1")
            rid2 = await store.set_report(tid, "b.json", "b.md", "v2")
            self.assertEqual(rid1, rid2)
            rep = (await store.get_task(tid))["report"]
            self.assertEqual(rep["report_json_path"], "b.json")
            self.assertEqual(rep["summary"], "v2")
        finally:
            await store.close()


class TestGetTaskJoin(unittest.IsolatedAsyncioTestCase):
    """DoD #4 — get_task returns the fully joined record."""

    async def test_full_join_record_shape(self):
        store = _new_store()
        try:
            tid = await store.create_task("diff", "fixture/03.diff", "real")
            await store.update_task_status(tid, "done", total_duration_ms=9000)
            await store.add_input_diff(tid, "x.py", "abc", 1, 5, "1 hunk")
            await store.add_input_diff(tid, "y.py", "def", 2, 8, "2 hunks")
            await store.add_sandbox_run(tid, "cube", "run_checks.py", "ok", 100, 0, 512, 0, 0)
            await store.add_finding(tid, "high", "resource", "x.py", 4, "leak", "open()",
                                    "close()", 0.9, "sandbox", "findings")
            await store.add_finding(tid, "low", "tests", "x.py", 1, "no test", "new fn",
                                    "add test", 0.4, "rule", "needs_human_review")
            await store.add_filter_block(tid, "network", "evil.com", "needs_human_review", "oob")
            await store.set_monitor_summary(tid, {"finding_count": 2, "sev_high": 1})
            await store.set_report(tid, "r.json", "r.md", "ok")

            rec = await store.get_task(tid)
            self.assertEqual(rec["task"]["status"], "done")
            self.assertEqual(len(rec["input_diffs"]), 2)
            self.assertEqual(len(rec["sandbox_runs"]), 1)
            self.assertEqual(len(rec["findings"]), 2)
            self.assertEqual(len(rec["filter_blocks"]), 1)
            self.assertEqual(rec["monitor_summary"]["sev_high"], 1)
            self.assertEqual(rec["report"]["summary"], "ok")
        finally:
            await store.close()

    async def test_get_task_missing_raises(self):
        store = _new_store()
        try:
            with self.assertRaises(KeyError):
                await store.get_task("does-not-exist")
        finally:
            await store.close()


class TestForeignKeyEnforcement(unittest.IsolatedAsyncioTestCase):
    """Risk note — foreign_keys pragma must be ON and enforced."""

    async def test_orphan_finding_blocked(self):
        store = _new_store()
        try:
            with self.assertRaises(IntegrityError):
                await store.add_finding(
                    "nonexistent-task", "low", "tests", "f.py", 1,
                    "x", "y", "z", 0.5, "rule", "warnings")
        finally:
            await store.close()


class TestProtocolConformance(unittest.IsolatedAsyncioTestCase):
    """DoD #5 — SQLiteStore satisfies the ReviewStore protocol."""

    def test_sqlite_store_is_review_store(self):
        self.assertIsInstance(SQLiteStore(":memory:"), ReviewStore)

    def test_protocol_methods_present(self):
        for name in ("create_task", "update_task_status", "add_input_diff",
                     "add_sandbox_run", "add_finding", "add_filter_block",
                     "set_monitor_summary", "set_report", "get_task"):
            self.assertTrue(hasattr(SQLiteStore, name), f"missing {name}")

    def test_fake_store_satisfies_protocol(self):
        class FakeStore:
            async def create_task(self, input_type, input_ref, mode): return "fake"
            async def update_task_status(self, task_id, status, total_duration_ms=None): pass
            async def add_input_diff(self, task_id, file_path, sha256, hunk_count, line_count, summary): return "fake"
            async def add_sandbox_run(self, task_id, runtime, script, status, duration_ms, exit_code, output_bytes, timed_out, masked_count): return "fake"
            async def add_finding(self, task_id, severity, category, file, line, title, evidence, recommendation, confidence, source, bucket): return "fake"
            async def add_filter_block(self, task_id, reason, target, decision, detail): return "fake"
            async def set_monitor_summary(self, task_id, summary): pass
            async def set_report(self, task_id, json_path, md_path, summary): return "fake"
            async def get_task(self, task_id): return {"task": {"id": task_id}}

        self.assertIsInstance(FakeStore(), ReviewStore)


if __name__ == "__main__":
    unittest.main(verbosity=2)
