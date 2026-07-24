# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Phase 6 (Test & acceptance) — end-to-end acceptance tests.

Runs the full Code Review Agent pipeline over the public diff fixtures and
asserts the eight acceptance criteria from phase-6-test-acceptance.md:

  1. all public fixtures run and produce a report
  2. high-risk detection (security/async/db) + no false positive on clean
  3. complete DB record queryable by task_id
  4. sandbox failure degrades gracefully (task still done)
  5. sensitive-info masking rate >= 95% (no plaintext secrets)
  6. dry-run full pipeline <= 2 minutes
  7. Filter deny/review never enters the sandbox
  8. report has all eight sections

Run:
    C:/Users/douzhenyu/.workbuddy/binaries/python/envs/cr_agent/Scripts/python.exe \\
        examples/skills_code_review_agent/tests/test_cr_agent.py
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_EXAMPLE_ROOT))

import agent  # noqa: E402
from agent.db import SQLiteStore  # noqa: E402
from agent.filters import FilterDecision, FilterGovernance  # noqa: E402

_FIXTURES = _EXAMPLE_ROOT / "tests" / "fixtures"
_SKILL_DIR = _EXAMPLE_ROOT / "skills" / "code-review"
_FAILING_SKILL_DIR = _FIXTURES / "skills" / "failing" / "code-review"

# Plaintext secret tokens planted in fixture 08 — must NEVER appear in a report.
_SECRET_PLAINTEXT = [
    "AKIAIOSFODNN7EXAMPLE",
    "sk-1234567890abcdef1234567890ab",
    "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890",
    "SuperSecret123!",
    "pa55w0rd",
]

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


async def _run_pipeline(fixture_name: str | None = None, **kw) -> tuple[str, str]:
    """Run the pipeline on a fixture .diff file (or raw kwargs) → (db, out).

    ``fixture_name`` is the fixture file stem (e.g. ``"01_clean"``); it is fed
    via ``--diff-file`` (the built-in ``--fixture`` only knows clean/security).
    """
    db = tempfile.mktemp(suffix=".db", prefix="cr_p6_")
    out = tempfile.mkdtemp(prefix="cr_p6_out_")
    diff_file = str(_FIXTURES / f"{fixture_name}.diff") if fixture_name else None
    ns = _args(db_path=db, output_dir=out, diff_file=diff_file, **kw)
    await agent._async_main(ns)
    return db, out


def _first_task_id(db_path: str) -> str:
    import sqlite3
    conn = sqlite3.connect(db_path)
    tid = conn.execute("SELECT id FROM review_task ORDER BY created_at LIMIT 1").fetchone()[0]
    conn.close()
    return tid


def _load_report(out: str) -> dict:
    return json.loads(Path(out, "review_report.json").read_text(encoding="utf-8"))


def _all_findings(report: dict) -> list[dict]:
    return [*report["1_findings"], *report["8_warnings"], *report["3_needs_human_review"]]


def _cleanup(db: str, out: str) -> None:
    if os.path.exists(db):
        try:
            os.unlink(db)
        except PermissionError:
            pass
    shutil.rmtree(out, ignore_errors=True)


def _patch_governance(verdict_for_suffix: dict) -> object:
    """Patch ``FilterGovernance.decide`` so scripts whose path ends with a given
    suffix receive the mapped verdict; everything else is allowed.

    Returns the original ``decide`` so the caller can restore it. Use this to
    force a specific Skill script (parse_diff / run_checks / dedupe /
    mask_secrets) into deny / needs_human_review and prove the orchestrator
    stops that phase *before* executing the script.
    """
    def _decide(self, script_path, script_content, budget=None):
        for suffix, verdict in verdict_for_suffix.items():
            if script_path.endswith(suffix):
                return FilterDecision(
                    verdict=verdict, reason="test", target=script_path,
                    detail=f"simulated {verdict} for acceptance test")
        return FilterDecision(verdict="allow", reason="ok", target=script_path, detail="")

    original = FilterGovernance.decide
    FilterGovernance.decide = _decide  # type: ignore[assignment]
    return original


def _restore_governance(original) -> None:
    if original is None:
        return
    try:
        FilterGovernance.decide = original  # type: ignore[assignment]
    except Exception:
        pass


class TestPerFixture(unittest.IsolatedAsyncioTestCase):
    """Acceptance #1 + #2 — each fixture runs, high-risk detected, clean has 0."""

    async def test_clean_diff(self):
        db, out = await _run_pipeline("01_clean")
        report = _load_report(out)
        self.assertEqual(report["1_findings"], [], "clean diff must have 0 findings (no false positive)")
        self.assertEqual(_all_findings(report), [])
        _cleanup(db, out)

    async def test_security_detection(self):
        db, out = await _run_pipeline("02_security")
        report = _load_report(out)
        sec = [f for f in report["1_findings"] if f["category"] == "security"]
        self.assertTrue(sec, "security fixture must surface a security finding")
        self.assertTrue(
            any(f["severity"] == "critical" for f in sec),
            "security critical (SQLi) must be detected",
        )
        _cleanup(db, out)

    async def test_async_leak(self):
        db, out = await _run_pipeline("03_async_leak")
        report = _load_report(out)
        async_hi = [
            f for f in _all_findings(report)
            if f["category"] == "async" and f["severity"] == "high"
        ]
        self.assertTrue(async_hi, "async high-severity leak must be detected")
        _cleanup(db, out)

    async def test_db_lifecycle(self):
        db, out = await _run_pipeline("04_db_lifecycle")
        report = _load_report(out)
        db_hi = [
            f for f in _all_findings(report)
            if f["category"] == "db" and f["severity"] == "high"
        ]
        self.assertTrue(db_hi, "db high-severity lifecycle issue must be detected")
        _cleanup(db, out)

    async def test_missing_tests(self):
        db, out = await _run_pipeline("05_missing_tests")
        report = _load_report(out)
        tests = [f for f in _all_findings(report) if f["category"] == "tests"]
        self.assertTrue(tests, "new public function without a test must be flagged")
        self.assertEqual(tests[0]["severity"], "low")
        _cleanup(db, out)

    async def test_dedup(self):
        db, out = await _run_pipeline("06_duplicate")
        report = _load_report(out)
        async_findings = [f for f in _all_findings(report) if f["category"] == "async"]
        # Two raw diagnostics on the same (file, line, category) collapse to one.
        self.assertEqual(len(async_findings), 1, "duplicate diagnostics must merge to one")
        _cleanup(db, out)


class TestSandboxFailure(unittest.IsolatedAsyncioTestCase):
    """Acceptance #4 — sandbox failure degrades; task still completes.

    The failure is injected at the runtime-factory level (not by patching a
    single backend), so it fires regardless of which sandbox backend the agent
    actually selects — local, container, or cube. This fixes the previous test
    that only patched ``LocalRuntime.run`` and was silently bypassed whenever
    docker was available (the agent then used ``ContainerRuntime`` instead).
    """

    async def test_sandbox_fail(self):
        from agent.sandbox import build_runtime_with_fallback, select_runtime

        class BoomRuntime:
            """A sandbox backend whose execution always fails."""

            def ensure_available(self) -> bool:
                return True

            async def run(self, script_path, input, policy=None):
                raise RuntimeError("sandbox timeout / crash")

        def _boom_factory(*a, **k):
            return (BoomRuntime(), "boom")

        with mock.patch(
            "agent.sandbox.build_runtime_with_fallback", _boom_factory
        ), mock.patch(
            "agent.sandbox.select_runtime", lambda *a, **k: BoomRuntime()
        ):
            db, out = await _run_pipeline("07_sandbox_fail", mode="real")

        store = SQLiteStore(db)
        try:
            rec = await store.get_task(_first_task_id(db))
            # (a) graceful degradation — the task still completes.
            self.assertEqual(rec["task"]["status"], "done")
            # (b) findings are still produced via the FakeRunner fallback.
            self.assertGreater(
                len(rec["findings"]), 0, "degraded run should still yield findings"
            )
            # (c) the failed sandbox execution is recorded in the DB
            #     (ties P1-1: every sandbox run must be persisted).
            self.assertEqual(
                len(rec["sandbox_runs"]), 1,
                "the failed sandbox run must be recorded in the DB",
            )
            self.assertEqual(rec["sandbox_runs"][0]["status"], "failed")
        finally:
            await store.close()
        _cleanup(db, out)

    async def test_real_failure_fixture_records_failed_sandbox_run(self):
        """Fixture 09 fails by executing a checker that exits nonzero."""

        db, out = await _run_pipeline(
            "09_sandbox_runtime_failure",
            mode="real",
            skill_dir=str(_FAILING_SKILL_DIR),
        )

        store = SQLiteStore(db)
        try:
            rec = await store.get_task(_first_task_id(db))
            self.assertEqual(rec["task"]["status"], "done")
            self.assertEqual(len(rec["sandbox_runs"]), 1)
            run = rec["sandbox_runs"][0]
            self.assertEqual(run["status"], "failed")
            self.assertNotEqual(run["exit_code"], 0)
            self.assertGreater(len(rec["findings"]), 0)

            report = _load_report(out)
            self.assertEqual(report["6_sandbox_runs"][0]["status"], "failed")
            self.assertNotEqual(report["6_sandbox_runs"][0]["exit_code"], 0)
        finally:
            await store.close()
        _cleanup(db, out)


class TestLocalRuntimeEnvWhitelist(unittest.IsolatedAsyncioTestCase):
    """P1-2 — LocalRuntime must enforce the env-variable whitelist."""

    async def test_non_whitelisted_env_not_leaked(self):
        from agent.sandbox import LocalRuntime, SandboxPolicy

        probe_dir = tempfile.mkdtemp(prefix="cr_envprobe_")
        probe = Path(probe_dir) / "probe.py"
        probe.write_text(
            "import os\nprint(os.environ.get('CR_P3_VISIBLE'))\n", encoding="utf-8"
        )
        os.environ["CR_P3_VISIBLE"] = "leaked"
        try:
            rt = LocalRuntime()
            res = await rt.run(
                str(probe),
                {"_rules": {}},
                SandboxPolicy(env_whitelist=["PATH", "HOME", "LANG"]),
            )
        finally:
            os.environ.pop("CR_P3_VISIBLE", None)
            shutil.rmtree(probe_dir, ignore_errors=True)

        # The script must actually have executed (not silently failed).
        self.assertEqual(
            res.status, "ok", f"sandbox script should run; stderr={res.stderr!r}"
        )
        # CR_P3_VISIBLE is NOT in the whitelist, so it must not be visible.
        self.assertNotIn(
            "leaked", res.stdout,
            "non-whitelisted env var leaked into the sandbox",
        )

    async def test_whitelisted_env_is_visible(self):
        from agent.sandbox import LocalRuntime, SandboxPolicy

        probe_dir = tempfile.mkdtemp(prefix="cr_envprobe_")
        probe = Path(probe_dir) / "probe.py"
        probe.write_text(
            "import os\nprint(os.environ.get('CR_P3_ALLOWED'))\n", encoding="utf-8"
        )
        os.environ["CR_P3_ALLOWED"] = "visible"
        try:
            rt = LocalRuntime()
            res = await rt.run(
                str(probe),
                {"_rules": {}},
                SandboxPolicy(env_whitelist=["PATH", "HOME", "LANG", "CR_P3_ALLOWED"]),
            )
        finally:
            os.environ.pop("CR_P3_ALLOWED", None)
            shutil.rmtree(probe_dir, ignore_errors=True)

        self.assertEqual(res.status, "ok", f"stderr={res.stderr!r}")
        self.assertIn("visible", res.stdout, "whitelisted env var must be visible")


class TestSensitiveMasking(unittest.IsolatedAsyncioTestCase):
    """Acceptance #5 — masking rate >= 95%, no plaintext secrets in output."""

    async def test_sensitive_masking(self):
        db, out = await _run_pipeline("08_sensitive_info")
        report = _load_report(out)
        # 1) sensitive findings are detected.
        sensitive = [f for f in report["1_findings"] if f["category"] == "sensitive"]
        self.assertGreaterEqual(len(sensitive), 5, "multiple secret formats must be detected")

        # 2) no plaintext secret anywhere in the JSON or MD report.
        blob = json.dumps(report, ensure_ascii=False)
        blob += "\n" + Path(out, "review_report.md").read_text(encoding="utf-8")
        for secret in _SECRET_PLAINTEXT:
            self.assertNotIn(secret, blob, f"plaintext secret leaked: {secret}")

        # 3) redaction actually happened.
        self.assertIn("***REDACTED***", blob, "expected at least one redaction marker")

        # 4) DB-stored evidence is also masked.
        store = SQLiteStore(db)
        try:
            rec = await store.get_task(_first_task_id(db))
            db_blob = json.dumps(rec["findings"], ensure_ascii=False)
            for secret in _SECRET_PLAINTEXT:
                self.assertNotIn(secret, db_blob, f"plaintext secret in DB: {secret}")
        finally:
            await store.close()
        _cleanup(db, out)


class TestReportAndDb(unittest.IsolatedAsyncioTestCase):
    """Acceptance #3, #8 — complete DB record + eight report sections."""

    async def test_report_eight_sections(self):
        db, out = await _run_pipeline("02_security")
        report = _load_report(out)
        self.assertTrue(_REPORT_KEYS.issubset(report.keys()), "report missing sections")
        md = Path(out, "review_report.md").read_text(encoding="utf-8")
        for sec in _MD_SECTIONS:
            self.assertIn(sec, md, f"md missing section header: {sec}")
        _cleanup(db, out)

    async def test_db_query_by_task_id(self):
        db, out = await _run_pipeline("02_security")
        store = SQLiteStore(db)
        try:
            tid = _first_task_id(db)
            rec = await store.get_task(tid)
            for key in ("task", "input_diffs", "sandbox_runs", "findings",
                        "filter_blocks", "monitor_summary", "report"):
                self.assertIn(key, rec, f"get_task missing {key}")
            self.assertEqual(rec["task"]["id"], tid)
            self.assertEqual(rec["task"]["status"], "done")
            self.assertGreater(len(rec["input_diffs"]), 0)
        finally:
            await store.close()
        _cleanup(db, out)


class TestFilterGating(unittest.IsolatedAsyncioTestCase):
    """Acceptance #7 — Filter deny/review never *executes* the checker.

    The previous test only proved ``LocalRuntime.run`` is not called. It did
    NOT prove the in-process ``FakeRunner`` fallback (which imports and runs
    ``run_checks.py`` *outside* the sandbox) was skipped. A denied script is a
    denied script regardless of backend, so we patch BOTH execution paths to
    raise, and assert zero raw findings flow through — covering dry-run and
    real mode, for both ``deny`` and ``needs_human_review`` verdicts.
    """

    def _force_verdict(self, verdict):
        def _decide(self, script_path, script_content, budget=None):
            if script_path.endswith("run_checks.py"):
                return FilterDecision(
                    verdict=verdict, reason="test", target=script_path,
                    detail=f"simulated {verdict} for acceptance test",
                )
            return FilterDecision(verdict="allow", reason="ok", target=script_path, detail="")

        original = FilterGovernance.decide
        FilterGovernance.decide = _decide  # type: ignore[assignment]
        return original

    async def _run_blocked(self, verdict, mode):
        from agent.agent import FakeRunner
        from agent.sandbox import LocalRuntime

        original_gov = self._force_verdict(verdict)

        # If EITHER execution path fires on a denied/reviewed checker, fail loud.
        async def _sandbox_must_not_run(self_, script_path, input, policy):
            raise AssertionError("sandbox must NOT run a denied/reviewed checker")
        orig_local = LocalRuntime.run
        LocalRuntime.run = _sandbox_must_not_run  # type: ignore[assignment]

        orig_fake = FakeRunner.run
        async def _fake_must_not_run(self_, changeset, ruleset):
            raise AssertionError("FakeRunner must NOT execute a denied/reviewed checker")
        FakeRunner.run = _fake_must_not_run  # type: ignore[assignment]

        db = out = None
        try:
            db, out = await _run_pipeline("02_security", mode=mode)
            store = SQLiteStore(db)
            try:
                rec = await store.get_task(_first_task_id(db))
                self.assertEqual(rec["task"]["status"], "done")
                # (a) a filter_block must be recorded for the blocked checker.
                blocks = rec["filter_blocks"]
                self.assertTrue(blocks, "blocked checker must record a filter_block")
                self.assertTrue(
                    any(b["decision"] == verdict for b in blocks),
                    f"filter_block decision must be {verdict}",
                )
                # (b) NO raw findings may flow through — the checker never ran.
                self.assertEqual(
                    len(rec["findings"]), 0,
                    "a blocked checker must produce zero findings",
                )
                # (c) the (non-)execution is recorded as blocked in the DB.
                runs = rec["sandbox_runs"]
                self.assertEqual(len(runs), 1, "one sandbox decision must be recorded")
                self.assertEqual(runs[0]["runtime"], "blocked")
                self.assertEqual(runs[0]["status"], verdict)
            finally:
                await store.close()
        finally:
            # Restore every patch independently — a failure in one must not
            # leave a global monkey-patch in place for the next test (which
            # would cause hard-to-diagnose cross-test contamination).
            for _restore in (
                lambda: setattr(FilterGovernance, "decide", original_gov),
                lambda: setattr(LocalRuntime, "run", orig_local),
                lambda: setattr(FakeRunner, "run", orig_fake),
            ):
                try:
                    _restore()
                except Exception:
                    pass
            if db or out:
                _cleanup(db, out)

    async def test_deny_blocks_real(self):
        await self._run_blocked("deny", "real")

    async def test_deny_blocks_dry_run(self):
        await self._run_blocked("deny", "dry-run")

    async def test_review_blocks_real(self):
        await self._run_blocked("needs_human_review", "real")

    async def test_review_blocks_dry_run(self):
        await self._run_blocked("needs_human_review", "dry-run")


class TestUnifiedGate(unittest.IsolatedAsyncioTestCase):
    """Unified Filter gate — Acceptance #7 extended to EVERY Skill script.

    run_checks.py was already gated. These tests prove the gate also holds for
    parse_diff (L1), dedupe (L5) and mask_secrets (helper used by dedupe + LLM
    triage): when the Filter returns deny / needs_human_review for one of them,
    the orchestrator stops that phase *before* the script executes — in-process
    no less than in the sandbox. We prove non-execution by monkey-patching the
    script's entry point to raise: if the gate is broken the patch fires and the
    test errors; if the gate holds, the patch is never invoked.
    """

    async def _assert_blocked_execution(
        self, *, suffix, verdict, fixture, mode, llm_enabled=False,
        expect_no_sandbox_run=False,
    ):
        """Run a pipeline with ``suffix`` forced to ``verdict`` and assert the
        matching script never executed, a filter_block was recorded, and zero
        findings flowed through.

        The whole body is wrapped in try/finally with the ``try`` at the very
        top, so the FilterGovernance patch is ALWAYS restored even if an early
        line raises — otherwise a leaked ``decide`` would contaminate the next
        test (e.g. deny parse_diff and zero out its findings).
        """
        import dedupe as dedupe_mod
        from agent.agent import FakeRunner
        from agent.llm import LlmTriage
        from agent.sandbox import LocalRuntime

        # Pre-initialize so the finally can restore unconditionally (a patch
        # that was never installed simply restores to None-safe state).
        original_gov = None
        orig_parse = orig_local = orig_fake = orig_dedupe = orig_triage = None

        # Set up every patch inside the try so the finally (which restores all
        # of them) is guaranteed to run even on an early failure.
        try:
            original_gov = _patch_governance({suffix: verdict})

            orig_parse = agent.agent.parse_diff
            def _parse_must_not_run(*a, **k):
                raise AssertionError(f"parse_diff must NOT execute when filtered ({verdict})")

            orig_local = LocalRuntime.run
            async def _sandbox_must_not_run(self_, script_path, input, policy):
                raise AssertionError("sandbox must NOT run when a script is filtered")

            orig_fake = FakeRunner.run
            async def _fake_must_not_run(self_, changeset, ruleset):
                raise AssertionError("FakeRunner must NOT execute a filtered script")

            orig_dedupe = dedupe_mod.dedupe
            def _dedupe_must_not_run(*a, **k):
                raise AssertionError("dedupe must NOT execute when filtered")
            orig_triage = LlmTriage.run
            async def _triage_must_not_run(self_, *a, **k):
                raise AssertionError("LLM triage (masked diff) must NOT run when filtered")

            # Install the raising stub ONLY on the script under test. The other
            # declared scripts are allowed in that scenario and MUST run, so
            # patching them to raise would be a test bug (not a product bug).
            # (run_checks.py executors are jointly exercised by TestFilterGating.)
            if suffix == "parse_diff.py":
                agent.agent.parse_diff = _parse_must_not_run  # type: ignore[assignment]
            if suffix in ("dedupe.py", "mask_secrets.py"):
                dedupe_mod.dedupe = _dedupe_must_not_run  # type: ignore[assignment]
            if suffix == "mask_secrets.py":
                LlmTriage.run = _triage_must_not_run  # type: ignore[assignment]

            # Point the LLM config at an empty .env so the product .env (which
            # now carries a real DeepSeek key) cannot leak in and flip is_enabled.
            llm_env = tempfile.NamedTemporaryFile(
                suffix=".env", prefix="cr_empty_", delete=False).name
            Path(llm_env).write_text("", encoding="utf-8")

            db = out = None
            try:
                db, out = await _run_pipeline(
                    fixture, mode=mode, enable_llm=llm_enabled, llm_env=llm_env)
                store = SQLiteStore(db)
                try:
                    rec = await store.get_task(_first_task_id(db))
                    self.assertEqual(rec["task"]["status"], "done")
                    blocks = rec["filter_blocks"]
                    self.assertTrue(blocks, "filtered script must record a filter_block")
                    self.assertTrue(
                        any(b["decision"] == verdict and b["target"].endswith(suffix)
                            for b in blocks),
                        f"filter_block must be {verdict} for *{suffix}",
                    )
                    self.assertEqual(
                        len(rec["findings"]), 0,
                        f"a filtered *{suffix} must produce zero findings",
                    )
                    if expect_no_sandbox_run:
                        self.assertEqual(
                            len(rec["sandbox_runs"]), 0,
                            "no sandbox decision may be recorded when parse is blocked",
                        )
                finally:
                    await store.close()
            finally:
                if os.path.exists(llm_env):
                    os.unlink(llm_env)
        finally:
            # Restore every patch independently — a failure restoring one must
            # not leave another global monkey-patch in place for the next test.
            _restore_governance(original_gov)
            for _mod, _name, _orig in (
                (agent.agent, "parse_diff", orig_parse),
                (LocalRuntime, "run", orig_local),
                (FakeRunner, "run", orig_fake),
                (dedupe_mod, "dedupe", orig_dedupe),
                (LlmTriage, "run", orig_triage),
            ):
                if _orig is None:
                    continue
                try:
                    setattr(_mod, _name, _orig)
                except Exception:
                    pass
            if db or out:
                _cleanup(db, out)

    async def test_parse_diff_deny_stops_everything(self):
        # parse_diff is the L1 foundation; a deny must stop the whole chain
        # (no sandbox, no dedupe) and never execute the script.
        await self._assert_blocked_execution(
            suffix="parse_diff.py", verdict="deny", fixture="02_security",
            mode="real", expect_no_sandbox_run=True)

    async def test_dedupe_deny_skips_dedupe(self):
        # run_checks is allowed (so raw findings exist), but dedupe is denied →
        # dedupe() must NOT run and zero findings flow through.
        await self._assert_blocked_execution(
            suffix="dedupe.py", verdict="deny", fixture="02_security", mode="real")

    async def test_mask_secrets_deny_skips_dedupe_and_triage(self):
        # mask_secrets gates both the dedupe helper and the LLM triage; a deny
        # must skip dedupe AND skip sending the (unmasked) diff to the model.
        await self._assert_blocked_execution(
            suffix="mask_secrets.py", verdict="deny", fixture="02_security",
            mode="real", llm_enabled=True)


class TestGateNoModuleImport(unittest.IsolatedAsyncioTestCase):
    """Proof that a denied Skill script is NEVER even *imported* — its module
    top-level code and any ``load_rules``-style call must not execute. Function
    patches (TestFilterGating / TestUnifiedGate) cannot catch module-level
    import, so we evict the module from ``sys.modules`` first and assert it is
    NOT re-imported during a denied run.

    NOTE: ``mask_secrets`` is intentionally excluded — ``agent/sandbox/
    runtime.py`` imports it at module top for always-on output redaction (a
    mandatory safety boundary, not a pipeline execution), so its absence can't
    be asserted. The dedupe/LLM-triage *usage* of mask_secrets is still gated
    (lazy import inside ``dedupe()`` / ``LlmTriage.run``, only on allow).
    """

    async def _assert_module_not_imported(self, *, suffix, verdict, fixture, mode):
        import sys

        from agent.sandbox import LocalRuntime

        mod_name = {
            "run_checks.py": "run_checks",
            "dedupe.py": "dedupe",
        }[suffix]

        original_gov = None
        saved_module = None
        llm_env = None
        db = out = None
        try:
            original_gov = _patch_governance({suffix: verdict})
            # Evict the module so a fresh import during the run is detectable.
            saved_module = sys.modules.pop(mod_name, None)

            llm_env = tempfile.NamedTemporaryFile(
                suffix=".env", prefix="cr_empty_", delete=False).name
            Path(llm_env).write_text("", encoding="utf-8")

            db, out = await _run_pipeline(
                fixture, mode=mode, enable_llm=False, llm_env=llm_env)
            # The denied script must NOT have been imported into this process —
            # no module top-level, no load_rules, no execution of any kind.
            self.assertNotIn(
                mod_name, sys.modules,
                f"{mod_name} must NOT be imported when the Filter denies it")
        finally:
            _restore_governance(original_gov)
            if saved_module is not None:
                sys.modules[mod_name] = saved_module
            if llm_env and os.path.exists(llm_env):
                os.unlink(llm_env)
            if db or out:
                _cleanup(db, out)

    async def test_run_checks_deny_not_imported(self):
        # run_checks.py is denied → the checker module is never imported, yet
        # the blocked decision is still recorded (DB integrity preserved).
        await self._assert_module_not_imported(
            suffix="run_checks.py", verdict="deny", fixture="02_security", mode="real")

    async def test_dedupe_deny_not_imported(self):
        # dedupe.py is denied → dedupe() never runs AND the module is never
        # imported. The empty DedupeResult comes from the benign cr_models.
        await self._assert_module_not_imported(
            suffix="dedupe.py", verdict="deny", fixture="02_security", mode="real")


class TestDryRunPerformance(unittest.IsolatedAsyncioTestCase):
    """Acceptance #6 — full dry-run pipeline completes within 2 minutes."""

    async def test_dry_run_under_2min(self):
        db = tempfile.mktemp(suffix=".db", prefix="cr_p6_perf_")
        out = tempfile.mkdtemp(prefix="cr_p6_perf_out_")
        ns = _args(
            db_path=db, output_dir=out,
            diff_file=str(_FIXTURES / "02_security.diff"), mode="dry-run")
        start = time.perf_counter()
        await agent._async_main(ns)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 120.0, f"dry-run took {elapsed:.1f}s (> 120s)")
        _cleanup(db, out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
