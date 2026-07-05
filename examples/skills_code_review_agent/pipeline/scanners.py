# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run established OSS scanners and normalize their output into the ``Finding`` schema.

Design thesis (see the plan): findings come from *deterministic* scanners, not the LLM, so the
hidden-set thresholds are reproducible. Each adapter shells out to a tool, parses its native JSON,
and yields ``Finding`` objects conforming to ``pipeline.types``. Adapters skip cleanly when their
tool isn't installed, and a crashing scanner never sinks the whole review (see ``scan``).

MVP: adapters run in-process against a materialized checkout. Slice 2 moves the identical
invocation inside the container sandbox with no change here.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Callable

from .types import DiffSummary, Finding, Severity


def _changed_lines(diff: DiffSummary) -> dict[str, set[int]]:
    """Per-file set of new-file line numbers the diff touched (findings elsewhere are dropped)."""
    out: dict[str, set[int]] = {}
    for f in diff.files:
        lines: set[int] = set()
        for h in f.hunks:
            lines.update(h.candidate_lines)
        out[f.path] = lines
    return out


def _run(cmd: list[str], cwd: str, timeout: float = 90.0) -> subprocess.CompletedProcess:
    """Run a scanner. Never raises on non-zero exit (scanners exit non-zero when they find issues)."""
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)


def _rel(path: str, root: str) -> str:
    """Normalize a scanner-reported path to match diff paths.

    Scanners report either an absolute path or a path relative to ``root`` (their cwd, e.g.
    ``./insecure.py``). Resolve absolutes against ``root``; normalize relatives in place — do NOT
    use relpath on a relative path, which would resolve it against the process cwd.
    """
    if os.path.isabs(path):
        try:
            # realpath both sides so a symlinked root (e.g. macOS /var -> /private/var) still matches.
            return os.path.normpath(os.path.relpath(os.path.realpath(path), os.path.realpath(root)))
        except ValueError:
            return os.path.normpath(path)
    return os.path.normpath(path)


def _in_diff(file: str, line: int | None, changed: dict[str, set[int]]) -> bool:
    """Keep a finding only if it lands on a line the diff actually changed."""
    if file not in changed:
        return False
    touched = changed[file]
    if not touched or line is None:
        return True  # file-level finding, or file has no line info
    return line in touched


# assert-used (bandit B101 / ruff S101) is noise, especially in test files — suppress it.
_NOISE_RULES = {"B101", "S101"}

# bandit issue_severity -> our Severity. (Tunable — bandit also exposes issue_confidence.)
_BANDIT_SEV: dict[str, Severity] = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}
_BANDIT_CONF = {"HIGH": 0.9, "MEDIUM": 0.6, "LOW": 0.4}


def normalize_bandit(repo_dir: str, changed: dict[str, set[int]]) -> list[Finding]:
    if not shutil.which("bandit"):
        return []
    proc = _run(["bandit", "-r", ".", "-f", "json", "-q"], cwd=repo_dir)
    if not proc.stdout.strip():
        return []
    data = json.loads(proc.stdout)
    findings: list[Finding] = []
    for r in data.get("results", []):
        file = _rel(r["filename"], repo_dir)
        line = r.get("line_number")
        if not _in_diff(file, line, changed) or r.get("test_id") in _NOISE_RULES:
            continue
        findings.append(
            Finding(
                severity=_BANDIT_SEV.get(r.get("issue_severity", "LOW"), "low"),
                category="security",
                file=file,
                line=line,
                title=r.get("test_name", "security issue"),
                evidence=(r.get("code") or r.get("issue_text", "")).strip(),
                recommendation=r.get("issue_text", "Review this security finding."),
                confidence=_BANDIT_CONF.get(r.get("issue_confidence", "MEDIUM"), 0.6),
                source="static",
                rule_id=f"bandit:{r.get('test_id', '')}",
            ))
    return findings


# ruff rule prefix -> (category, severity). Covers async-error and resource-leak requirements.
_RUFF_CATEGORY = {
    "ASYNC": ("async_errors", "high"),
    "SIM115": ("resource_leak", "medium"),
    "S": ("security", "high"),  # flake8-bandit subset
    "B": ("resource_leak", "medium"),  # flake8-bugbear
}


def _ruff_map(code: str) -> tuple[str, Severity]:
    for prefix, (cat, sev) in _RUFF_CATEGORY.items():
        if code.startswith(prefix):
            return cat, sev  # type: ignore[return-value]
    return "code_quality", "low"


def normalize_ruff(repo_dir: str, changed: dict[str, set[int]]) -> list[Finding]:
    if not shutil.which("ruff"):
        return []
    proc = _run(["ruff", "check", ".", "--output-format", "json", "--select", "ASYNC,SIM115,B,S", "--quiet"],
                cwd=repo_dir)
    if not proc.stdout.strip():
        return []
    findings: list[Finding] = []
    for r in json.loads(proc.stdout):
        file = _rel(r["filename"], repo_dir)
        line = (r.get("location") or {}).get("row")
        code = r.get("code") or ""
        if not _in_diff(file, line, changed) or code in _NOISE_RULES:
            continue
        cat, sev = _ruff_map(code)
        findings.append(
            Finding(
                severity=sev,
                category=cat,
                file=file,
                line=line,
                title=code or "lint issue",
                evidence=r.get("message", ""),
                recommendation=r.get("message", "See ruff rule documentation."),
                confidence=0.7,
                source="static",
                rule_id=f"ruff:{code}",
            ))
    return findings


def normalize_detect_secrets(repo_dir: str, changed: dict[str, set[int]]) -> list[Finding]:
    if not shutil.which("detect-secrets"):
        return []
    # `detect-secrets scan .` enumerates via git and finds nothing outside a git repo — pass the
    # changed files explicitly (also correct: we only review what the diff touched).
    targets = [f for f in changed if f and os.path.isfile(os.path.join(repo_dir, f))]
    if not targets:
        return []
    proc = _run(["detect-secrets", "scan", *targets], cwd=repo_dir)
    if not proc.stdout.strip():
        return []
    data = json.loads(proc.stdout)
    findings: list[Finding] = []
    for raw_file, hits in (data.get("results") or {}).items():
        file = _rel(raw_file, repo_dir)
        for h in hits:
            line = h.get("line_number")
            if not _in_diff(file, line, changed):
                continue
            findings.append(
                Finding(
                    severity="critical",
                    category="secret_leakage",
                    file=file,
                    line=line,
                    title=f"Possible secret: {h.get('type', 'secret')}",
                    evidence=f"{h.get('type', 'secret')} detected (value redacted)",
                    recommendation="Remove the secret from source; use env vars or a secret manager.",
                    confidence=0.85,
                    source="static",
                    rule_id=f"detect-secrets:{h.get('type', '')}",
                ))
    return findings


def normalize_semgrep(repo_dir: str, changed: dict[str, set[int]]) -> list[Finding]:
    """Optional: skips cleanly if semgrep isn't installed. Covers custom DB-lifecycle rules."""
    if not shutil.which("semgrep"):
        return []
    rules = os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, os.pardir, "skills", "code-review", "rules")
    cmd = ["semgrep", "--json", "--quiet", "--config", rules if os.path.isdir(rules) else "auto", "."]
    proc = _run(cmd, cwd=repo_dir, timeout=120.0)
    if not proc.stdout.strip():
        return []
    findings: list[Finding] = []
    for r in json.loads(proc.stdout).get("results", []):
        file = _rel(r.get("path", ""), repo_dir)
        line = (r.get("start") or {}).get("line")
        if not _in_diff(file, line, changed):
            continue
        extra = r.get("extra", {})
        sev = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}.get(extra.get("severity", "WARNING"), "medium")
        findings.append(
            Finding(
                severity=sev,
                category="db_lifecycle",
                file=file,
                line=line,
                title=r.get("check_id", "semgrep finding").split(".")[-1],
                evidence=(extra.get("lines") or "").strip(),
                recommendation=extra.get("message", "See rule."),
                confidence=0.75,
                source="static",
                rule_id=f"semgrep:{r.get('check_id', '')}",
            ))
    return findings


# A DB connection/cursor bound to a variable — leak-prone if not context-managed or closed.
_DB_CONNECT = re.compile(r"\b([A-Za-z_]\w*)\s*=\s*[\w.]*\b(connect|cursor)\s*\(")


def normalize_db_lifecycle(repo_dir: str, changed: dict[str, set[int]]) -> list[Finding]:
    """Heuristic (no semgrep needed): a DB connection/cursor opened without `with` and never closed."""
    findings: list[Finding] = []
    for file, lines in changed.items():
        path = os.path.join(repo_dir, file)
        if not (file.endswith(".py") and os.path.isfile(path)):
            continue
        try:
            content = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for i, text in enumerate(content.splitlines(), start=1):
            if lines and i not in lines:
                continue
            m = _DB_CONNECT.search(text)
            if not m or text.lstrip().startswith("with "):
                continue
            var = m.group(1)
            if re.search(rf"\b{re.escape(var)}\s*\.\s*close\s*\(", content):
                continue
            findings.append(
                Finding(
                    severity="medium",
                    category="db_lifecycle",
                    file=file,
                    line=i,
                    title="DB resource without lifecycle management",
                    evidence=text.strip(),
                    recommendation=f"Use a context manager (`with ...`) or ensure `{var}.close()` in a finally block.",
                    confidence=0.7,
                    source="static",
                    rule_id="cr:db-lifecycle"))
    return findings


# Scanners the review relies on; a missing one must be surfaced, not silently treated as "clean".
_REQUIRED_TOOLS = {"bandit": "security", "ruff": "async-error/resource-leak", "detect-secrets": "secret_leakage"}


def detect_unavailable_scanners() -> list[Finding]:
    """One needs-human-review finding per missing required scanner (confidence lands it there)."""
    out: list[Finding] = []
    for tool, covers in _REQUIRED_TOOLS.items():
        if not shutil.which(tool):
            out.append(
                Finding(severity="low",
                        category="scanner_unavailable",
                        file="",
                        line=None,
                        title=f"{tool} unavailable — {covers} not checked",
                        evidence=f"scanner '{tool}' is not installed in this environment",
                        recommendation=f"Install {tool} so {covers} is actually scanned.",
                        confidence=0.3,
                        source="static",
                        rule_id=f"internal:missing:{tool}"))
    return out


def tool_calls_available() -> int:
    """How many scanner tools actually ran this review (uniform across in-process and sandbox paths)."""
    return sum(1 for t in _REQUIRED_TOOLS if shutil.which(t)) + 1  # + the db_lifecycle heuristic


def _is_test_path(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    return (base.startswith("test_") or base.endswith("_test.py") or path.startswith("tests/") or "/tests/" in path)


def detect_missing_tests(diff: DiffSummary) -> list[Finding]:
    """Diff-level heuristic: source files changed with no corresponding test change."""
    src = [
        f for f in diff.files
        if f.path.endswith(".py") and not _is_test_path(f.path) and f.change_type in ("added", "modified")
    ]
    tests = [f for f in diff.files if _is_test_path(f.path)]
    if src and not tests:
        return [
            Finding(severity="low",
                    category="missing_tests",
                    file=src[0].path,
                    line=None,
                    title="Source changed without accompanying tests",
                    evidence=f"{len(src)} source file(s) changed; no test file changed",
                    recommendation="Add or update tests covering the changed code.",
                    confidence=0.6,
                    source="rule",
                    rule_id="cr:missing-tests")
        ]
    return []


Adapter = Callable[[str, dict[str, set[int]]], list[Finding]]

# Enabled adapters cover all 6 required categories: security, secret_leakage, async_errors,
# resource_leak, db_lifecycle (+ semgrep rules when present). missing_tests is diff-level (added by scan()).
ADAPTERS: list[Adapter] = [
    normalize_bandit,
    normalize_ruff,
    normalize_detect_secrets,
    normalize_db_lifecycle,
    normalize_semgrep,
]


def scan(repo_dir: str, diff: DiffSummary) -> list[Finding]:
    """Run every enabled adapter over the changed files; a crashing scanner is recorded, not fatal."""
    changed = _changed_lines(diff)
    findings: list[Finding] = list(detect_unavailable_scanners())
    for adapter in ADAPTERS:
        try:
            findings.extend(adapter(repo_dir, changed))
        except Exception as exc:  # noqa: BLE001 - one scanner must never sink the whole review
            findings.append(_scanner_error_finding(adapter.__name__, exc))
    return findings


def _scanner_error_finding(adapter_name: str, exc: Exception) -> Finding:
    return Finding(
        severity="low",
        category="scanner_error",
        file="",
        line=None,
        title=f"{adapter_name} failed to run",
        evidence=f"{type(exc).__name__}: {exc}",
        recommendation="Check scanner installation / input.",
        confidence=1.0,
        source="static",
        status="needs_human_review",
        rule_id=f"internal:{adapter_name}",
    )
