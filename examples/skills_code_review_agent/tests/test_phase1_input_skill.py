# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Phase 1 (Input & Skill loading) acceptance tests.

Covers every line of the Phase-1 Definition of Done:
  1. parse_diff parses a standard unified diff (files / hunks / line numbers).
  2. add lines carry correct new_line_no.
  3. skill_load reads SKILL.md frontmatter → rules + scripts + sandbox_config.
  4. ChangeSet persists to the input_diff table.
  5. All three input modes work: --diff-file / --repo-path / fixture.

Run:
    python examples/skills_code_review_agent/tests/test_phase1_input_skill.py
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_EXAMPLE_ROOT))

# parse_diff lives under the skill scripts dir; agent.py wires it onto
# sys.path on import, but we add it directly so tests can import it too.
_SCRIPTS_DIR = _EXAMPLE_ROOT / "skills" / "code-review" / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import agent  # noqa: E402
from agent.db import SQLiteStore  # noqa: E402
from parse_diff import ChangeSet  # noqa: E402
from parse_diff import parse_diff  # noqa: E402

_SKILL_DIR = _EXAMPLE_ROOT / "skills" / "code-review"

# A canonical diff with known line numbers, used to assert new_line_no.
_SAMPLE_DIFF = """\
diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -10,3 +10,4 @@
 context line
-old line
+new line
+added line
diff --git a/bar.py b/bar.py
--- /dev/null
+++ b/bar.py
@@ -0,0 +1,2 @@
+def hello():
+    return "hi"
"""

# diff with binary + rename + mode-change meta lines to test tolerance.
_META_DIFF = """\
diff --git a/binary.dat b/binary.dat
index 1234567..89abcde 100644
Binary files a/binary.dat and b/binary.dat differ
diff --git a/old.py b/new.py
similarity index 90%
rename from old.py
rename to new.py
diff --git a/mode.sh b/mode.sh
old mode 100644
new mode 100755
diff --git a/real.py b/real.py
--- a/real.py
+++ b/real.py
@@ -1,2 +1,2 @@
-x
+y
 z
"""


def _git_available() -> bool:
    return shutil.which("git") is not None


class TestParseDiff(unittest.TestCase):
    """DoD #1 — parse_diff extracts files / hunks / line numbers."""

    def test_parses_two_files(self):
        cs = parse_diff(_SAMPLE_DIFF)
        self.assertIsInstance(cs, ChangeSet)
        self.assertEqual(cs.file_count, 2)
        paths = [f.path for f in cs.files]
        self.assertEqual(paths, ["foo.py", "bar.py"])

    def test_status_inference(self):
        cs = parse_diff(_SAMPLE_DIFF)
        self.assertEqual(cs.files[0].status, "modified")  # --- a/ +++ b/
        self.assertEqual(cs.files[1].status, "added")     # --- /dev/null

    def test_hunk_header_fields(self):
        cs = parse_diff(_SAMPLE_DIFF)
        h = cs.files[0].hunks[0]
        self.assertEqual((h.old_start, h.old_count), (10, 3))
        self.assertEqual((h.new_start, h.new_count), (10, 4))

    def test_line_types(self):
        cs = parse_diff(_SAMPLE_DIFF)
        h = cs.files[0].hunks[0]
        types = [ln.type for ln in h.lines]
        self.assertEqual(types, ["ctx", "del", "add", "add"])

    def test_content_stripped_of_sigil(self):
        cs = parse_diff(_SAMPLE_DIFF)
        h = cs.files[0].hunks[0]
        self.assertEqual(h.lines[0].content, "context line")
        self.assertEqual(h.lines[1].content, "old line")
        self.assertEqual(h.lines[2].content, "new line")

    def test_hunk_without_counts(self):
        """@@ -1 +1 @@ (no comma counts) must parse with count=1."""
        diff = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"
        cs = parse_diff(diff)
        h = cs.files[0].hunks[0]
        self.assertEqual(h.old_count, 1)
        self.assertEqual(h.new_count, 1)

    def test_empty_diff_returns_empty_changeset(self):
        self.assertEqual(parse_diff("").file_count, 0)
        self.assertEqual(parse_diff("   \n  ").file_count, 0)

    def test_meta_lines_tolerated(self):
        """Binary / rename / mode-change meta must be skipped, not crash."""
        cs = parse_diff(_META_DIFF)
        # Only real.py has an actual hunk; the others are meta-only.
        paths = [f.path for f in cs.files]
        self.assertIn("real.py", paths)
        self.assertEqual(len(cs.files), 1)
        self.assertEqual(cs.files[0].hunks[0].lines[0].content, "x")

    def test_no_newline_marker_skipped(self):
        diff = (
            "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n\\ No newline at end of file\n"
            "+new\n\\ No newline at end of file\n"
        )
        cs = parse_diff(diff)
        h = cs.files[0].hunks[0]
        # Two real lines (del + add); the two \ markers are dropped.
        self.assertEqual(len(h.lines), 2)
        self.assertEqual([ln.type for ln in h.lines], ["del", "add"])

    def test_changed_file_helpers(self):
        cs = parse_diff(_SAMPLE_DIFF)
        f = cs.files[0]
        self.assertEqual(f.hunk_count, 1)
        self.assertEqual(f.added_lines, 2)
        self.assertEqual(f.deleted_lines, 1)
        self.assertEqual(f.line_count, 4)  # ctx+del+add+add


class TestParseDiffLineNumbers(unittest.TestCase):
    """DoD #2 — add lines carry correct new_line_no."""

    def test_new_line_no_increments_on_add_and_ctx(self):
        cs = parse_diff(_SAMPLE_DIFF)
        h = cs.files[0].hunks[0]  # @@ -10,3 +10,4 @@
        # ctx -> 10, del -> None, add -> 11, add -> 12
        self.assertEqual(h.lines[0].new_line_no, 10)   # ctx
        self.assertIsNone(h.lines[1].new_line_no)      # del
        self.assertEqual(h.lines[2].new_line_no, 11)   # add
        self.assertEqual(h.lines[3].new_line_no, 12)   # add

    def test_added_file_starts_at_new_start(self):
        cs = parse_diff(_SAMPLE_DIFF)
        h = cs.files[1].hunks[0]  # @@ -0,0 +1,2 @@
        self.assertEqual(h.new_start, 1)
        self.assertEqual(h.lines[0].new_line_no, 1)  # first add line
        self.assertEqual(h.lines[1].new_line_no, 2)

    def test_del_lines_carry_none(self):
        diff = "--- a/x\n+++ b/x\n@@ -5,2 +5,1 @@\n ctx\n-removed\n-also\n"
        cs = parse_diff(diff)
        h = cs.files[0].hunks[0]
        self.assertIsNone(h.lines[1].new_line_no)
        self.assertIsNone(h.lines[2].new_line_no)
        # ctx line keeps its number; del lines don't advance it.
        self.assertEqual(h.lines[0].new_line_no, 5)

    def test_multiple_hunks_line_numbers_independent(self):
        diff = (
            "--- a/x\n+++ b/x\n"
            "@@ -10,2 +10,2 @@\n c\n-a\n+b\n"
            "@@ -50,2 +50,2 @@\n d\n-e\n+f\n"
        )
        cs = parse_diff(diff)
        hunks = cs.files[0].hunks
        self.assertEqual(len(hunks), 2)
        # hunk0: ctx(10), del(None), add(11)  →  +b at line 11
        self.assertEqual(hunks[0].lines[0].new_line_no, 10)   # ctx c
        self.assertIsNone(hunks[0].lines[1].new_line_no)      # del a
        self.assertEqual(hunks[0].lines[2].new_line_no, 11)   # add b
        # hunk1: ctx(50), del(None), add(51)  →  +f at line 51
        self.assertEqual(hunks[1].lines[0].new_line_no, 50)   # ctx d
        self.assertIsNone(hunks[1].lines[1].new_line_no)      # del e
        self.assertEqual(hunks[1].lines[2].new_line_no, 51)   # add f


class TestSkillLoad(unittest.TestCase):
    """DoD #3 — skill_load reads frontmatter → rules + scripts + sandbox_config."""

    def test_returns_name_and_lists(self):
        skill = agent.skill_load(_SKILL_DIR)
        self.assertEqual(skill["name"], "code-review")
        self.assertIsInstance(skill["rules"], list)
        self.assertIsInstance(skill["scripts"], list)
        self.assertGreater(len(skill["rules"]), 0)
        self.assertGreater(len(skill["scripts"]), 0)

    def test_rules_are_relative_paths_to_md(self):
        skill = agent.skill_load(_SKILL_DIR)
        for r in skill["rules"]:
            self.assertTrue(r.startswith("rules/"))
            self.assertTrue(r.endswith(".md"))
        # The six canonical rule files are present.
        expected = {
            "rules/security.md",
            "rules/async_errors.md",
            "rules/resource_leak.md",
            "rules/missing_tests.md",
            "rules/sensitive_info.md",
            "rules/db_lifecycle.md",
        }
        self.assertEqual(set(skill["rules"]), expected)

    def test_scripts_are_relative_py_paths(self):
        skill = agent.skill_load(_SKILL_DIR)
        self.assertIn("scripts/parse_diff.py", skill["scripts"])
        for s in skill["scripts"]:
            self.assertTrue(s.startswith("scripts/"))
            self.assertTrue(s.endswith(".py"))

    def test_sandbox_config_fields_and_types(self):
        cfg = agent.skill_load(_SKILL_DIR)["sandbox_config"]
        self.assertEqual(cfg["default_runtime"], "container")
        self.assertEqual(cfg["fallback"], "local")
        self.assertEqual(cfg["timeout_s"], 30)
        self.assertIsInstance(cfg["timeout_s"], int)
        self.assertEqual(cfg["max_output_bytes"], 1048576)
        self.assertIsInstance(cfg["max_output_bytes"], int)
        self.assertEqual(cfg["env_whitelist"], ["PATH", "HOME", "LANG"])
        self.assertIsInstance(cfg["env_whitelist"], list)

    def test_skill_load_missing_skill_md_raises(self):
        with self.assertRaises(FileNotFoundError):
            agent.skill_load(_EXAMPLE_ROOT)  # no SKILL.md here


class TestChangeSetPersist(unittest.IsolatedAsyncioTestCase):
    """DoD #4 — ChangeSet persists to the input_diff table (async store)."""

    async def test_persist_writes_one_row_per_file(self):
        store = SQLiteStore(":memory:")
        try:
            tid = await store.create_task("diff", "sample.diff", "dry-run")
            cs = parse_diff(_SAMPLE_DIFF)
            written = await agent.persist_changeset(store, tid, cs)
            self.assertEqual(written, 2)

            diffs = (await store.get_task(tid))["input_diffs"]
            self.assertEqual(len(diffs), 2)
            by_path = {d["file_path"]: d for d in diffs}

            foo = by_path["foo.py"]
            self.assertEqual(foo["hunk_count"], 1)
            self.assertEqual(foo["line_count"], 4)  # ctx+del+add+add
            self.assertIn("2+", foo["summary"])  # 2 add lines
            self.assertIn("1-", foo["summary"])  # 1 del line
            self.assertRegex(foo["sha256"], r"^[0-9a-f]{64}$")

            bar = by_path["bar.py"]
            self.assertEqual(bar["hunk_count"], 1)
            self.assertEqual(bar["line_count"], 2)  # 2 add lines
            self.assertRegex(bar["sha256"], r"^[0-9a-f]{64}$")

            # sha256 differs per file.
            self.assertNotEqual(foo["sha256"], bar["sha256"])
        finally:
            await store.close()

    async def test_persist_empty_changeset_writes_nothing(self):
        store = SQLiteStore(":memory:")
        try:
            tid = await store.create_task("diff", "empty.diff", "dry-run")
            written = await agent.persist_changeset(store, tid, parse_diff(""))
            self.assertEqual(written, 0)
            self.assertEqual(len((await store.get_task(tid))["input_diffs"]), 0)
        finally:
            await store.close()

    async def test_persist_rolls_into_get_task_join(self):
        """Phase-0 get_task must surface the Phase-1 input_diff rows."""
        store = SQLiteStore(":memory:")
        try:
            tid = await store.create_task("fixture", "security", "dry-run")
            cs = parse_diff(agent.FIXTURES["security"])
            await agent.persist_changeset(store, tid, cs)
            rec = await store.get_task(tid)
            self.assertEqual(rec["task"]["id"], tid)
            self.assertEqual(len(rec["input_diffs"]), 2)
        finally:
            await store.close()


class TestInputModes(unittest.TestCase):
    """DoD #5 — --diff-file / --repo-path / fixture all work."""

    def test_diff_file_mode(self):
        fd, path = tempfile.mkstemp(suffix=".diff")
        os.close(fd)
        try:
            Path(path).write_text(_SAMPLE_DIFF, encoding="utf-8")
            text, itype, ref = agent.load_diff(diff_file=path)
            self.assertEqual(itype, "diff")
            self.assertEqual(ref, path)
            self.assertEqual(parse_diff(text).file_count, 2)
        finally:
            os.unlink(path)

    def test_fixture_mode_security(self):
        text, itype, ref = agent.load_diff(fixture="security")
        self.assertEqual(itype, "fixture")
        self.assertEqual(ref, "security")
        cs = parse_diff(text)
        self.assertEqual(cs.file_count, 2)
        paths = [f.path for f in cs.files]
        self.assertIn("app/db.py", paths)
        self.assertIn("app/secret.py", paths)

    def test_fixture_mode_clean(self):
        text, itype, ref = agent.load_diff(fixture="clean")
        self.assertEqual(itype, "fixture")
        self.assertEqual(parse_diff(text).file_count, 1)

    def test_fixture_unknown_raises(self):
        with self.assertRaises(KeyError):
            agent.load_diff(fixture="nope")

    def test_no_input_raises(self):
        with self.assertRaises(ValueError):
            agent.load_diff()

    @unittest.skipUnless(_git_available(), "git not installed")
    def test_repo_path_mode_git_diff_head(self):
        """Create a temp git repo, commit, modify, and read `git diff HEAD`."""
        repo = tempfile.mkdtemp(prefix="cr_p1_repo_")
        try:
            env = dict(os.environ)
            # Minimal git identity so commit doesn't fail.
            subprocess.run(
                ["git", "init", "-q"], cwd=repo, check=True,
                env=env,
            )
            subprocess.run(
                ["git", "config", "user.email", "t@t"], cwd=repo, check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "t"], cwd=repo, check=True,
            )
            # Initial commit.
            Path(repo, "a.py").write_text("a = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "init"], cwd=repo, check=True,
                env=env,
            )
            # Modify a.py (unstaged) → git diff HEAD should see it.
            Path(repo, "a.py").write_text("a = 2\nb = 3\n", encoding="utf-8")

            text, itype, ref = agent.load_diff(repo_path=repo)
            self.assertEqual(itype, "repo")
            self.assertEqual(ref, repo)
            cs = parse_diff(text)
            self.assertEqual(cs.file_count, 1)
            self.assertEqual(cs.files[0].path, "a.py")
            # Modified: added b=3, changed a=1→a=2.
            self.assertEqual(cs.files[0].status, "modified")
        finally:
            shutil.rmtree(repo, ignore_errors=True)


class TestCLIIntegration(unittest.TestCase):
    """End-to-end: `agent.py --fixture ...` runs the full Phase-1 chain."""

    def test_cli_fixture_security(self):
        db_path = tempfile.mktemp(suffix=".db", prefix="cr_p1_cli_")
        out_dir = tempfile.mkdtemp(prefix="cr_p1_out_")
        try:
            rc = agent.main(
                ["--fixture", "security", "--db-path", db_path, "--output-dir", out_dir]
            )
            self.assertEqual(rc, 0)
            # Verify persistence directly. P5 full pipeline → status "done".
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            task = conn.execute("SELECT * FROM review_task").fetchone()
            self.assertEqual(task["status"], "done")
            self.assertEqual(task["input_type"], "fixture")
            self.assertEqual(task["input_ref"], "security")
            n = conn.execute("SELECT COUNT(*) FROM input_diff").fetchone()[0]
            conn.close()
            self.assertEqual(n, 2)
        finally:
            if os.path.exists(db_path):
                try:
                    os.unlink(db_path)
                except PermissionError:
                    pass
            shutil.rmtree(out_dir, ignore_errors=True)

    def test_cli_diff_file_mode(self):
        fd, diff_path = tempfile.mkstemp(suffix=".diff")
        os.close(fd)
        db_path = tempfile.mktemp(suffix=".db")
        out_dir = tempfile.mkdtemp(prefix="cr_p1_out_")
        try:
            Path(diff_path).write_text(_SAMPLE_DIFF, encoding="utf-8")
            rc = agent.main(
                ["--diff-file", diff_path, "--db-path", db_path, "--output-dir", out_dir]
            )
            self.assertEqual(rc, 0)
            conn = sqlite3.connect(db_path)
            n = conn.execute("SELECT COUNT(*) FROM input_diff").fetchone()[0]
            conn.close()
            self.assertEqual(n, 2)
        finally:
            if os.path.exists(diff_path):
                os.unlink(diff_path)
            if os.path.exists(db_path):
                try:
                    os.unlink(db_path)
                except PermissionError:
                    pass
            shutil.rmtree(out_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
