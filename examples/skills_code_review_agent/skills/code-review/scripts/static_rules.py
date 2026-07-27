#!/usr/bin/env python3
"""Run lightweight static review rules over a unified diff."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HUNK_RE = re.compile(r"@@ -\d+(?:,\d+)? \+(?P<new>\d+)(?:,\d+)? @@")
# Kept deliberately in step with agent/redaction.py KEY_VALUE_RE: the keyword is
# matched inside an identifier (DATABASE_PASSWORD, SENDGRID_API_KEY), the key may
# be quoted as in JSON, and code references are excluded by CODE_REFERENCE_RE.
_KEYWORD = (
    r"api[_-]?key|access[_-]?key|secret[_-]?key|storage[_-]?key|signing[_-]?key|private[_-]?key|"
    r"access[_-]?token|auth[_-]?token|refresh[_-]?token|id[_-]?token|bearer[_-]?token|"
    r"client[_-]?secret|credential|passphrase|password|passwd|pwd|secret|token"
)
SECRET_RE = re.compile(
    r"(?i)(?P<key>[A-Za-z0-9_.\-]*(?:" + _KEYWORD + r")[A-Za-z0-9_.\-]*)"
    r"(?P<sep>[\"']?\s*[:=]\s*)"
    r"(?P<quote>[\"']?)"
    r"(?P<value>[^\"'()\[\]{}\s,;#]{6,})"
    r"(?P=quote)"
    r"(?=$|[\s,;#\]}])")
CODE_REFERENCE_RE = re.compile(r"^([a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+|os\.(environ|getenv)\b.*)$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PLACEHOLDER_RE = re.compile(
    r"(?i)^(?:<[^>]*>|\{\{?[^}]*\}?\}|x{4,}|\*{4,}|\.{3,}"
    r"|changeme|placeholder|example|sample|dummy|todo|tbd|fake|notreal)$"
    r"|^[A-Z_]*(?:REPLACE|CHANGE|YOUR|PLACEHOLDER|EXAMPLE|SAMPLE|DUMMY|TODO|TBD|HERE|ME)[A-Z_]*$")


def is_reference(value: str, quote: str, line: str) -> bool:
    """Return whether a captured value is code or a documented placeholder."""
    if "<REDACTED>" in value or CODE_REFERENCE_RE.match(value) or PLACEHOLDER_RE.match(value):
        return True
    if quote or not IDENTIFIER_RE.match(value):
        return False
    return "(" in line or "{" in line
PROVIDER_RE = re.compile(
    r"\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9_]{20,}\b|\bxox[baprs]-[A-Za-z0-9-]{16,}\b|"
    r"\bAIza[0-9A-Za-z_-]{33,40}|\bsk-[A-Za-z0-9_-]{16,}\b|"
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")


def normalize(path: str) -> str:
    path = path.strip()
    if path in {"/dev/null", "dev/null"}:
        return ""
    if path.startswith(("a/", "b/")):
        path = path[2:]
    return path


def has_secret(text: str) -> bool:
    """Return whether the line carries a live credential."""
    if PROVIDER_RE.search(text):
        return True
    match = SECRET_RE.search(text)
    return bool(match and not is_reference(match.group("value"), match.group("quote") or "", text))


def redact(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        quote = match.group("quote") or ""
        if is_reference(match.group("value"), quote, text):
            return match.group(0)
        return match.group("key") + match.group("sep") + quote + "<REDACTED>" + quote

    return PROVIDER_RE.sub("<REDACTED>", SECRET_RE.sub(repl, text))


def finding(severity, category, file, line, title, evidence, recommendation, confidence, source):
    return {
        "severity": severity,
        "category": category,
        "file": file,
        "line": line,
        "title": title,
        "evidence": redact(evidence),
        "recommendation": recommendation,
        "confidence": confidence,
        "source": source,
    }


def analyze(diff_text: str) -> list[dict]:
    if "TRPC_REVIEW_FORCE_SANDBOX_FAILURE" in diff_text:
        raise RuntimeError("forced sandbox failure for fixture coverage")
    out = []
    current_file = ""
    new_line = 0
    in_hunk = False
    for raw in diff_text.replace("\r\n", "\n").splitlines():
        if raw.startswith("+++ "):
            current_file = normalize(raw[4:].split("\t", 1)[0])
            in_hunk = False
            continue
        if raw.startswith(("--- ", "diff --git ")):
            in_hunk = False
            continue
        match = HUNK_RE.match(raw)
        if match:
            new_line = int(match.group("new"))
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if not raw.startswith("+"):
            # Removed lines do not advance the post-image counter; everything
            # else in a hunk is context and does. Blank context lines reach us
            # as "" whenever trailing whitespace has been stripped, so key off
            # the "-" prefix rather than a leading space -- otherwise the line
            # numbers drift away from agent/diff_parser.py.
            if not raw.startswith("-"):
                new_line += 1
            continue
        line = raw[1:].strip()
        candidate_line = new_line
        new_line += 1
        if has_secret(line):
            out.append(finding("critical", "sensitive_info", current_file, candidate_line, "Potential secret in diff", line, "Remove and rotate the credential.", 0.98, "skill-script:sensitive-info"))
        elif "<REDACTED>" in line:
            # The sandbox only ever receives the redacted diff, so a masking
            # marker is corroborating evidence that the in-process engine found
            # a credential here. Deliberately below the confident threshold: it
            # locates the finding, it does not independently establish one.
            out.append(finding("critical", "sensitive_info", current_file, candidate_line, "Credential masked upstream at this line", line, "Remove and rotate the credential.", 0.75, "skill-script:sensitive-info-marker"))
        if re.search(r"\b(eval|exec)\s*\(", line):
            out.append(finding("high", "security", current_file, candidate_line, "Dynamic code execution", line, "Replace dynamic execution with explicit parsing or dispatch.", 0.9, "skill-script:dangerous-exec"))
        if re.search(r"\bos\.(system|popen)\s*\(", line):
            out.append(finding("high", "security", current_file, candidate_line, "Shell command execution", line, "Use subprocess with an argument list and validate inputs.", 0.86, "skill-script:command-injection"))
        if "shell=True" in line and "subprocess" in line:
            out.append(finding("high", "security", current_file, candidate_line, "subprocess shell=True", line, "Use argument lists with shell=False.", 0.88, "skill-script:shell-injection"))
        if re.search(r"\bexecute\s*\([^)]*(\+|%)", line):
            out.append(finding("high", "security", current_file, candidate_line, "SQL string concatenation", line, "Use parameterized SQL and pass values separately.", 0.86, "skill-script:sql-injection"))
        if "aiohttp.ClientSession(" in line and "async with" not in line:
            out.append(finding("high", "async_resource", current_file, candidate_line, "Unscoped aiohttp ClientSession", line, "Use async with or close in finally.", 0.88, "skill-script:async-session"))
        if "httpx.AsyncClient(" in line and "async with" not in line:
            out.append(finding("high", "async_resource", current_file, candidate_line, "Unscoped httpx AsyncClient", line, "Use async with or close the client in finally.", 0.86, "skill-script:async-client"))
        if re.search(r"=\s*open\s*\(", line) and "with " not in line:
            out.append(finding("medium", "resource_leak", current_file, candidate_line, "File handle not scoped", line, "Use with open(...) as f.", 0.78, "skill-script:file-lifecycle"))
        if re.search(r"=\s*(sqlite3|psycopg2|pymysql|aiomysql)\.connect\s*\(", line):
            out.append(finding("high", "db_lifecycle", current_file, candidate_line, "Database connection not scoped", line, "Close in finally or use a context manager.", 0.86, "skill-script:db-lifecycle"))
    return out


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: static_rules.py INPUT.diff OUTPUT.json", file=sys.stderr)
        return 2
    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    payload = {"findings": analyze(input_path.read_text(encoding="utf-8"))}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
