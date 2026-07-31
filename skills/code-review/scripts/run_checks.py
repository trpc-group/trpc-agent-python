#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Sandbox entry point for the code-review skill: scan a target directory -> out/findings.json.

This is the **only** implementation of the review rules. The agent reaches it through the
framework's Skills mechanism (``skill_load`` then ``skill_run``); the deterministic CLI reaches the
same script through the development sandbox. Nothing re-implements these checks elsewhere.

Self-contained by design — the skill runs inside a sandbox and must not import the example package.
Output conforms to ``../docs/OUTPUT_SCHEMA.md``.

When a unified diff is available (``--diff``, or a ``.changes.diff`` sitting in the target), findings
are restricted to the lines the diff actually touched and the diff-level missing-tests rule runs.
Without one, every file under the target is reviewed in full.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

_MASK = "***REDACTED***"
_SECRET_RE = re.compile(
    r"""(?ix)\b(password|passwd|secret|api[_-]?key|token|auth|client[_-]?secret)\b\s*[:=]\s*['"]?([^\s'"]{4,})""")
_STANDALONE = [re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"), re.compile(r"\bghp_[A-Za-z0-9]{36}\b")]

#: A diff dropped next to the changed files; hidden so it is never itself scanned.
DIFF_SIDECAR = ".changes.diff"


def _redact(text: str) -> str:
    if not text:
        return text or ""
    out = _SECRET_RE.sub(lambda m: f"{m.group(1)}={_MASK}", text)
    for pat in _STANDALONE:
        out = pat.sub(_MASK, out)
    return out


_NOISE_RULES = {"B101", "S101"}  # assert-used — noise, especially in tests

_BANDIT_CONF = {"HIGH": 0.9, "MEDIUM": 0.6, "LOW": 0.4}
_RUFF_MAP = [("ASYNC", "async_errors", "high"), ("SIM115", "resource_leak", "medium"), ("S", "security", "high"),
             ("B", "resource_leak", "medium")]
_REQUIRED_TOOLS = {"bandit": "security", "ruff": "async_errors/resource_leak", "detect-secrets": "secret_leakage"}

_IGNORE_DIRS = {".ruff_cache", "__pycache__", ".git", ".mypy_cache", ".pytest_cache", "node_modules"}


def _ruff_cat_sev(code: str) -> tuple[str, str]:
    for prefix, cat, sev in _RUFF_MAP:
        if code.startswith(prefix):
            return cat, sev
    return "code_quality", "low"


def _source_files(target: str):
    """Files under target, excluding tool caches, hidden paths, and the diff sidecar.

    The exclusion test runs on the path *relative to target* — testing the absolute path would make
    every file invisible whenever the workspace itself lives under a dot-directory (temp dirs often
    do), silently turning a real scan into a clean bill of health.

    The sidecar is excluded explicitly: it carries the diff verbatim, secrets included, so scanning
    it would be a self-inflicted leak.
    """
    root = Path(target)
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).parts
        if any(part in _IGNORE_DIRS or part.startswith(".") for part in rel):
            continue
        yield p


def _rel(path: str, root: str) -> str:
    """Normalize a scanner-reported path to be relative to root."""
    if os.path.isabs(path):
        try:
            return os.path.normpath(os.path.relpath(os.path.realpath(path), os.path.realpath(root)))
        except ValueError:
            return os.path.normpath(path)
    return os.path.normpath(path)


def _run(cmd: list[str], cwd: str, timeout: float = 120.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)


def _finding(**kw) -> dict:
    kw["evidence"] = _redact(kw.get("evidence", ""))
    return kw


# --------------------------------------------------------------------------------------------
# diff awareness
# --------------------------------------------------------------------------------------------

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def parse_diff(diff_text: str) -> tuple[dict[str, set[int]], list[str]]:
    """Return per-file new-file line numbers the diff added/changed, and the changed file list.

    A deliberately small unified-diff reader: the sandbox has no access to the example's parser.
    """
    changed: dict[str, set[int]] = {}
    current: str | None = None
    new_line = 0
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            current = None if path == "/dev/null" else os.path.normpath(path)
            if current:
                changed.setdefault(current, set())
            continue
        if line.startswith("@@"):
            m = _HUNK_RE.match(line)
            if m:
                new_line = int(m.group(1))
            continue
        if current is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            changed[current].add(new_line)
            new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            continue
        elif line.startswith(" ") or not line:
            new_line += 1
    return changed, list(changed)


def _in_diff(file: str, line: int | None, changed: dict[str, set[int]]) -> bool:
    """True when the finding sits on a line the diff touched (or when we have no diff at all)."""
    if not changed:
        return True
    lines = changed.get(os.path.normpath(file))
    if lines is None:
        return False
    return not lines or line is None or line in lines


def _is_test_path(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    return base.startswith("test_") or base.endswith("_test.py") or path.startswith("tests/") or "/tests/" in path


def detect_missing_tests(files: list[str]) -> list[dict]:
    """Diff-level rule: source files changed with no corresponding test change."""
    src = [f for f in files if f.endswith(".py") and not _is_test_path(f)]
    tests = [f for f in files if _is_test_path(f)]
    if not src or tests:
        return []
    return [
        _finding(severity="low",
                 category="missing_tests",
                 file=src[0],
                 line=None,
                 title="Source changed without accompanying tests",
                 evidence=f"{len(src)} source file(s) changed; no test file changed",
                 recommendation="Add or update tests covering the changed code.",
                 confidence=0.6,
                 source="rule",
                 rule_id="cr:missing-tests")
    ]


# --------------------------------------------------------------------------------------------
# scanners
# --------------------------------------------------------------------------------------------


def scan_bandit(target: str, changed: dict[str, set[int]]) -> list[dict]:
    if not shutil.which("bandit"):
        return []
    proc = _run(["bandit", "-r", ".", "-f", "json", "-q"], cwd=target)
    if not proc.stdout.strip():
        return []
    out: list[dict] = []
    for r in json.loads(proc.stdout).get("results", []):
        if r.get("test_id") in _NOISE_RULES:
            continue
        file, line = _rel(r["filename"], target), r.get("line_number")
        if not _in_diff(file, line, changed):
            continue
        sev = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}.get(r.get("issue_severity"), "low")
        out.append(
            _finding(severity=sev,
                     category="security",
                     file=file,
                     line=line,
                     title=r.get("test_name", "security issue"),
                     evidence=(r.get("code") or r.get("issue_text", "")).strip(),
                     recommendation=r.get("issue_text", "Review this security finding."),
                     confidence=_BANDIT_CONF.get(r.get("issue_confidence", "MEDIUM"), 0.6),
                     source="static",
                     rule_id=f"bandit:{r.get('test_id', '')}"))
    return out


def scan_ruff(target: str, changed: dict[str, set[int]]) -> list[dict]:
    if not shutil.which("ruff"):
        return []
    proc = _run(
        ["ruff", "check", ".", "--no-cache", "--output-format", "json", "--select", "ASYNC,SIM115,B,S", "--quiet"],
        cwd=target)
    if not proc.stdout.strip():
        return []
    out: list[dict] = []
    for r in json.loads(proc.stdout):
        code = r.get("code") or ""
        if code in _NOISE_RULES:
            continue
        file, line = _rel(r["filename"], target), (r.get("location") or {}).get("row")
        if not _in_diff(file, line, changed):
            continue
        cat, sev = _ruff_cat_sev(code)
        out.append(
            _finding(severity=sev,
                     category=cat,
                     file=file,
                     line=line,
                     title=code or "lint issue",
                     evidence=r.get("message", ""),
                     recommendation=r.get("message", "See ruff rule documentation."),
                     confidence=0.7,
                     source="static",
                     rule_id=f"ruff:{code}"))
    return out


def scan_secrets(target: str, changed: dict[str, set[int]]) -> list[dict]:
    if not shutil.which("detect-secrets"):
        return []
    # `detect-secrets scan .` enumerates via git and finds nothing outside a repo — pass files explicitly.
    files = [str(p.relative_to(target)) for p in _source_files(target)]
    if not files:
        return []
    proc = _run(["detect-secrets", "scan", *files], cwd=target)
    if not proc.stdout.strip():
        return []
    out: list[dict] = []
    for raw_file, hits in (json.loads(proc.stdout).get("results") or {}).items():
        file = os.path.normpath(raw_file)
        for h in hits:
            line = h.get("line_number")
            if not _in_diff(file, line, changed):
                continue
            out.append(
                _finding(severity="critical",
                         category="secret_leakage",
                         file=file,
                         line=line,
                         title=f"Possible secret: {h.get('type')}",
                         evidence="secret detected (value redacted)",
                         recommendation="Remove secret from source; use env vars / a secret manager.",
                         confidence=0.85,
                         source="static",
                         rule_id=f"detect-secrets:{h.get('type')}"))
    return out


def scan_semgrep(target: str, changed: dict[str, set[int]]) -> list[dict]:
    """Custom DB-lifecycle rules; skips cleanly when semgrep isn't installed."""
    if not shutil.which("semgrep"):
        return []
    rules = str(Path(__file__).resolve().parent.parent / "rules")
    if not os.path.isdir(rules):
        return []  # no `--config auto`: it pulls arbitrary remote rules we never validated
    # --disable-version-check avoids a ~9s network round-trip per invocation (and an outbound call
    # from a sandbox that is supposed to be offline); --metrics=off stops the telemetry upload.
    cmd = ["semgrep", "--json", "--quiet", "--disable-version-check", "--metrics=off", "--config", rules, "."]
    proc = _run(cmd, cwd=target)
    if not proc.stdout.strip():
        return []
    out: list[dict] = []
    for r in json.loads(proc.stdout).get("results", []):
        file, line = _rel(r.get("path", ""), target), (r.get("start") or {}).get("line")
        if not _in_diff(file, line, changed):
            continue
        extra = r.get("extra", {})
        sev = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}.get(extra.get("severity", "WARNING"), "medium")
        check_id = r.get("check_id", "")
        # Category comes from the rule that fired, not a blanket assumption. Our own rules/ tree is
        # db_lifecycle today, but a hardcoded label would silently mislabel anything added later.
        category = "db_lifecycle" if "db_lifecycle" in check_id else "security"
        out.append(
            _finding(severity=sev,
                     category=category,
                     file=file,
                     line=line,
                     title=r.get("check_id", "semgrep finding").split(".")[-1],
                     evidence=(extra.get("lines") or "").strip(),
                     recommendation=extra.get("message", "See rule."),
                     confidence=0.75,
                     source="static",
                     rule_id=f"semgrep:{check_id}"))
    return out


_DB_CONNECT = re.compile(r"\b([A-Za-z_]\w*)\s*=\s*[\w.]*\b(connect|cursor)\s*\(")


def scan_db_lifecycle(target: str, changed: dict[str, set[int]]) -> list[dict]:
    """Heuristic (no semgrep needed): a DB connection/cursor opened without `with` and never closed."""
    out: list[dict] = []
    for p in _source_files(target):
        if p.suffix != ".py":
            continue
        file = os.path.normpath(str(p.relative_to(target)))
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, text in enumerate(content.splitlines(), start=1):
            if not _in_diff(file, i, changed):
                continue
            m = _DB_CONNECT.search(text)
            if not m or text.lstrip().startswith("with "):
                continue
            var = m.group(1)
            if re.search(rf"\b{re.escape(var)}\s*\.\s*close\s*\(", content):
                continue
            out.append(
                _finding(severity="medium",
                         category="db_lifecycle",
                         file=file,
                         line=i,
                         title="DB resource without lifecycle management",
                         evidence=text.strip(),
                         recommendation=f"Use a context manager or ensure `{var}.close()` in a finally block.",
                         confidence=0.7,
                         source="static",
                         rule_id="cr:db-lifecycle"))
    return out


def unavailable_scanners() -> list[dict]:
    """A missing scanner must be surfaced, never silently treated as 'clean'."""
    return [
        _finding(severity="low",
                 category="scanner_unavailable",
                 file="",
                 line=None,
                 title=f"{tool} unavailable — {covers} not checked",
                 evidence=f"scanner '{tool}' is not installed in this environment",
                 recommendation=f"Install {tool} so {covers} is actually scanned.",
                 confidence=0.3,
                 source="static",
                 rule_id=f"internal:missing:{tool}") for tool, covers in _REQUIRED_TOOLS.items()
        if not shutil.which(tool)
    ]


def _scanner_error(name: str, exc: Exception) -> dict:
    return _finding(severity="low",
                    category="scanner_error",
                    file="",
                    line=None,
                    title=f"{name} failed to run",
                    evidence=f"{type(exc).__name__}: {exc}",
                    recommendation="Check scanner installation / input.",
                    confidence=1.0,
                    source="static",
                    status="needs_human_review",
                    rule_id=f"internal:{name}")


#: Covers all six required categories: security, secret_leakage, async_errors, resource_leak,
#: db_lifecycle (+ semgrep rules when present). missing_tests is diff-level, added by ``collect``.
SCANNERS = [scan_bandit, scan_ruff, scan_secrets, scan_db_lifecycle, scan_semgrep]

#: Name reported in the envelope's ``tools`` map for each scanner, and the binary it needs.
_SCANNER_TOOLS = {
    "scan_bandit": "bandit",
    "scan_ruff": "ruff",
    "scan_secrets": "detect-secrets",
    "scan_semgrep": "semgrep",
    "scan_db_lifecycle": None,  # pure-python heuristic, always available
}
_TOOL_LABEL = {"scan_db_lifecycle": "cr-db-lifecycle"}


def collect(target: str, diff_text: str = "") -> tuple[list[dict], dict[str, bool]]:
    """Run every scanner over the target; a crashing scanner is recorded, never fatal.

    Returns the findings and a map of which scanners actually executed. That map is measured *here*,
    inside the sandbox, which is the only place it can be measured honestly — a host-side PATH sweep
    describes the host, not the environment the review ran in.
    """
    changed, files = parse_diff(diff_text) if diff_text else ({}, [])
    findings: list[dict] = list(unavailable_scanners())
    tools: dict[str, bool] = {}
    for scanner in SCANNERS:
        binary = _SCANNER_TOOLS.get(scanner.__name__)
        label = _TOOL_LABEL.get(scanner.__name__, binary or scanner.__name__)
        available = binary is None or bool(shutil.which(binary))
        tools[label] = available
        if not available:
            continue
        try:
            findings.extend(scanner(target, changed))
        except Exception as exc:  # noqa: BLE001 - one scanner must never sink the whole review
            findings.append(_scanner_error(scanner.__name__, exc))
            tools[label] = False
    if files:
        findings.extend(detect_missing_tests(files))
    return findings, tools


def resolve_root(target: str) -> tuple[str, str]:
    """Locate the real scan root and the diff that anchors it.

    The workspace runtimes stage inputs at different depths — the local runtime nests them under a
    directory named after the source, the container runtime does not — so ``--target`` alone cannot
    tell us which directory the diff's relative paths are relative to. The sidecar can: wherever
    ``.changes.diff`` sits, its parent is the root. Findings' file paths then match the diff's paths
    under every layout.

    Falls back to ``target`` itself when no sidecar was staged (the file-list / worktree modes).
    """
    hits = sorted(Path(target).rglob(DIFF_SIDECAR), key=lambda p: len(p.parts))
    if hits:
        return str(hits[0].parent), str(hits[0])
    return target, ""


def _read_diff(target: str, diff_arg: str) -> tuple[str, str]:
    """Return (root, diff_text). An explicit --diff wins over the staged sidecar."""
    root, sidecar = resolve_root(target)
    path = diff_arg if (diff_arg and os.path.isfile(diff_arg)) else sidecar
    text = Path(path).read_text(encoding="utf-8", errors="replace") if path else ""
    return root, text


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the code-review scanners over a target directory.")
    ap.add_argument("--target", required=True, help="directory holding the changed files")
    ap.add_argument("--diff", default="", help=f"unified diff (default: {DIFF_SIDECAR} staged under --target)")
    ap.add_argument("--out", default="out/findings.json")
    args = ap.parse_args()

    root, diff_text = _read_diff(args.target, args.diff)
    findings, tools = collect(root, diff_text)
    _changed, diff_files = parse_diff(diff_text) if diff_text else ({}, [])

    envelope = {
        "schema_version": 1,
        "root": root,
        "tools": tools,
        "tool_calls": sum(1 for ran in tools.values() if ran),
        "diff_files": diff_files,
        "findings": findings,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    print(f"wrote {out} ({len(findings)} finding(s), {envelope['tool_calls']} scanner(s) ran)")


if __name__ == "__main__":
    main()
