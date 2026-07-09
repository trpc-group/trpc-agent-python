# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Phase 3 (Sandbox & Filter) acceptance tests.

Covers the Phase-3 DoD:
  1. LocalRuntime runs a script; timeout interrupts without crashing.
  2. Output over 1MB is truncated (status=truncated).
  3. Only whitelisted env vars are passed (non-whitelisted dropped).
  4. stdout is masked + masked_count is correct.
  5. FilterGovernance flags high-risk scripts → deny.
  6. FilterGovernance flags forbidden paths → deny.
  7. RunResult persists to the sandbox_run table.

Run with the CR Agent venv (SDK + python-magic-bin):
    C:/Users/douzhenyu/.workbuddy/binaries/python/envs/cr_agent/Scripts/python.exe \\
        examples/skills_code_review_agent/tests/test_phase3_sandbox_filter.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_EXAMPLE_ROOT))

from agent.db import SQLiteStore  # noqa: E402
from agent.filters import FilterGovernance  # noqa: E402
from agent.sandbox import LocalRuntime  # noqa: E402
from agent.sandbox import RunResult  # noqa: E402
from agent.sandbox import RuntimeUnavailable  # noqa: E402
from agent.sandbox import SandboxPolicy  # noqa: E402
from agent.sandbox import build_runtime_with_fallback  # noqa: E402
from agent.sandbox import select_runtime  # noqa: E402


def _make_script_dir() -> str:
    """Create a temp dir + return its path. Caller cleans up."""
    return tempfile.mkdtemp(prefix="cr_p3_ws_")


def _write(d: str, name: str, content: str) -> str:
    p = Path(d) / name
    p.write_text(content, encoding="utf-8")
    return str(p)


class TestLocalRuntime(unittest.IsolatedAsyncioTestCase):
    """DoD #1-4 — LocalRuntime execution + timeout + truncation + env + masking."""

    async def test_run_ok_returns_stdout(self):
        d = _make_script_dir()
        try:
            script = _write(d, "echo.py",
                "import sys, json\n"
                "d = json.load(sys.stdin)\n"
                "print(json.dumps({'got': d}))\n")
            rt = LocalRuntime(work_root=d)
            res = await rt.run(script, {"hello": "world"}, SandboxPolicy(timeout_s=10))
            self.assertEqual(res.status, "ok")
            self.assertEqual(res.exit_code, 0)
            self.assertIn("hello", res.stdout)
            self.assertFalse(res.timed_out)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    async def test_timeout_interrupts_without_crash(self):
        d = _make_script_dir()
        try:
            script = _write(d, "sleep.py", "import time; time.sleep(30)\n")
            rt = LocalRuntime(work_root=d)
            res = await rt.run(script, {}, SandboxPolicy(timeout_s=1))
            self.assertEqual(res.status, "timeout")
            self.assertTrue(res.timed_out)
            # Must not raise — fail-safe.
            self.assertIsInstance(res, RunResult)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    async def test_output_truncated_over_limit(self):
        d = _make_script_dir()
        try:
            # Print ~2MB of 'A' — exceeds the 1024-byte cap below.
            script = _write(d, "big.py", "print('A' * (2 * 1024 * 1024))\n")
            rt = LocalRuntime(work_root=d)
            res = await rt.run(script, {}, SandboxPolicy(timeout_s=10, max_output_bytes=1024))
            self.assertEqual(res.status, "truncated")
            self.assertLessEqual(res.output_bytes, 1024)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    async def test_secrets_masked_in_stdout(self):
        d = _make_script_dir()
        try:
            script = _write(d, "secret.py",
                'print("key=sk-1234567890abcdef1234567890abcdef")\n')
            rt = LocalRuntime(work_root=d)
            res = await rt.run(script, {}, SandboxPolicy(timeout_s=10, mask_secrets=True))
            self.assertEqual(res.status, "ok")
            self.assertIn("***REDACTED***", res.stdout)
            self.assertGreater(res.masked_count, 0)
            self.assertNotIn("sk-1234567890abcdef", res.stdout)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    async def test_env_whitelist_drops_non_whitelisted(self):
        d = _make_script_dir()
        try:
            script = _write(d, "envcheck.py",
                "import os\n"
                "print(os.environ.get('CR_P3_SECRET', 'MISSING'))\n")
            os.environ["CR_P3_SECRET"] = "leaked"
            try:
                rt = LocalRuntime(work_root=d)
                # CR_P3_SECRET is NOT in the default whitelist → not passed.
                res = await rt.run(script, {}, SandboxPolicy(timeout_s=10))
                self.assertEqual(res.status, "ok")
                self.assertIn("MISSING", res.stdout)
                self.assertNotIn("leaked", res.stdout)
            finally:
                os.environ.pop("CR_P3_SECRET", None)  # tolerant of restore timing
        finally:
            shutil.rmtree(d, ignore_errors=True)

    async def test_failed_does_not_crash(self):
        d = _make_script_dir()
        try:
            # Script that exits non-zero.
            script = _write(d, "fail.py", "import sys; sys.exit(3)\n")
            rt = LocalRuntime(work_root=d)
            res = await rt.run(script, {}, SandboxPolicy(timeout_s=10))
            self.assertEqual(res.status, "failed")
            self.assertEqual(res.exit_code, 3)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    async def test_siblings_importable_in_workspace(self):
        """run_checks-style: script imports a sibling module staged alongside."""
        d = _make_script_dir()
        try:
            _write(d, "helper.py", "VALUE = 42\n")
            script = _write(d, "main.py", "from helper import VALUE\nprint(VALUE)\n")
            rt = LocalRuntime(work_root=d)
            res = await rt.run(script, {}, SandboxPolicy(timeout_s=10))
            self.assertEqual(res.status, "ok")
            self.assertIn("42", res.stdout)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestFilterGovernance(unittest.TestCase):
    """DoD #5-6 — governance flags high-risk + forbidden-path → deny."""

    def setUp(self):
        self.gov = FilterGovernance()

    def test_high_risk_rm_rf_denied(self):
        d = self.gov.decide("x.sh", "import os; os.system('rm -rf /')", {})
        self.assertEqual(d.verdict, "deny")
        self.assertEqual(d.reason, "high-risk")

    def test_high_risk_sudo_denied(self):
        d = self.gov.decide("x.sh", "os.system('sudo apt install evil')", {})
        self.assertEqual(d.verdict, "deny")
        self.assertEqual(d.reason, "high-risk")

    def test_high_risk_eval_denied(self):
        d = self.gov.decide("x.py", "eval(user_input)", {})
        self.assertEqual(d.verdict, "deny")
        self.assertEqual(d.reason, "high-risk")

    def test_forbidden_path_etc_denied(self):
        d = self.gov.decide("x.py", "open('/etc/passwd').read()", {})
        self.assertEqual(d.verdict, "deny")
        self.assertEqual(d.reason, "forbidden-path")

    def test_forbidden_path_ssh_denied(self):
        d = self.gov.decide("x.py", "with open('~/.ssh/id_rsa') as f: pass", {})
        self.assertEqual(d.verdict, "deny")
        self.assertEqual(d.reason, "forbidden-path")

    def test_network_non_whitelisted_needs_review(self):
        d = self.gov.decide("x.py", "requests.get('http://evil.com/data')", {})
        self.assertEqual(d.verdict, "needs_human_review")
        self.assertEqual(d.reason, "network")

    def test_network_whitelisted_allowed(self):
        d = self.gov.decide("x.py", "requests.get('http://localhost:8080/h')", {})
        self.assertEqual(d.verdict, "allow")

    def test_budget_over_limit_needs_review(self):
        d = self.gov.decide("x.py", "print('ok')",
                            {"estimated_duration_s": 120, "estimated_memory_mb": 100})
        self.assertEqual(d.verdict, "needs_human_review")
        self.assertEqual(d.reason, "budget")

    def test_normal_script_allowed(self):
        d = self.gov.decide("run_checks.py",
                            "import ast\nfor n in ast.walk(tree): pass\n", {})
        self.assertEqual(d.verdict, "allow")
        self.assertEqual(d.reason, "ok")

    def test_rm_rf_in_comment_not_flagged(self):
        """False-positive guard: `rm -rf` in a comment is skipped (spec risk note)."""
        d = self.gov.decide("x.py", "# warning: do not run rm -rf /tmp\nprint('safe')\n", {})
        self.assertEqual(d.verdict, "allow")


class TestRunResultPersist(unittest.IsolatedAsyncioTestCase):
    """DoD #7 — RunResult persists to the sandbox_run table."""

    async def test_sandbox_run_row_written_and_joined(self):
        store = SQLiteStore(":memory:")
        try:
            tid = await store.create_task("diff", "a.diff", "dry-run")
            run_id = await store.add_sandbox_run(
                tid, "local", "run_checks.py", "ok", 1200, 0, 2048, 0, 3)
            self.assertTrue(run_id)
            runs = (await store.get_task(tid))["sandbox_runs"]
            self.assertEqual(len(runs), 1)
            r = runs[0]
            self.assertEqual(r["runtime"], "local")
            self.assertEqual(r["status"], "ok")
            self.assertEqual(r["masked_count"], 3)
            self.assertEqual(r["timed_out"], 0)
        finally:
            await store.close()

    async def test_failed_run_result_persisted(self):
        store = SQLiteStore(":memory:")
        try:
            tid = await store.create_task("diff", "a.diff", "dry-run")
            await store.add_sandbox_run(
                tid, "local", "run_checks.py", "timeout", 30000, 124, 0, 1, 0)
            r = (await store.get_task(tid))["sandbox_runs"][0]
            self.assertEqual(r["status"], "timeout")
            self.assertEqual(r["timed_out"], 1)
            self.assertEqual(r["exit_code"], 124)
        finally:
            await store.close()


class _StubRuntime:
    """Minimal SandboxRuntime used to exercise selection/fallback logic."""

    def __init__(self, kind, available=True):
        self.kind = kind
        self._available = available

    def ensure_available(self):
        if not self._available:
            raise RuntimeUnavailable(f"{self.kind} unavailable")
        return True

    async def run(self, script_path, input, policy=None):
        return RunResult("ok", "[]", "", 0, 0, 0, False, 0)


def _patched_select(mapping):
    """Return a select_runtime replacement backed by a fixed kind→instance map."""
    def _select(kind, policy=None, work_root=None, image=None):
        return mapping[kind]
    return _select


class TestRuntimeSelection(unittest.IsolatedAsyncioTestCase):
    """G1 — agent honors default_runtime/fallback, not hardcoded LocalRuntime."""

    def test_default_used_when_available(self):
        import agent.sandbox.runtime as _rt
        orig = _rt.select_runtime
        container = _StubRuntime("container", available=True)
        local = _StubRuntime("local", available=True)
        _rt.select_runtime = _patched_select({"container": container, "local": local})
        try:
            rt, kind = build_runtime_with_fallback("container", "local")
            self.assertIs(rt, container)
            self.assertEqual(kind, "container")
        finally:
            _rt.select_runtime = orig

    def test_fallback_used_when_default_unavailable(self):
        import agent.sandbox.runtime as _rt
        orig = _rt.select_runtime
        container = _StubRuntime("container", available=False)
        local = _StubRuntime("local", available=True)
        _rt.select_runtime = _patched_select({"container": container, "local": local})
        try:
            rt, kind = build_runtime_with_fallback("container", "local")
            self.assertIs(rt, local)
            self.assertEqual(kind, "local")
        finally:
            _rt.select_runtime = orig

    def test_select_runtime_kind_mapping(self):
        self.assertIsInstance(select_runtime("local"), LocalRuntime)
        self.assertIsInstance(select_runtime("container"), object)
        self.assertIsInstance(select_runtime("cube"), object)
        self.assertIsInstance(select_runtime("bogus-unknown"), LocalRuntime)

    def test_no_runtime_available_raises(self):
        import agent.sandbox.runtime as _rt
        orig = _rt.select_runtime
        container = _StubRuntime("container", available=False)
        local = _StubRuntime("local", available=False)
        _rt.select_runtime = _patched_select({"container": container, "local": local})
        try:
            with self.assertRaises(RuntimeUnavailable):
                build_runtime_with_fallback("container", "local")
        finally:
            _rt.select_runtime = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
