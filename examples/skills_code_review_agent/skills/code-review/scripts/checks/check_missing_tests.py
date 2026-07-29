# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""missing_tests: flag source changes that ship without any test changes.

This is a *structural* check (path classification + changed-definition
detection), not a data-flow analysis, and it is the most false-positive prone
of the six categories.  The design bias is therefore: when in doubt, stay
silent.

Rules
-----
TEST001  Substantial source change (a ``def``/``class`` header line inside the
         candidate lines of a non-test python file) while the changeset
         contains no test file at all.
         * repo mode, and no repo test file matches the source stem either
           -> severity=medium, precision=high, confidence=medium
         * repo mode, but a matching repo test file exists (just untouched)
           -> NOT reported: existing tests cover the module, whether new
           cases are needed is a human call
         * diff-only mode (or unknown mode, treated conservatively the same)
           -> severity=info, precision=low, confidence=low; the decision
           table routes this tier into the warnings bucket
TEST002  A test file is deleted by the change
         -> severity=medium, precision=high, confidence=high.

Test file detection
-------------------
A path is a test file when any non-final path segment is ``test``/``tests``,
or the basename matches ``test_*.py`` / ``*_test.py`` / ``conftest.py``.

Patterns deliberately NOT reported (false-positive guards)
----------------------------------------------------------
* doc/config-only changes (.md/.txt/.rst/.yaml/.json/.toml/.cfg/.ini, any
  non-python language): skipped because TEST001 requires ``language==python``;
* renames without content changes: no candidate lines -> no changed defs;
* edits that touch no ``def``/``class`` header line (a bugfix inside an
  existing body arguably deserves a test too, but flagging every edited line
  would drown reviewers -> only definition-level changes count);
* ``__init__.py`` (and ``__main__.py``/``setup.py``): usually re-export or
  packaging glue; import-only ``__init__.py`` edits carry no def lines anyway
  and stem matching would be meaningless for def-carrying ones;
* files under demo-like directory trees (examples/, docs/, benchmarks/, ...);
* any test activity in the changeset (added/modified/renamed/deleted test
  file, old or new path) silences TEST001 entirely -- a deleted test is
  already reported by TEST002, double-reporting it as TEST001 is noise;
* modified test files whose ``def test_`` count shrank: net-count analysis
  over partial hunks is unreliable, so only *deleted* test files raise
  TEST002; deleted non-python assets under tests/ (fixtures, data) stay
  silent as well;
* repo mode when any known repo test file basename contains the source stem
  (``calculator.py`` -> ``test_calculator.py``/``calculator_test.py``/...):
  the substring match deliberately errs toward suppression.

Diff-only robustness: for ``content_complete=False`` files ``parse_ast`` may
fail on the gap-reconstructed text; the check then falls back to a per-changed-
line ``def``/``class`` regex (precision stays low) and never raises.
"""

from __future__ import annotations

import ast
import posixpath
import re

from checks.common import FileCtx, MODE_REPO, make_finding

CATEGORY = "missing_tests"

#: directory segments that mark a test tree (spec: /tests/ or /test/ only)
_TEST_DIR_SEGMENTS = frozenset({"test", "tests"})
#: basenames (lowercased) that mark a test file
_TEST_BASENAME_RE = re.compile(r"^(?:test_[^/]*|[^/]*_test|conftest)\.py$")
#: regex fallback for definition header lines when the AST is unavailable
_DEF_LINE_RE = re.compile(r"^\s*(?:async\s+def|def|class)\s+[A-Za-z_]")
_DEF_NAME_RE = re.compile(r"(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")
#: stems that never trigger TEST001 (packaging / re-export glue)
_SKIP_STEMS = frozenset({"__init__", "__main__", "setup"})
#: directory segments whose files are demo/doc code, not unit-test targets
_DEMO_DIR_SEGMENTS = frozenset({
    "example",
    "examples",
    "sample",
    "samples",
    "demo",
    "demos",
    "doc",
    "docs",
    "benchmark",
    "benchmarks",
    "migrations",
})


def _segments(path: str) -> list[str]:
    """Path split into non-empty, forward-slash segments."""
    return [seg for seg in path.replace("\\", "/").split("/") if seg]


def _is_test_file(path: str) -> bool:
    """True when the path is a test file per the detection rule above."""
    segs = _segments(path.lower())
    if not segs:
        return False
    if any(seg in _TEST_DIR_SEGMENTS for seg in segs[:-1]):
        return True
    return bool(_TEST_BASENAME_RE.match(segs[-1]))


def _stem(path: str) -> str:
    """Basename without the last extension: ``src/calculator.py`` -> ``calculator``."""
    base = posixpath.basename(path.replace("\\", "/"))
    return base.rsplit(".", 1)[0] if "." in base else base


def _changed_definitions(ctx: FileCtx) -> tuple[list[tuple[int, str, str]], str]:
    """Definitions whose header line is a changed line.

    Returns ``(hits, engine)`` where hits is ``[(line, kind, name), ...]``
    sorted by line and engine is ``"ast"`` or ``"regex"``.  AST is the primary
    engine; when parsing fails (diff-only gap reconstruction, syntax error, or
    missing content) the fallback scans only the changed lines with a
    ``def``/``class`` regex.  Never raises.
    """
    if not ctx.candidate_lines:
        return [], "ast"
    tree, _err = ctx.parse_ast()
    if tree is not None:
        hits = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.lineno in ctx.candidate_lines:
                    kind = "class" if isinstance(node, ast.ClassDef) else "def"
                    hits.append((node.lineno, kind, node.name))
        return sorted(hits), "ast"
    hits = []
    for line in sorted(ctx.candidate_lines):
        text = ctx.line_text(line)
        if not _DEF_LINE_RE.match(text):
            continue
        name_match = _DEF_NAME_RE.search(text)
        kind = "class" if text.lstrip().startswith("class") else "def"
        hits.append((line, kind, name_match.group(1) if name_match else "<unknown>"))
    return hits, "regex"


def _matching_repo_test(stem: str, repo_test_files: list) -> str:
    """First repo test file whose basename contains the source stem, else ""."""
    want = stem.lower()
    if not want:
        return ""
    for test_path in repo_test_files:
        base = posixpath.basename(str(test_path).replace("\\", "/")).lower()
        if base.endswith(".py"):
            base = base[:-3]
        if want in base:
            return str(test_path)
    return ""


def _test_skeleton(stem: str, hits: list[tuple[int, str, str]]) -> str:
    """Suggested pytest skeleton naming the actually changed definitions."""
    out = [f"# tests/test_{stem}.py"]
    for _line, kind, name in hits[:3]:
        safe = re.sub(r"[^A-Za-z0-9_]", "_", name).lower() or "changed_code"
        out.append(f"def test_{safe}():")
        out.append(f"    ...  # TODO: cover {kind} '{name}'")
    return "\n".join(out)


def _in_demo_tree(path: str) -> bool:
    """True when any parent directory segment marks demo/doc code."""
    return any(seg.lower() in _DEMO_DIR_SEGMENTS for seg in _segments(path)[:-1])


def run(files: list[FileCtx], mode: str, context: dict) -> list[dict]:
    """Entry point, see the module docstring for the rule set."""
    findings: list[dict] = []
    files = files or []
    repo_ctx = (context or {}).get("repo_context") or {}
    repo_test_files = list(repo_ctx.get("test_files") or [])

    # Any test activity (old or new path) counts: added, modified, renamed or
    # deleted test files all mean the author looked at the test suite.
    changeset_has_tests = any(
        _is_test_file(ctx.path or "") or (ctx.old_path and _is_test_file(ctx.old_path)) for ctx in files)

    # ---- TEST002: deleted test files ------------------------------------
    # Deleted files have no candidate lines (only "-" hunk lines), so this is
    # the one documented exception to "report only changed lines": the file
    # header itself is the evidence, reported at line 1.
    for ctx in files:
        path = ctx.path or ""
        if ctx.change_type != "deleted" or not _is_test_file(path):
            continue
        if not path.lower().endswith(".py"):
            continue  # deleted data/fixture assets under tests/ stay silent
        findings.append(
            make_finding(
                rule_id="TEST002",
                category=CATEGORY,
                severity="medium",
                file=path,
                line=1,
                title="Test file deleted",
                evidence=f"{path} is removed by this change (change_type=deleted)",
                recommendation="Confirm the covered behaviour is really gone or that its cases moved to "
                "another test file; otherwise restore the deleted tests.",
                confidence="high",
                precision="high",
            ))

    # ---- TEST001: source changed, no test changed -----------------------
    if changeset_has_tests:
        return findings

    for ctx in files:
        path = ctx.path or ""
        if ctx.language != "python" or ctx.change_type in ("deleted", "binary"):
            continue  # docs/config/non-python and removals never trigger
        if _is_test_file(path):
            continue  # defensive; unreachable while the gate above holds
        if _in_demo_tree(path) or _stem(path).lower() in _SKIP_STEMS:
            continue
        hits, engine = _changed_definitions(ctx)
        if not hits:
            # Renames without content edits and import/comment-only changes
            # (e.g. an __init__.py re-export tweak) land here: no def lines.
            continue
        stem = _stem(path)
        if mode == MODE_REPO:
            match = _matching_repo_test(stem, repo_test_files)
            if match:
                continue  # module already has tests in the repo, human call
            severity = "medium"
            precision = "high" if engine == "ast" else "low"
            confidence = "medium" if engine == "ast" else "low"
            extra = (f"; no repo test file name contains stem '{stem}' "
                     f"({len(repo_test_files)} known test file(s))")
        else:
            severity, precision, confidence = "info", "low", "low"
            extra = "; diff-only mode cannot see repository tests"
        first_line = hits[0][0]
        listing = ", ".join(f"{kind} {name} (line {line})" for line, kind, name in hits[:5])
        if len(hits) > 5:
            listing += f", +{len(hits) - 5} more"
        findings.append(
            make_finding(
                rule_id="TEST001",
                category=CATEGORY,
                severity=severity,
                file=path,
                line=first_line,
                title="Source change ships without test changes",
                evidence=f"{len(hits)} new/changed definition(s), no test file in the changeset: "
                f"{listing}{extra}",
                recommendation=f"Add or update tests (e.g. tests/test_{stem}.py) covering the changed "
                "definitions before merging.",
                confidence=confidence,
                precision=precision,
                fix_snippet={
                    "before": ctx.line_text(first_line).strip() or f"<line {first_line}>",
                    "after": _test_skeleton(stem, hits),
                },
            ))
    return findings
