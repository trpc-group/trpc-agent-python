#!/usr/bin/env python3
"""Detect high-confidence execution, deserialization, and SQL risks."""

import re

from review_common import ParsedDiff
from review_common import added_changes
from review_common import file_path
from review_common import finding
from review_common import run_rule_cli

RULE_NAME = "security"
LITERAL_ARGUMENT = re.compile(r"^(?:[rub]{0,2})(['\"]).*\1$", re.IGNORECASE)


def _has_dynamic_execution(content: str) -> bool:
    direct = re.search(
        r"\b(?:os\.(?:system|popen)|eval|exec)\s*\((.*)\)",
        content,
        re.IGNORECASE,
    )
    if direct:
        return not LITERAL_ARGUMENT.fullmatch(direct.group(1).strip())
    go_shell = re.search(
        r"\bexec\.Command(?:Context)?\s*\(\s*(?:[^,]+,\s*)?"
        r"[\"'](?:sh|bash|cmd(?:\.exe)?|powershell)[\"']\s*,\s*"
        r"[\"'](?:-c|/c)[\"']\s*,\s*([^,)]+)",
        content,
        re.IGNORECASE,
    )
    if go_shell:
        return not LITERAL_ARGUMENT.fullmatch(go_shell.group(1).strip())
    javascript_exec = re.search(
        r"\b(?:child_process\.)?(?:exec|execSync)\s*\(\s*([^,)]+)",
        content,
        re.IGNORECASE,
    )
    if javascript_exec:
        return not LITERAL_ARGUMENT.fullmatch(javascript_exec.group(1).strip())
    java_exec = re.search(
        r"\b(?:Runtime\.getRuntime\(\)|runtime)\.exec\s*\(\s*([^,)]+)",
        content,
        re.IGNORECASE,
    )
    if java_exec:
        return not LITERAL_ARGUMENT.fullmatch(java_exec.group(1).strip())
    if "shell=true" not in content.lower().replace(" ", ""):
        return False
    subprocess_call = re.search(
        r"\bsubprocess\.(?:run|popen|call|check_call|check_output)\s*\(\s*([^,]+)",
        content,
        re.IGNORECASE,
    )
    return subprocess_call is None or not LITERAL_ARGUMENT.fullmatch(
        subprocess_call.group(1).strip()
    )


def review(parsed: ParsedDiff) -> list[dict[str, object]]:
    """Return deterministic security candidates from added lines."""
    findings = []
    for file_data in parsed.get("files", []):
        path = file_path(file_data)
        for hunk, change in added_changes(file_data):
            content = str(change.get("content", ""))
            lowered = content.lower()
            visible_hunk_text = "\n".join(
                str(item.get("content", ""))
                for item in hunk.get("changes", [])
                if item.get("kind") in {"added", "context"}
            ).lower()
            command_execution = _has_dynamic_execution(content)
            unsafe_deserialization = bool(
                "pickle.loads(" in lowered
                or re.search(r"\bunserialize\s*\(\s*\$?\w+", content, re.IGNORECASE)
                or (
                    "yaml.load(" in lowered
                    and "safe_load(" not in lowered
                    and "safeloader" not in visible_hunk_text
                )
            )
            dynamic_sql = bool(
                re.search(r"\.execute(?:many)?\s*\(\s*f[\"']", lowered)
                or re.search(
                    r"\.execute(?:many)?\s*\(\s*[\"'][^\"']*[\"']\s*"
                    r"(?:\+|%|\.format\s*\()",
                    lowered,
                )
                or re.search(
                    r"\.(?:execute|query)\s*\(\s*`[^`]*\$\{",
                    content,
                    re.IGNORECASE,
                )
            )
            if not (command_execution or unsafe_deserialization or dynamic_sql):
                continue
            findings.append(
                finding(
                    severity="critical",
                    category=RULE_NAME,
                    file=path,
                    line=change.get("new_line"),
                    title="Untrusted data crosses a dangerous execution boundary",
                    evidence=content,
                    recommendation=(
                        "Use parameterized APIs or argument lists and validate "
                        "untrusted input before the boundary."
                    ),
                    confidence=0.96,
                    source="skill:review_security.py",
                )
            )
    return findings


if __name__ == "__main__":
    raise SystemExit(run_rule_cli(RULE_NAME, review))
