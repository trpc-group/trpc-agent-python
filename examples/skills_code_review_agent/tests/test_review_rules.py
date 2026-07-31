# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Business tests for deterministic review rules."""

from __future__ import annotations

from examples.skills_code_review_agent.agent import FindingCategory
from examples.skills_code_review_agent.agent import FindingSeverity
from examples.skills_code_review_agent.agent import parse_unified_diff
from examples.skills_code_review_agent.agent import run_review_rules
from examples.skills_code_review_agent.tests.secret_samples import openai_like_token


def test_hard_coded_secret_is_reported_and_redacted():
    findings = _findings(f"""+API_KEY = "{openai_like_token()}"\n""")

    secret = _first(findings, FindingCategory.SECRET)
    assert secret.severity is FindingSeverity.CRITICAL
    assert "abcdefghijklmnop" not in secret.evidence
    assert "[REDACTED]" in secret.evidence


def test_skill_words_are_not_secret_findings():
    findings = _findings("""+path = "skills_code_review_agent/skill_manifest.json"\n""")

    assert all(item.category is not FindingCategory.SECRET for item in findings)


def test_eval_exec_and_shell_true_are_high_security_findings():
    findings = _findings("""+eval(user_input)\n+exec(code)\n+subprocess.run(cmd, shell=True)\n""")

    security_findings = [item for item in findings if item.category is FindingCategory.SECURITY]
    assert len(security_findings) == 3
    assert all(item.severity is FindingSeverity.HIGH for item in security_findings)


def test_async_client_without_context_is_reported():
    findings = _findings("""+client = httpx.AsyncClient()\n+return await client.get(url)\n""")

    assert _first(findings, FindingCategory.ASYNC).title == "Async client/session may not be closed"


def test_file_handle_without_context_is_reported():
    findings = _findings("""+handle = open(path)\n+return handle.read()\n""")

    assert _first(findings, FindingCategory.RESOURCE).title == "File handle may not be closed"


def test_database_connection_without_close_is_reported():
    findings = _findings("""+conn = sqlite3.connect(path)\n+conn.execute(sql)\n""")

    assert _first(findings, FindingCategory.DB).title == "Database connection lifecycle is not bounded"


def test_context_managed_python_resources_do_not_report_lifecycle_findings():
    findings = _findings("+with open(path) as handle:\n"
                         "+    data = handle.read()\n"
                         "+async with httpx.AsyncClient() as client:\n"
                         "+    await client.get(url)\n"
                         "+with sqlite3.connect(path) as conn:\n"
                         "+    conn.execute(sql)\n")

    categories = {finding.category for finding in findings}
    assert FindingCategory.RESOURCE not in categories
    assert FindingCategory.ASYNC not in categories
    assert FindingCategory.DB not in categories


def test_malformed_python_falls_back_to_regex_rules():
    findings = _findings("""+if True\n+eval(user_input)\n""")

    assert _first(findings, FindingCategory.SECURITY)


def test_test_update_suppresses_missing_tests_finding():
    diff = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-old
+new
diff --git a/tests/test_app.py b/tests/test_app.py
--- a/tests/test_app.py
+++ b/tests/test_app.py
@@ -1 +1 @@
-assert old
+assert new
"""

    findings = run_review_rules(parse_unified_diff(diff, task_id="task-tests"))

    assert all(item.category is not FindingCategory.TEST for item in findings)


def test_source_change_without_test_update_has_stable_fingerprint():
    diff = _diff_for_lines("+new = 1\n")

    first = run_review_rules(parse_unified_diff(diff, task_id="task-a"))
    second = run_review_rules(parse_unified_diff(diff, task_id="task-b"))

    first_test = _first(first, FindingCategory.TEST)
    second_test = _first(second, FindingCategory.TEST)
    assert first_test.fingerprint == second_test.fingerprint


def _findings(added_lines: str):
    return run_review_rules(parse_unified_diff(_diff_for_lines(added_lines), task_id="task"))


def _diff_for_lines(added_lines: str) -> str:
    return f"""diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1,{max(1, added_lines.count(chr(10)))} @@
-old
{added_lines}"""


def _first(findings, category: FindingCategory):
    for finding in findings:
        if finding.category is category:
            return finding
    raise AssertionError(f"missing finding category: {category}")
