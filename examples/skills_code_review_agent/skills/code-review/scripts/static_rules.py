#!/usr/bin/env python3
"""Run lightweight static review rules over a unified diff."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


HUNK_RE = re.compile(r"@@ -\d+(?:,\d+)? \+(?P<new>\d+)(?:,\d+)? @@")
SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|password|secret)\b(\s*[:=]\s*)(['\"]?)([^'\"()\s,;#]{8,})(\3)(?=$|[\s,;#])"
)


def normalize(path: str) -> str:
    path = path.strip()
    if path in {"/dev/null", "dev/null"}:
        return ""
    if path.startswith("a/") or path.startswith("b/"):
        path = path[2:]
    return path


def redact(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        if "<REDACTED>" in match.group(4):
            return match.group(0)
        quote = match.group(3) or ""
        return match.group(1) + match.group(2) + quote + "<REDACTED>" + quote

    return SECRET_RE.sub(repl, text)


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
    for raw in diff_text.replace("\r\n", "\n").splitlines():
        if raw.startswith("+++ "):
            current_file = normalize(raw[4:].split("\t", 1)[0])
            continue
        match = HUNK_RE.match(raw)
        if match:
            new_line = int(match.group("new"))
            continue
        if not raw.startswith("+") or raw.startswith("+++ "):
            if raw.startswith(" ") and new_line:
                new_line += 1
            continue
        line = raw[1:].strip()
        candidate_line = new_line
        new_line += 1
        if SECRET_RE.search(line) or "<REDACTED>" in line:
            out.append(finding("critical", "sensitive_info", current_file, candidate_line, "Potential secret in diff", line, "Remove and rotate the credential.", 0.98, "skill-script:sensitive-info"))
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
