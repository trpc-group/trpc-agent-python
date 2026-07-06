# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Rule engine accuracy on a labeled corpus.

Acceptance criterion 2 (≥80% detection / ≤15% false positives on hidden
samples) is approximated here with a labeled public corpus: every seeded
issue must be detected (recall proxy) and a clean corpus must yield zero
HIGH-CONFIDENCE findings (false-positive proxy) — documented in the README.
"""

from typing import List

from codereview.diff_parser import parse_unified_diff
from codereview.diff_parser import run_all_rules

MIN_CONFIDENCE = 0.7  # mirror of NoiseConfig.min_confidence


def _diff_for(path: str, lines: List[str]) -> str:
    body = "".join(f"+{line}\n" for line in lines)
    return (f"diff --git a/{path} b/{path}\n"
            f"new file mode 100644\n"
            f"--- /dev/null\n"
            f"+++ b/{path}\n"
            f"@@ -0,0 +1,{len(lines)} @@\n{body}")


def _run(path: str, lines: List[str]):
    return run_all_rules(parse_unified_diff(_diff_for(path, lines)))


def _categories(findings) -> set:
    return {finding["category"] for finding in findings}


# --- positive corpus: every seeded issue must be caught -----------------------

POSITIVE_CASES = [
    ("security_risk", ["import os", "os.system('rm ' + name)"]),
    ("security_risk", ["import subprocess", "subprocess.run(cmd, shell=True)"]),
    ("security_risk", ["result = eval(user_input)"]),
    ("security_risk", ["import pickle", "obj = pickle.loads(blob)"]),
    ("security_risk", ["import yaml", "cfg = yaml.load(stream)"]),
    ("security_risk", ["cur.execute(f\"SELECT * FROM t WHERE id={uid}\")"]),
    ("security_risk", ["requests.get(url, verify=False)"]),
    ("async_error", ["import time", "async def go():", "    time.sleep(3)"]),
    ("async_error", ["import requests", "async def fetch():", "    requests.get(url)"]),
    ("async_error", ["asyncio.create_task(worker())"]),
    ("resource_leak", ["fh = open('/tmp/data.txt')", "data = fh.read()"]),
    ("resource_leak", ["tmp = tempfile.NamedTemporaryFile(delete=False)"]),
    ("resource_leak", ["sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)"]),
    ("secret_leakage", ["password = 'hunter2butlonger'"]),
    ("secret_leakage", ["KEY = 'AKIAIOSFODNN7EXAMPLE'"]),
    ("secret_leakage", ["tok = 'ghp_FAKE1234567890abcdefFAKE12345'"]),
    ("db_lifecycle", ["conn = sqlite3.connect(path)", "cur = conn.cursor()",
                      "cur.execute('BEGIN')", "cur.execute('UPDATE t SET x=1')"]),
    ("db_lifecycle", ["for row in rows:", "    conn = engine.connect()",
                      "    conn.execute(stmt)"]),
    ("missing_tests", ["def helper():", "    return 1"]),
]


def test_positive_corpus_full_recall():
    missed = []
    for expected_category, lines in POSITIVE_CASES:
        findings = _run("app/mod.py", lines)
        if expected_category not in _categories(findings):
            missed.append((expected_category, lines))
    assert not missed, f"undetected seeded issues: {missed}"


def test_detection_rate_at_least_80_percent():
    hits = sum(1 for expected, lines in POSITIVE_CASES
               if expected in _categories(_run("app/mod.py", lines)))
    assert hits / len(POSITIVE_CASES) >= 0.8


# --- clean corpus: no high-confidence false positives --------------------------

CLEAN_CASES = [
    ["import os", "path = os.path.join(base, name)"],
    ["with open(path) as fh:", "    data = fh.read()"],
    ["async def go():", "    await asyncio.sleep(1)"],
    ["with engine.connect() as conn:", "    conn.execute(stmt)", "    conn.commit()"],
    ["password = os.environ['DB_PASSWORD']"],
    ["token = get_secret('svc-token')  # loaded at runtime"],
    ["cur.execute('SELECT * FROM t WHERE id = %s', (uid,))"],
    ["cfg = yaml.safe_load(stream)"],
    ["result = subprocess.run(['ls', '-l'], check=True)"],
    ["api_key = None"],
]


def test_clean_corpus_no_high_confidence_findings():
    false_positives = []
    for lines in CLEAN_CASES:
        for finding in _run("app/clean.py", lines):
            if finding["category"] == "missing_tests":
                continue  # changeset-level, expected on code-only snippets
            if finding["confidence"] >= MIN_CONFIDENCE:
                false_positives.append(finding)
    assert not false_positives, f"high-confidence FPs on clean code: {false_positives}"


def test_false_positive_rate_within_15_percent():
    flagged = 0
    for lines in CLEAN_CASES:
        findings = [finding for finding in _run("app/clean.py", lines)
                    if finding["category"] != "missing_tests"
                    and finding["confidence"] >= MIN_CONFIDENCE]
        if findings:
            flagged += 1
    assert flagged / len(CLEAN_CASES) <= 0.15


# --- rule details ---------------------------------------------------------------

def test_finding_schema_complete():
    findings = _run("app/mod.py", ["os.system('reboot')"])
    finding = next(item for item in findings if item["category"] == "security_risk")
    for key in ("severity", "category", "file", "line", "title", "evidence",
                "recommendation", "confidence", "source", "rule_id"):
        assert key in finding and finding[key] not in ("", None), key
    assert finding["file"] == "app/mod.py"
    assert finding["line"] == 1
    assert finding["source"] == "static_rule"


def test_missing_tests_not_reported_when_tests_change():
    diff = (_diff_for("app/mod.py", ["def helper():", "    return 1"])
            + _diff_for("tests/test_mod.py", ["def test_helper():", "    assert True"]))
    findings = run_all_rules(parse_unified_diff(diff))
    assert "missing_tests" not in _categories(findings)


def test_secret_evidence_pre_redacted_in_sandbox_rule():
    findings = _run("app/cfg.py", ["password = 'plaintexthunter2'"])
    secret_findings = [item for item in findings if item["category"] == "secret_leakage"]
    assert secret_findings
    assert all("plaintexthunter2" not in item["evidence"] for item in secret_findings)
    assert all("***REDACTED***" in item["evidence"] for item in secret_findings)
