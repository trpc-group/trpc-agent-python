# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Phase 2 (Rules engine) acceptance tests.

Covers every line of the Phase-2 Definition of Done:
  1. Six rule docs present, each with ≥3 concrete rules.
  2. run_checks detects each rule category on a sample diff.
  3. RawFinding has all fields with sensible confidence.
  4. mask_secrets redacts common secret formats with correct count.
  5. A clean diff yields an empty finding list (no false positives).

Run:
    python examples/skills_code_review_agent/tests/test_phase2_rules_engine.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_EXAMPLE_ROOT))

_SCRIPTS_DIR = _EXAMPLE_ROOT / "skills" / "code-review" / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import agent  # noqa: E402
from mask_secrets import mask_secrets  # noqa: E402
from parse_diff import parse_diff  # noqa: E402
from run_checks import RawFinding  # noqa: E402
from run_checks import load_rules  # noqa: E402
from run_checks import run_checks  # noqa: E402

_SKILL_DIR = _EXAMPLE_ROOT / "skills" / "code-review"


def _ruleset() -> dict:
    """A ruleset wired to the real skill dir (exercises load_rules on disk)."""
    return agent.skill_load(_SKILL_DIR)


def _run(diff_text: str) -> list[RawFinding]:
    cs = parse_diff(diff_text)
    return run_checks(cs, _ruleset())


def _diff(path: str, add_lines: list[str], extra_files: list[tuple[str, list[str]]] | None = None) -> str:
    """Build a minimal diff adding the given lines to ``path`` (and optional
    extra files). Each add line is prefixed with '+' automatically."""
    parts = [f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}",
             "@@ -0,0 +1,{0} @@".format(len(add_lines) if add_lines else 1)]
    for ln in add_lines:
        parts.append("+" + ln)
    for fpath, flines in (extra_files or []):
        parts.append(f"diff --git a/{fpath} b/{fpath}")
        parts.append(f"--- a/{fpath}")
        parts.append(f"+++ b/{fpath}")
        parts.append("@@ -0,0 +1,{0} @@".format(len(flines) if flines else 1))
        for ln in flines:
            parts.append("+" + ln)
    return "\n".join(parts) + "\n"


class TestRuleDocs(unittest.TestCase):
    """DoD #1 — six rule docs, each with ≥3 concrete rules."""

    def test_six_categories_loaded(self):
        rs = _ruleset()
        rules = load_rules(rs["skill_dir"], rs["rules"])
        self.assertEqual(
            set(rules.keys()),
            {"security", "sensitive", "async", "resource", "db", "tests"},
        )

    def test_each_category_has_at_least_three_rules(self):
        rs = _ruleset()
        rules = load_rules(rs["skill_dir"], rs["rules"])
        for cat, rlist in rules.items():
            self.assertGreaterEqual(
                len(rlist), 3, f"category {cat} has only {len(rlist)} rules"
            )

    def test_rule_fields_complete(self):
        rs = _ruleset()
        rules = load_rules(rs["skill_dir"], rs["rules"])
        for cat, rlist in rules.items():
            for r in rlist:
                for field in ("id", "pattern", "severity_hint", "confidence", "description"):
                    self.assertIn(field, r, f"{cat} rule missing {field}: {r}")
                self.assertIn(r.get("type"), ("pattern", "ast", "diff", None))

    def test_skill_load_returns_skill_dir(self):
        """P2 added skill_dir so run_checks can locate rule docs."""
        rs = agent.skill_load(_SKILL_DIR)
        self.assertIn("skill_dir", rs)
        self.assertTrue(Path(rs["skill_dir"]).is_absolute())
        self.assertTrue((Path(rs["skill_dir"]) / "SKILL.md").exists())


class TestRunChecksSecurity(unittest.TestCase):
    """DoD #2 — run_checks detects security issues."""

    def test_sql_injection_detected(self):
        findings = _run(_diff("s.py", [
            'def get(uid):',
            '    cur = conn.execute("SELECT * FROM u WHERE id=" + str(uid))',
        ]))
        sec = [f for f in findings if f.category == "security"]
        self.assertTrue(any("SEC001" in f.title for f in sec), sec)
        self.assertEqual(sec[0].severity_hint, "critical")
        self.assertGreaterEqual(sec[0].confidence, 0.9)

    def test_command_injection_detected(self):
        findings = _run(_diff("s.py", [
            '    os.system("ls " + name)',
        ]))
        self.assertTrue(any("SEC002" in f.title for f in findings))

    def test_pickle_deserialization_detected(self):
        findings = _run(_diff("s.py", [
            '    data = pickle.loads(user_bytes)',
        ]))
        self.assertTrue(any("SEC004" in f.title for f in findings))

    def test_hardcoded_key_detected(self):
        findings = _run(_diff("s.py", [
            'API_KEY = "sk-1234567890abcdef1234567890"',
        ]))
        sec = [f for f in findings if f.category == "security"]
        self.assertTrue(any("SEC003" in f.title for f in sec))


class TestRunChecksSensitive(unittest.TestCase):
    """DoD #2 — run_checks detects sensitive info (patterns + entropy)."""

    def test_known_formats_detected(self):
        findings = _run(_diff("c.py", [
            'a = "AKIAIOSFODNN7EXAMPLE"',
            'b = "sk-1234567890abcdef1234567890abcdef"',
            'c = "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890"',
            'password = "hunter2"',
        ]))
        sen = [f for f in findings if f.category == "sensitive"]
        ids = " ".join(f.title for f in sen)
        self.assertIn("SEN001", ids)  # AWS
        self.assertIn("SEN002", ids)  # OpenAI
        self.assertIn("SEN003", ids)  # GitHub
        self.assertIn("SEN004", ids)  # password
        for f in sen:
            self.assertGreaterEqual(f.confidence, 0.85)

    def test_entropy_detected_for_unprefixed_token(self):
        # A high-entropy base64 string with no known prefix → SEN_ENT.
        findings = _run(_diff("c.py", [
            'key = "Zm9vYmFyYmF6cXV4NTEyMzRhYmNkZWZnaGlqa2xtbg=="',
        ]))
        ent = [f for f in findings if f.category == "sensitive" and "SEN_ENT" in f.title]
        self.assertEqual(len(ent), 1)
        self.assertEqual(ent[0].severity_hint, "critical")

    def test_known_format_suppresses_duplicate_entropy(self):
        # ghp_ token is high-entropy but already caught by SEN003 — no SEN_ENT.
        findings = _run(_diff("c.py", [
            'TOKEN = "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890"',
        ]))
        sen = [f for f in findings if f.category == "sensitive"]
        self.assertTrue(any("SEN003" in f.title for f in sen))
        self.assertFalse(any("SEN_ENT" in f.title for f in sen), sen)


class TestRunChecksAsync(unittest.TestCase):
    """DoD #2 — run_checks detects async errors."""

    def test_gather_without_await_detected(self):
        findings = _run(_diff("a.py", [
            '    task = asyncio.gather(coro1, coro2)',
        ]))
        asy = [f for f in findings if f.category == "async"]
        self.assertTrue(any("ASY001" in f.title for f in asy))

    def test_client_session_without_async_with_detected(self):
        findings = _run(_diff("a.py", [
            '    session = aiohttp.ClientSession()',
        ]))
        asy = [f for f in findings if f.category == "async"]
        self.assertTrue(any("ASY002" in f.title for f in asy))

    def test_awaited_call_not_flagged(self):
        findings = _run(_diff("a.py", [
            '    await asyncio.gather(coro1, coro2)',
        ]))
        asy = [f for f in findings if f.category == "async" and "ASY001" in f.title]
        self.assertEqual(asy, [])


class TestRunChecksResource(unittest.TestCase):
    """DoD #2 — run_checks detects resource leaks (with `with` filtering)."""

    def test_open_without_with_detected(self):
        findings = _run(_diff("r.py", [
            '    f = open("x.txt")',
        ]))
        res = [f for f in findings if f.category == "resource"]
        self.assertTrue(any("RES001" in f.title for f in res))

    def test_open_with_with_not_flagged(self):
        findings = _run(_diff("r.py", [
            '    with open("x.txt") as f:',
            '        pass',
        ]))
        res = [f for f in findings if f.category == "resource" and "RES001" in f.title]
        self.assertEqual(res, [])

    def test_connect_without_with_detected(self):
        findings = _run(_diff("r.py", [
            '    conn = pool.connect()',
        ]))
        res = [f for f in findings if f.category == "resource"]
        self.assertTrue(any("RES002" in f.title for f in res))


class TestRunChecksDb(unittest.TestCase):
    """DoD #2 — run_checks detects DB lifecycle issues."""

    def test_connect_without_with_detected(self):
        findings = _run(_diff("d.py", [
            '    conn = engine.connect()',
        ]))
        db = [f for f in findings if f.category == "db"]
        self.assertTrue(any("DB001" in f.title for f in db))

    def test_cursor_without_close_detected(self):
        findings = _run(_diff("d.py", [
            '    cur = conn.cursor()',
        ]))
        db = [f for f in findings if f.category == "db"]
        self.assertTrue(any("DB002" in f.title for f in db))

    def test_begin_transaction_detected(self):
        findings = _run(_diff("d.py", [
            '    txn = conn.begin()',
        ]))
        db = [f for f in findings if f.category == "db"]
        self.assertTrue(any("DB003" in f.title for f in db))


class TestRunChecksTests(unittest.TestCase):
    """DoD #2 — run_checks detects missing tests (cross-file)."""

    def test_new_public_function_without_test_detected(self):
        findings = _run(_diff("app.py", [
            'def public_fn(arg):',
            '    return arg',
        ]))
        tests = [f for f in findings if f.category == "tests"]
        self.assertTrue(any("public_fn" in f.title for f in tests))

    def test_new_function_with_test_not_flagged(self):
        findings = _run(_diff("app.py", [
            'def public_fn(arg):',
            '    return arg',
        ], extra_files=[("test_app.py", ['def test_public_fn():', '    assert public_fn(1) == 1'])]))
        tests = [f for f in findings if f.category == "tests" and "public_fn" in f.title]
        self.assertEqual(tests, [])

    def test_private_function_not_flagged(self):
        findings = _run(_diff("app.py", [
            'def _private():',
            '    pass',
        ]))
        tests = [f for f in findings if f.category == "tests"]
        self.assertEqual(tests, [])


class TestRawFindingFields(unittest.TestCase):
    """DoD #3 — RawFinding fields complete, confidence sensible."""

    def test_fields_complete(self):
        findings = _run(_diff("s.py", [
            '    cur = conn.execute("SELECT * FROM u WHERE id=" + x)',
        ]))
        self.assertTrue(findings)
        for f in findings:
            self.assertIsInstance(f.category, str)
            self.assertIsInstance(f.file, str)
            self.assertIsInstance(f.line, int)
            self.assertIsInstance(f.title, str)
            self.assertIsInstance(f.evidence, str)
            self.assertIsInstance(f.severity_hint, str)
            self.assertIsInstance(f.confidence, float)
            self.assertEqual(f.source, "rule")
            self.assertIn(f.category, ("security", "sensitive", "async", "resource", "db", "tests"))
            self.assertIn(f.severity_hint, ("critical", "high", "medium", "low"))
            self.assertGreater(f.confidence, 0.0)
            self.assertLessEqual(f.confidence, 1.0)

    def test_confidence_range_by_strength(self):
        # Exact known format → ≥0.9; heuristic AST/pattern → ≤0.75.
        known = _run(_diff("c.py", ['a = "AKIAIOSFODNN7EXAMPLE"']))
        known_conf = [f.confidence for f in known if "SEN001" in f.title]
        self.assertTrue(known_conf and known_conf[0] >= 0.9)

        heur = _run(_diff("a.py", ['    task = asyncio.gather(coro)']))
        heur_conf = [f.confidence for f in heur if "ASY001" in f.title]
        self.assertTrue(heur_conf and heur_conf[0] <= 0.75)

    def test_to_dict_roundtrip(self):
        f = RawFinding("security", "a.py", 1, "t", "e", "high", 0.9)
        d = f.to_dict()
        self.assertEqual(d["category"], "security")
        self.assertEqual(d["confidence"], 0.9)


class TestMaskSecrets(unittest.TestCase):
    """DoD #4 — mask_secrets redacts common formats with correct count."""

    def test_aws_key(self):
        m, n = mask_secrets("key AKIAIOSFODNN7EXAMPLE here")
        self.assertIn("***REDACTED***", m)
        self.assertEqual(n, 1)

    def test_openai_key(self):
        m, n = mask_secrets("sk-1234567890abcdef1234567890abcdef")
        self.assertEqual(n, 1)
        self.assertEqual(m, "***REDACTED***")

    def test_github_token(self):
        m, n = mask_secrets("ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890")
        self.assertEqual(n, 1)

    def test_password_assignment(self):
        m, n = mask_secrets('password = "hunter2"')
        self.assertEqual(n, 1)
        self.assertIn("***REDACTED***", m)

    def test_private_key(self):
        m, n = mask_secrets("-----BEGIN RSA PRIVATE KEY-----\nblah")
        self.assertEqual(n, 1)

    def test_connection_string(self):
        m, n = mask_secrets("postgres://user:secretpw@host/db")
        self.assertEqual(n, 1)

    def test_multiple_secrets_count(self):
        text = "AKIAIOSFODNN7EXAMPLE and sk-1234567890abcdef1234567890abcdef"
        m, n = mask_secrets(text)
        self.assertEqual(n, 2)
        self.assertEqual(m.count("***REDACTED***"), 2)

    def test_no_secrets(self):
        m, n = mask_secrets("just normal text without secrets")
        self.assertEqual(n, 0)
        self.assertEqual(m, "just normal text without secrets")

    def test_empty(self):
        self.assertEqual(mask_secrets(""), ("", 0))

    def test_entropy_redaction(self):
        # High-entropy base64 with no known prefix → redacted by entropy.
        m, n = mask_secrets("key=Zm9vYmFyYmF6cXV4NTEyMzRhYmNkZWZnaGlqa2xtbg==")
        self.assertGreaterEqual(n, 1)
        self.assertIn("***REDACTED***", m)


class TestCleanDiff(unittest.TestCase):
    """DoD #5 — a clean diff produces an empty finding list."""

    def test_clean_fixture_no_findings(self):
        findings = _run(agent.FIXTURES["clean"])
        self.assertEqual(findings, [])

    def test_trivial_clean_diff(self):
        findings = _run(_diff("ok.py", [
            '    return a + b',
            '',
        ]))
        self.assertEqual(findings, [])

    def test_empty_changeset(self):
        self.assertEqual(run_checks(parse_diff(""), _ruleset()), [])


class TestRunChecksUsesSkillDir(unittest.TestCase):
    """run_checks resolves rules via skill_load's skill_dir (integration)."""

    def test_run_via_skill_load_ruleset(self):
        rs = agent.skill_load(_SKILL_DIR)  # carries skill_dir + rules
        cs = parse_diff(_diff("s.py", [
            '    cur = conn.execute("SELECT * FROM u WHERE id=" + x)',
        ]))
        findings = run_checks(cs, rs)
        self.assertTrue(any(f.category == "security" for f in findings))


if __name__ == "__main__":
    unittest.main(verbosity=2)
