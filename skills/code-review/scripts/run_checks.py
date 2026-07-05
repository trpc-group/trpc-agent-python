#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Standalone sandbox entry point: run scanners over a target directory -> out/findings.json.

Self-contained by design — the skill must run inside a sandbox without importing the example
package. Output conforms to ../docs/OUTPUT_SCHEMA.md. In slice 2 the container sandbox invokes this;
the example's in-process path uses ``pipeline/scanners.py`` (same tools, same schema).
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


def _redact(text: str) -> str:
    if not text:
        return text or ""
    out = _SECRET_RE.sub(lambda m: f"{m.group(1)}={_MASK}", text)
    for pat in _STANDALONE:
        out = pat.sub(_MASK, out)
    return out


_NOISE_RULES = {"B101", "S101"}  # assert-used — noise, especially in tests

# Kept identical to pipeline/scanners.py so both paths emit the same findings (a parity test enforces
# this). The skill cannot import the example package, so the mapping is duplicated by necessity.
_BANDIT_CONF = {"HIGH": 0.9, "MEDIUM": 0.6, "LOW": 0.4}
_RUFF_MAP = [("ASYNC", "async_errors", "high"), ("SIM115", "resource_leak", "medium"), ("S", "security", "high"),
             ("B", "resource_leak", "medium")]
_REQUIRED_TOOLS = {"bandit": "security", "ruff": "async_errors/resource_leak", "detect-secrets": "secret_leakage"}


def _ruff_cat_sev(code: str) -> tuple[str, str]:
    for prefix, cat, sev in _RUFF_MAP:
        if code.startswith(prefix):
            return cat, sev
    return "code_quality", "low"


_IGNORE_DIRS = {".ruff_cache", "__pycache__", ".git", ".mypy_cache", ".pytest_cache", "node_modules"}


def _source_files(target: str):
    """Files under target excluding tool caches / hidden dirs (which scanners create and pollute scans)."""
    for p in Path(target).rglob("*"):
        if p.is_file() and not any(part in _IGNORE_DIRS or part.startswith(".") for part in p.parts):
            yield p


def _rel(path: str, root: str) -> str:
    """Normalize a scanner-reported path to be relative to root (kept in sync with scanners.py::_rel)."""
    if os.path.isabs(path):
        try:
            return os.path.normpath(os.path.relpath(os.path.realpath(path), os.path.realpath(root)))
        except ValueError:
            return os.path.normpath(path)
    return os.path.normpath(path)


def _run(cmd: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120, check=False)


def _finding(**kw) -> dict:
    kw["evidence"] = _redact(kw.get("evidence", ""))
    return kw


def collect(target: str) -> list[dict]:
    findings: list[dict] = []
    if shutil.which("bandit"):
        proc = _run(["bandit", "-r", ".", "-f", "json", "-q"], cwd=target)
        if proc.stdout.strip():
            for r in json.loads(proc.stdout).get("results", []):
                if r.get("test_id") in _NOISE_RULES:
                    continue
                sev = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}.get(r.get("issue_severity"), "low")
                findings.append(
                    _finding(severity=sev,
                             category="security",
                             file=_rel(r["filename"], target),
                             line=r.get("line_number"),
                             title=r.get("test_name", "security issue"),
                             evidence=(r.get("code") or r.get("issue_text", "")).strip(),
                             recommendation=r.get("issue_text", "Review this security finding."),
                             confidence=_BANDIT_CONF.get(r.get("issue_confidence", "MEDIUM"), 0.6),
                             source="static",
                             rule_id=f"bandit:{r.get('test_id', '')}"))
    if shutil.which("ruff"):
        proc = _run(
            ["ruff", "check", ".", "--no-cache", "--output-format", "json", "--select", "ASYNC,SIM115,B,S", "--quiet"],
            cwd=target)
        if proc.stdout.strip():
            for r in json.loads(proc.stdout):
                code = r.get("code") or ""
                if code in _NOISE_RULES:
                    continue
                cat, sev = _ruff_cat_sev(code)
                findings.append(
                    _finding(severity=sev,
                             category=cat,
                             file=_rel(r["filename"], target),
                             line=(r.get("location") or {}).get("row"),
                             title=code or "lint issue",
                             evidence=r.get("message", ""),
                             recommendation=r.get("message", "See ruff rule documentation."),
                             confidence=0.7,
                             source="static",
                             rule_id=f"ruff:{code}"))
    if shutil.which("detect-secrets"):
        files = [str(p.relative_to(target)) for p in _source_files(target)]
        if files:
            proc = _run(["detect-secrets", "scan", *files], cwd=target)
            if proc.stdout.strip():
                for f, hits in (json.loads(proc.stdout).get("results") or {}).items():
                    for h in hits:
                        findings.append(
                            _finding(severity="critical",
                                     category="secret_leakage",
                                     file=os.path.normpath(f),
                                     line=h.get("line_number"),
                                     title=f"Possible secret: {h.get('type')}",
                                     evidence="secret detected (value redacted)",
                                     recommendation="Remove secret from source; use env vars / a secret manager.",
                                     confidence=0.85,
                                     source="static",
                                     rule_id=f"detect-secrets:{h.get('type')}"))
    findings.extend(_db_lifecycle(target))
    for tool, covers in _REQUIRED_TOOLS.items():
        if not shutil.which(tool):
            findings.append(
                _finding(severity="low",
                         category="scanner_unavailable",
                         file="",
                         line=None,
                         title=f"{tool} unavailable — {covers} not checked",
                         evidence=f"scanner '{tool}' is not installed in this environment",
                         recommendation=f"Install {tool} so {covers} is actually scanned.",
                         confidence=0.3,
                         source="static",
                         rule_id=f"internal:missing:{tool}"))
    return findings


_DB_CONNECT = re.compile(r"\b([A-Za-z_]\w*)\s*=\s*[\w.]*\b(connect|cursor)\s*\(")


def _db_lifecycle(target: str) -> list[dict]:
    """DB connection/cursor opened without `with` and never closed (no semgrep needed)."""
    out: list[dict] = []
    for p in _source_files(target):
        if p.suffix != ".py":
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, text in enumerate(content.splitlines(), start=1):
            m = _DB_CONNECT.search(text)
            if not m or text.lstrip().startswith("with "):
                continue
            var = m.group(1)
            if re.search(rf"\b{re.escape(var)}\s*\.\s*close\s*\(", content):
                continue
            out.append(
                _finding(severity="medium",
                         category="db_lifecycle",
                         file=os.path.normpath(str(p.relative_to(target))),
                         line=i,
                         title="DB resource without lifecycle management",
                         evidence=text.strip(),
                         recommendation=f"Use a context manager or ensure `{var}.close()` in a finally block.",
                         confidence=0.7,
                         source="static",
                         rule_id="cr:db-lifecycle"))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--out", default="out/findings.json")
    args = ap.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"findings": collect(args.target)}, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
