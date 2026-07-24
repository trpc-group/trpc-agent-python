#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Rule engine — ChangeSet → raw findings (Phase 2, L4).

Loads the six rule documents (``rules/*.md`` YAML blocks) and matches them
against the **add lines** of a :class:`ChangeSet`, emitting a list of
:class:`RawFinding`. Output is deliberately un-deduped and un-bucketed —
that is Phase 4's job. This module only produces raw diagnostics with a
confidence hint so P4 can triage.

Design
------
* Only ``add`` lines are analysed — deleted code raises no new issues.
* Each rule carries ``id`` / ``pattern`` / ``severity_hint`` / ``confidence``
  / ``type`` (``pattern`` | ``ast`` | ``diff``).
* ``pattern`` rules: compiled regex matched per add line (fast, reliable).
* ``ast`` rules: pattern match first, plus a best-effort ``ast`` parse of the
  joined add lines for confirmation (skipped silently on syntax errors —
  diff fragments are often incomplete).
* ``diff`` rules (missing_tests): cross-file association analysis.
* ``sensitive`` adds Shannon-entropy detection for un-prefixed high-entropy
  tokens (shares logic with ``mask_secrets``).
* Confidence: exact/known-format 0.9+, heuristic 0.6-0.75 (P4 routes low
  confidence to ``warnings`` to control false positives).

Usage
-----
    from run_checks import run_checks, load_rules
    rules = load_rules(skill_dir, rule_paths)
    findings = run_checks(changeset, {"_rules": rules})

    # CLI (stdin JSON ChangeSet → stdout JSON findings):
    python run_checks.py --skill-dir skills/code-review < changeset.json
"""

from __future__ import annotations

import ast
import json
import re
import sys
import textwrap
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Callable

from mask_secrets import _TOKEN_RE
from mask_secrets import _shannon_entropy
from parse_diff import ChangeSet
from parse_diff import parse_diff

# --------------------------------------------------------------------------- #
# RawFinding
# --------------------------------------------------------------------------- #
@dataclass
class RawFinding:
    """One raw diagnostic, pre-dedup / pre-bucket.

    ``severity_hint`` and ``confidence`` are rule suggestions; the final
    ``severity`` and ``bucket`` are decided in Phase 4.
    """

    category: str  # security|async|resource|tests|sensitive|db
    file: str
    line: int
    title: str
    evidence: str
    severity_hint: str  # critical|high|medium|low
    confidence: float  # 0.0-1.0
    source: str = "rule"

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Rule document loading
# --------------------------------------------------------------------------- #
# rule file stem → finding category
_CATEGORY_MAP = {
    "security": "security",
    "async_errors": "async",
    "resource_leak": "resource",
    "missing_tests": "tests",
    "sensitive_info": "sensitive",
    "db_lifecycle": "db",
}

_YAML_BLOCK_RE = re.compile(r"```yaml\n(.*?)```", re.DOTALL)


def _category_from_path(rel_path: str) -> str:
    stem = Path(rel_path).stem
    return _CATEGORY_MAP.get(stem, stem)


def _extract_yaml_block(doc: str) -> str:
    """Return the first ```yaml ... ``` fenced block in a rule doc (or '')."""
    m = _YAML_BLOCK_RE.search(doc)
    return m.group(1) if m else ""


def _coerce_scalar(val: str):
    """Coerce a YAML scalar (single-quoted with '' escape, float, or raw)."""
    if len(val) >= 2 and val[0] == "'" and val[-1] == "'":
        return val[1:-1].replace("''", "'")
    if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
        return val[1:-1]
    try:
        return float(val)
    except ValueError:
        pass
    return val


def _parse_rules_yaml(text: str) -> list[dict]:
    """Parse a rule-doc YAML block (a list of rule dicts).

    Focused parser for the rule-document shape: a top-level list whose items
    are indented ``key: value`` pairs. Handles single-quoted strings with
    ``''`` escapes so regex patterns containing quotes parse correctly.
    """
    rules: list[dict] = []
    cur: dict | None = None
    item_indent: int | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        stripped = raw.lstrip(" ")
        indent = len(raw) - len(stripped)
        if stripped.startswith("- "):
            if cur is not None:
                rules.append(cur)
            cur = {}
            item_indent = indent
            cur.update(_parse_kv(stripped[2:].strip()))
        elif cur is not None and item_indent is not None and indent > item_indent:
            cur.update(_parse_kv(stripped))
    if cur is not None:
        rules.append(cur)
    return rules


def _parse_kv(s: str) -> dict:
    if ":" not in s:
        return {}
    key, _, val = s.partition(":")
    return {key.strip(): _coerce_scalar(val.strip())}


def load_rules(skill_dir: str | Path, rule_paths: list[str]) -> dict[str, list[dict]]:
    """Load all rule docs under ``skill_dir`` → {category: [rule_dict, ...]}.

    Each rule dict has: id, pattern, severity_hint, confidence, type,
    description.
    """
    skill_dir = Path(skill_dir)
    by_cat: dict[str, list[dict]] = {}
    for rel in rule_paths:
        cat = _category_from_path(rel)
        doc_path = skill_dir / rel
        if not doc_path.exists():
            continue
        doc = doc_path.read_text(encoding="utf-8")
        block = _extract_yaml_block(doc)
        rules = _parse_rules_yaml(block) if block else []
        by_cat.setdefault(cat, []).extend(rules)
    return by_cat


def _compile_rules(rules: list[dict]) -> list[tuple[dict, re.Pattern]]:
    """Compile each rule's ``pattern`` to a regex; skip invalid patterns."""
    out = []
    for r in rules:
        pat = r.get("pattern")
        if not pat:
            continue
        try:
            out.append((r, re.compile(pat)))
        except re.error:
            continue
    return out


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _add_lines(file) -> list[tuple[int, str]]:
    """Collect (new_line_no, content) for every add line in a ChangedFile."""
    out = []
    for hunk in file.hunks:
        for ln in hunk.lines:
            if ln.type == "add" and ln.new_line_no is not None:
                out.append((ln.new_line_no, ln.content))
    return out


def _pattern_findings(
    file_path: str,
    add_lines: list[tuple[int, str]],
    compiled: list[tuple[dict, re.Pattern]],
    category: str,
    *,
    line_filter: Callable[[str, str, dict], bool] | None = None,
) -> list[RawFinding]:
    """Match compiled rules against add lines → RawFindings.

    ``line_filter(content, match_text, rule)`` may return False to suppress a
    match (used to drop ``with open(...)`` from the open()-leak rule, etc.).
    """
    findings: list[RawFinding] = []
    for rule, pat in compiled:
        for line_no, content in add_lines:
            m = pat.search(content)
            if not m:
                continue
            if line_filter and not line_filter(content, m.group(0), rule):
                continue
            rid = rule.get("id", category)
            findings.append(
                RawFinding(
                    category=category,
                    file=file_path,
                    line=line_no,
                    title=f"{rid}: {rule.get('description', '')}".strip(": ").strip(),
                    evidence=content.strip(),
                    severity_hint=str(rule.get("severity_hint", "medium")),
                    confidence=float(rule.get("confidence", 0.6)),
                    source="rule",
                )
            )
    return findings


# --------------------------------------------------------------------------- #
# Checkers
# --------------------------------------------------------------------------- #
def _has_with_prefix(content: str) -> bool:
    """True if the add line opens a `with` block (resource is managed)."""
    s = content.lstrip()
    return s.startswith("with ") or s.startswith("with\t") or " with " in content


def _has_await(content: str) -> bool:
    s = content.lstrip()
    return s.startswith("await ") or "await " in content


def check_security(file_path, add_lines, rules):
    compiled = _compile_rules(rules)
    return _pattern_findings(file_path, add_lines, compiled, "security")


def check_sensitive(file_path, add_lines, rules):
    findings = _pattern_findings(file_path, add_lines, _compile_rules(rules), "sensitive")
    # Entropy detection for un-prefixed high-entropy tokens.
    for line_no, content in add_lines:
        # evidence of known-format hits on this line — used to suppress
        # duplicate entropy findings for tokens already covered.
        line_evidence = [
            f.evidence for f in findings
            if f.line == line_no and f.file == file_path
        ]
        for m in _TOKEN_RE.finditer(content):
            tok = m.group(0)
            if _shannon_entropy(tok) <= 4.5:
                continue
            # Skip if this token is already covered by a known-format finding.
            if any(tok in ev for ev in line_evidence):
                continue
            findings.append(
                RawFinding(
                    category="sensitive",
                    file=file_path,
                    line=line_no,
                    title="SEN_ENT: 高熵字符串疑似密钥 (entropy > 4.5)",
                    evidence=content.strip(),
                    severity_hint="critical",
                    confidence=0.8,
                    source="rule",
                )
            )
            break  # one entropy finding per line is enough
    return findings


def check_async(file_path, add_lines, rules):
    compiled = _compile_rules(rules)

    def _filter(content, _match, rule):
        # ASY001/ASY002: suppress if the call is already awaited.
        if rule.get("id") in ("ASY001", "ASY002") and _has_await(content):
            return False
        return True

    findings = _pattern_findings(file_path, add_lines, compiled, "async", line_filter=_filter)
    findings.extend(_ast_async(file_path, add_lines))
    return findings


def check_resource(file_path, add_lines, rules):
    compiled = _compile_rules(rules)

    def _filter(content, _match, rule):
        # RES001/RES002: suppress if the call sits inside a `with` line.
        if rule.get("id") in ("RES001", "RES002") and _has_with_prefix(content):
            return False
        return True

    findings = _pattern_findings(file_path, add_lines, compiled, "resource", line_filter=_filter)
    findings.extend(_ast_resource(file_path, add_lines))
    return findings


def check_db(file_path, add_lines, rules):
    compiled = _compile_rules(rules)

    def _filter(content, _match, rule):
        if rule.get("id") in ("DB001", "DB002") and _has_with_prefix(content):
            return False
        return True

    findings = _pattern_findings(file_path, add_lines, compiled, "db", line_filter=_filter)
    findings.extend(_ast_db(file_path, add_lines))
    return findings


def check_tests(changeset, rules):
    """Cross-file: new public def/class with no matching test_* reference."""
    findings: list[RawFinding] = []
    # Collect new public names and their locations.
    new_defs: list[tuple[str, int, str]] = []  # (name, line, file)
    new_classes: list[tuple[str, int, str]] = []
    test_refs: set[str] = set()
    has_test_file = False
    for f in changeset.files:
        is_test = bool(re.search(r"(^|/)(test_|_test\.py$|tests?/)", f.path))
        if is_test:
            has_test_file = True
        for line_no, content in _add_lines(f):
            for m in re.finditer(r"def\s+([a-zA-Z][a-zA-Z0-9_]*)\s*\(", content):
                name = m.group(1)
                if is_test:
                    test_refs.add(name)
                elif not name.startswith("_"):
                    new_defs.append((name, line_no, f.path))
            for m in re.finditer(r"class\s+([A-Z][a-zA-Z0-9_]*)\s*(?:\(|:)", content):
                name = m.group(1)
                if is_test:
                    test_refs.add(name)
                elif not name.startswith("_"):
                    new_classes.append((name, line_no, f.path))
            # record test_<fn> / <fn>( references inside test files
            if is_test:
                for m in re.finditer(r"\b(test_)?([a-zA-Z][a-zA-Z0-9_]*)\s*\(", content):
                    test_refs.add(m.group(2))

    rule = rules[0] if rules else {}
    rid = rule.get("id", "TST001")
    conf = float(rule.get("confidence", 0.6))
    for name, line_no, fpath in new_defs:
        covered = f"test_{name}" in test_refs or name in test_refs
        if not covered:
            findings.append(
                RawFinding(
                    category="tests",
                    file=fpath,
                    line=line_no,
                    title=f"{rid}: 新增公开函数 {name}() 无对应测试",
                    evidence=f"def {name}(",
                    severity_hint=str(rule.get("severity_hint", "low")),
                    confidence=conf,
                    source="rule",
                )
            )
    for name, line_no, fpath in new_classes:
        covered = f"Test{name}" in test_refs or name in test_refs
        if not covered:
            findings.append(
                RawFinding(
                    category="tests",
                    file=fpath,
                    line=line_no,
                    title=f"{rid}: 新增公开类 {name} 无对应测试",
                    evidence=f"class {name}",
                    severity_hint=str(rule.get("severity_hint", "low")),
                    confidence=conf,
                    source="rule",
                )
            )
    return findings


# --------------------------------------------------------------------------- #
# AST enhancements (best-effort)
# --------------------------------------------------------------------------- #
def _try_parse(add_lines):
    """Join + dedent add lines, attempt ast.parse. Return tree or None."""
    src = "\n".join(content for _, content in add_lines)
    src = textwrap.dedent(src)
    try:
        return ast.parse(src)
    except SyntaxError:
        return None


def _calls_not_in_with(tree, func_pred):
    """Yield (lineno, func_name) for Call nodes whose func matches and that
    are NOT inside a With block."""
    in_with = set()

    class _Walker(ast.NodeVisitor):
        def __init__(self):
            self.hits = []

        def visit_With(self, node):
            for child in ast.walk(node):
                if child is not node:
                    in_with.add(id(child))
            self.generic_visit(node)

        def visit_Call(self, node):
            name = _call_name(node)
            if name and func_pred(name) and id(node) not in in_with:
                # node.lineno is 1-based within the joined source; map back via
                # the order of add_lines. We keep lineno as-is for hinting.
                self.hits.append((node.lineno, name))
            self.generic_visit(node)

    w = _Walker()
    w.visit(tree)
    return w.hits


def _call_name(node) -> str | None:
    """Best-effort function name from a Call node (Name or Attribute)."""
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _ast_resource(file_path, add_lines):
    tree = _try_parse(add_lines)
    if tree is None:
        return []
    out = []
    for _lineno, name in _calls_not_in_with(tree, lambda n: n in ("open", "connect")):
        out.append(
            RawFinding(
                category="resource",
                file=file_path,
                line=add_lines[0][0] if add_lines else 0,
                title=f"RES-AST: {name}() 调用未在 with 中 (AST 确认)",
                evidence=f"{name}(",
                severity_hint="high",
                confidence=0.82,
                source="rule",
            )
        )
    return out


def _ast_db(file_path, add_lines):
    tree = _try_parse(add_lines)
    if tree is None:
        return []
    out = []
    for _lineno, name in _calls_not_in_with(tree, lambda n: n in ("connect", "cursor")):
        out.append(
            RawFinding(
                category="db",
                file=file_path,
                line=add_lines[0][0] if add_lines else 0,
                title=f"DB-AST: {name}() 调用未在 with 中 (AST 确认)",
                evidence=f"{name}(",
                severity_hint="high",
                confidence=0.82,
                source="rule",
            )
        )
    return out


def _ast_async(file_path, add_lines):
    """Detect async-def names that are called without await in the add lines."""
    tree = _try_parse(add_lines)
    if tree is None:
        return []
    # Collect async def names.
    async_names = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.AsyncFunctionDef,)) and not n.name.startswith("_")
    }
    if not async_names:
        return []
    awaited = set()

    class _Walker(ast.NodeVisitor):
        def visit_Await(self, node):
            for c in ast.walk(node.value):
                if isinstance(c, ast.Call):
                    nm = _call_name(c)
                    if nm:
                        awaited.add(nm)
            self.generic_visit(node)

        def visit_Call(self, node):
            nm = _call_name(node)
            # A bare call to an async name (not awaited) is suspicious.
            if nm in async_names and nm not in awaited:
                self.hits.append((node.lineno, nm))
            self.generic_visit(node)

    w = _Walker()
    w.hits = []
    w.visit(tree)
    out = []
    for _lineno, nm in w.hits:
        out.append(
            RawFinding(
                category="async",
                file=file_path,
                line=add_lines[0][0] if add_lines else 0,
                title=f"ASY-AST: async 函数 {nm}() 疑似未 await (AST 确认)",
                evidence=f"{nm}(",
                severity_hint="high",
                confidence=0.8,
                source="rule",
            )
        )
    return out


# --------------------------------------------------------------------------- #
# run_checks
# --------------------------------------------------------------------------- #
_CHECKERS_PATTERN: dict[str, Callable] = {
    "security": check_security,
    "sensitive": check_sensitive,
    "async": check_async,
    "resource": check_resource,
    "db": check_db,
}


def run_checks(changeset: ChangeSet, ruleset: dict) -> list[RawFinding]:
    """Match all rules against a ChangeSet's add lines → RawFindings.

    ``ruleset`` may be either:
    * the dict returned by ``skill_load`` (must contain ``skill_dir`` and
      ``rules``), or
    * a dict with a pre-loaded ``"_rules"`` key (handy for tests).
    """
    if "_rules" in ruleset:
        rules_by_cat = ruleset["_rules"]
    else:
        skill_dir = ruleset.get("skill_dir") or ruleset.get("_skill_dir")
        rule_paths = ruleset.get("rules", [])
        if not skill_dir or not rule_paths:
            return []
        rules_by_cat = load_rules(skill_dir, rule_paths)

    findings: list[RawFinding] = []
    for f in changeset.files:
        add_lines = _add_lines(f)
        if not add_lines:
            continue
        for cat, checker in _CHECKERS_PATTERN.items():
            findings.extend(checker(f.path, add_lines, rules_by_cat.get(cat, [])))

    # Cross-file tests check runs on the whole changeset.
    findings.extend(check_tests(changeset, rules_by_cat.get("tests", [])))
    return findings


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _build_arg_parser():
    import argparse

    p = argparse.ArgumentParser(description="Run code-review rules on a ChangeSet.")
    p.add_argument("--skill-dir", default=None, help="code-review skill directory (optional if rules come via stdin)")
    p.add_argument(
        "--diff-file",
        help="optional: parse a diff file instead of reading ChangeSet JSON from stdin",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if args.diff_file:
        cs = parse_diff(Path(args.diff_file).read_text(encoding="utf-8"))
        skill_dir = Path(args.skill_dir) if args.skill_dir else None
        rule_paths = sorted(
            p.relative_to(skill_dir).as_posix()
            for p in (skill_dir / "rules").glob("*.md")
        ) if skill_dir else []
        rules_by_cat = load_rules(skill_dir, rule_paths) if skill_dir else {}
    else:
        data = json.loads(sys.stdin.read())
        # Two stdin shapes:
        #   {changeset: {...}, rules: {...}}  — rules pre-loaded (sandbox mode,
        #     no rules/*.md files needed in the workspace)
        #   {files: [...]}                     — bare ChangeSet (load rules from --skill-dir)
        if isinstance(data, dict) and "changeset" in data and "rules" in data:
            cs = _changeset_from_dict(data["changeset"])
            rules_by_cat = data["rules"]
        else:
            cs = _changeset_from_dict(data)
            skill_dir = Path(args.skill_dir) if args.skill_dir else None
            rule_paths = sorted(
                p.relative_to(skill_dir).as_posix()
                for p in (skill_dir / "rules").glob("*.md")
            ) if skill_dir else []
            rules_by_cat = load_rules(skill_dir, rule_paths) if skill_dir else {}

    findings = run_checks(cs, {"_rules": rules_by_cat})
    print(json.dumps([f.to_dict() for f in findings], ensure_ascii=False, indent=2))
    return 0


def _changeset_from_dict(data: dict) -> ChangeSet:
    """Reconstruct a ChangeSet from its JSON dict (for CLI stdin)."""
    cs = ChangeSet()
    for fd in data.get("files", []):
        from parse_diff import ChangedFile, Hunk, DiffLine

        cf = ChangedFile(path=fd["path"], status=fd["status"])
        for hd in fd.get("hunks", []):
            h = Hunk(
                old_start=hd["old_start"],
                new_start=hd["new_start"],
                old_count=hd["old_count"],
                new_count=hd["new_count"],
            )
            for ld in hd.get("lines", []):
                h.lines.append(DiffLine(**ld))
            cf.hunks.append(h)
        cs.files.append(cf)
    return cs


if __name__ == "__main__":
    raise SystemExit(main())
