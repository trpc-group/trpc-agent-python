"""Integration tests: manifest samples must match expected decisions.

These tests verify the public manifest in
``trpc_agent_sdk/tools/safety/examples/samples/manifest.yaml``. Per the issue acceptance
criteria: every sample must produce a structured report, high-risk
detection must be >= 90%, safe false positives <= 10%, and the
key-credential-delete-non-allowlist categories must be 100%.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from trpc_agent_sdk.tools.safety._guard import ToolSafetyGuard
from trpc_agent_sdk.tools.safety._models import SafetyDecision, SafetyScanRequest, ScriptLanguage
from trpc_agent_sdk.tools.safety._policy import load_safety_policy


REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = (
    REPO_ROOT / "trpc_agent_sdk" / "tools" / "safety" / "examples"
    / "tool_safety_policy.yaml"
)
MANIFEST_PATH = (
    REPO_ROOT / "trpc_agent_sdk" / "tools" / "safety" / "examples"
    / "samples" / "manifest.yaml"
)


@pytest.fixture(scope="module")
def guard():
    return ToolSafetyGuard(load_safety_policy(POLICY_PATH))


@pytest.fixture(scope="module")
def manifest():
    data = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    return data["samples"]


@pytest.fixture(scope="module")
def scan_results(guard, manifest):
    results = []
    base = MANIFEST_PATH.parent
    for sample in manifest:
        file_path = base / sample["file"]
        script = file_path.read_text(encoding="utf-8")
        request = SafetyScanRequest(
            tool_name="integration",
            language=ScriptLanguage(sample["language"]),
            script=script,
        )
        report = guard.scan(request)
        results.append((sample, report))
    return results


def test_every_sample_produces_report(scan_results):
    assert len(scan_results) == 14
    for sample, report in scan_results:
        assert report.report_id
        assert report.decision in (
            SafetyDecision.ALLOW,
            SafetyDecision.DENY,
            SafetyDecision.NEEDS_HUMAN_REVIEW,
        )
        assert report.rule_ids
        assert report.script_sha256
        assert report.policy_hash


def test_expected_decisions_match(scan_results):
    mismatches = []
    for sample, report in scan_results:
        expected = sample["expected_decision"]
        actual = report.decision.value
        if expected == "allow" and actual != "allow":
            mismatches.append((sample["name"], expected, actual))
        elif expected == "deny" and actual != "deny":
            mismatches.append((sample["name"], expected, actual))
        elif expected == "needs_human_review" \
                and actual != "needs_human_review":
            mismatches.append((sample["name"], expected, actual))
    assert not mismatches, f"mismatches: {mismatches}"


def test_detection_rate_high_risk(scan_results):
    high_risk = [(s, r) for s, r in scan_results
                 if s["expected_decision"] == "deny"]
    detected = [(s, r) for s, r in high_risk
                if r.decision == SafetyDecision.DENY]
    rate = len(detected) / len(high_risk) if high_risk else 1.0
    assert rate >= 0.9, f"detection rate {rate:.2%} below 90%"


def test_safe_false_positive_rate(scan_results):
    safe = [(s, r) for s, r in scan_results
            if s["expected_decision"] == "allow"]
    if not safe:
        return
    flagged = [(s, r) for s, r in safe
               if r.decision != SafetyDecision.ALLOW]
    rate = len(flagged) / len(safe)
    assert rate <= 0.1, f"false positive rate {rate:.2%} above 10%"


def test_required_categories_100_pct(scan_results):
    """Credential read, recursive delete, and non-allowlist network
    must be detected 100% of the time."""

    required = {
        "FILE003_CREDENTIAL_READ",
        "FILE001_RECURSIVE_DELETE",
        "NET001_DOMAIN_NOT_ALLOWED",
        "FILE004_DOTENV_READ",
    }
    for sample, report in scan_results:
        if "expected_rule_ids" in sample:
            for rule in sample["expected_rule_ids"]:
                if rule in required:
                    assert rule in report.rule_ids, \
                        f"{sample['name']}: expected {rule}, got {report.rule_ids}"


def test_policy_changes_decision_without_code_change(strict_policy_dict):
    """Tweak a domain in the YAML; verify behavior changes without code."""

    import copy
    from trpc_agent_sdk.tools.safety._policy import load_safety_policy_dict

    base = copy.deepcopy(strict_policy_dict)
    # Domain NOT allowlisted in base policy -> deny.
    base_guard = ToolSafetyGuard(load_safety_policy_dict(base))
    request = SafetyScanRequest(
        tool_name="t", language=ScriptLanguage.PYTHON,
        script="import requests\nrequests.get('https://my.api.example.com')\n",
    )
    base_report = base_guard.scan(request)
    assert base_report.decision == SafetyDecision.DENY

    # Allow my.api.example.com -> allow.
    modified = copy.deepcopy(base)
    modified["network"] = {"allow_domains": ["my.api.example.com"]}
    modified_guard = ToolSafetyGuard(load_safety_policy_dict(modified))
    modified_report = modified_guard.scan(request)
    assert modified_report.decision == SafetyDecision.ALLOW


def test_report_serialization_invariants(scan_results):
    """Reports must never leak raw script or env."""

    for sample, report in scan_results:
        payload = report.model_dump_json()
        lowered = payload.lower()
        assert "\"script\":" not in payload
        # The script sha256 hash appears as "script_sha256" only.
