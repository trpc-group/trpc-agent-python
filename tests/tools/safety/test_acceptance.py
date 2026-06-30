# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Acceptance tests over the 12 public sample scripts.

Covers acceptance criteria 1, 2, 3, 4 and 5 against the shipped samples:

1. every sample scans and produces a structured report,
2. deny detection rate >= 90% and safe false-positive rate <= 10%
   (false positive = safe sample judged ``deny``; ``needs_human_review`` is NOT
   counted, per design section 5),
3. secret-read / dangerous-delete / non-allow-listed egress denied 100%,
4. a 500-line scan finishes in under a second,
5. each report carries the five required elements.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from trpc_agent_sdk.tools.safety.engine import SafetyEngine
from trpc_agent_sdk.tools.safety.models import Decision
from trpc_agent_sdk.tools.safety.models import Language
from trpc_agent_sdk.tools.safety.models import ScanInput
from trpc_agent_sdk.tools.safety.policy import load_policy

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXAMPLE_DIR = _REPO_ROOT / "examples" / "tool_safety_guard"
_SAMPLES_DIR = _EXAMPLE_DIR / "samples"
_POLICY = _EXAMPLE_DIR / "tool_safety_policy.yaml"

# The three must-catch categories -> the samples that exercise them.
_MUST_CATCH = {
    "02_dangerous_delete.py",   # dangerous delete
    "03_read_secret.py",        # secret read
    "04_network_egress.py",     # non-allow-listed egress
    "10_secret_leak_output.py", # secret leak
    "11_curl_pipe_bash.sh",     # non-allow-listed egress
}

_SUFFIX_LANG = {".py": Language.PYTHON, ".sh": Language.BASH, ".bash": Language.BASH}


def _language(path: Path) -> Language:
    return _SUFFIX_LANG.get(path.suffix.lower(), Language.UNKNOWN)


@pytest.fixture(scope="module")
def expected():
    return json.loads((_SAMPLES_DIR / "EXPECTED.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def engine():
    return SafetyEngine(load_policy(str(_POLICY)))


@pytest.fixture(scope="module")
def reports(engine, expected):
    out = {}
    for name in expected:
        path = _SAMPLES_DIR / name
        script = path.read_text(encoding="utf-8", errors="ignore")
        out[name] = engine.scan(ScanInput(script=script, tool_name=name, language=_language(path)))
    return out


def test_all_samples_scanned_and_reported(reports, expected):
    """Acceptance 1."""
    assert len(reports) == 12
    for name, report in reports.items():
        assert report.tool_name == name
        assert report.to_dict()["decision"] in {"allow", "deny", "needs_human_review"}


def test_report_has_five_elements_for_every_sample(reports):
    """Acceptance 5."""
    for report in reports.values():
        data = report.to_dict()
        assert "decision" in data
        assert "risk_level" in data
        for finding in data["findings"]:
            assert finding["rule_id"]
            assert "evidence" in finding
            assert finding["recommendation"]


def test_every_decision_matches_expectation(reports, expected):
    for name, report in reports.items():
        assert report.decision.value == expected[name], f"{name}: {report.decision.value} != {expected[name]}"


def test_deny_detection_rate_at_least_90_percent(reports, expected):
    """Acceptance 2 (detection)."""
    deny_expected = [n for n, d in expected.items() if d == "deny"]
    hits = sum(1 for n in deny_expected if reports[n].decision == Decision.DENY)
    assert hits / len(deny_expected) >= 0.90


def test_safe_false_positive_rate_at_most_10_percent(reports, expected):
    """Acceptance 2 (false positives). Only safe->deny counts."""
    safe = [n for n, d in expected.items() if d == "allow"]
    false_positives = sum(1 for n in safe if reports[n].decision == Decision.DENY)
    assert false_positives / len(safe) <= 0.10


def test_safe_samples_are_allowed(reports):
    """The two safe samples are the false-positive denominator: both must allow."""
    assert reports["01_safe_compute.py"].decision == Decision.ALLOW
    assert reports["05_allowlisted_request.py"].decision == Decision.ALLOW


def test_must_catch_categories_100_percent(reports):
    """Acceptance 3."""
    for name in _MUST_CATCH:
        assert reports[name].decision == Decision.DENY, f"{name} must be denied"


def test_single_500_line_scan_under_one_second(engine):
    """Acceptance 4."""
    script = "\n".join(f"value_{i} = {i} * {i}" for i in range(500))
    start = time.perf_counter()
    engine.scan(ScanInput(script=script, tool_name="perf", language=Language.PYTHON))
    assert (time.perf_counter() - start) < 1.0
