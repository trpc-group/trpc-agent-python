# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for policy-driven ``forbidden_paths`` enforcement (acceptance req. 6).

These prove the part of requirement 6 that says *forbidden paths* must be
tunable from the policy file with **no code change**:

- a path listed in ``forbidden_paths`` is flagged and denied,
- adding/removing a path in the policy flips the decision with no code change,
- ``/dev`` / ``/proc`` / ``/sys`` map to ``FILE_OVERWRITE_DEVICE``,
- boundary matching avoids false positives (``/etcd``, URL paths),
- the overly-broad ``/`` entry is skipped.
"""

from __future__ import annotations

from dataclasses import replace

from trpc_agent_sdk.tools.safety.engine import SafetyEngine
from trpc_agent_sdk.tools.safety.models import Decision
from trpc_agent_sdk.tools.safety.models import Language
from trpc_agent_sdk.tools.safety.models import ScanInput
from trpc_agent_sdk.tools.safety.policy import SafetyPolicy


def _policy(forbidden_paths: list[str]) -> SafetyPolicy:
    """A minimal policy that only configures the forbidden paths under test."""
    return replace(SafetyPolicy.default(), forbidden_paths=list(forbidden_paths))


def _scan(policy: SafetyPolicy, script: str, language: Language = Language.PYTHON):
    return SafetyEngine(policy).scan(
        ScanInput(script=script, tool_name="t", language=language))


def _rule_ids(report) -> set[str]:
    return {f.rule_id for f in report.findings}


def test_forbidden_path_flagged_and_denied():
    script = 'open("/etc/cron.d/payload", "w").write("* * * * * root sh")\n'
    report = _scan(_policy(["/etc"]), script)
    assert "FILE_FORBIDDEN_PATH" in _rule_ids(report)
    assert report.decision == Decision.DENY


def test_device_path_maps_to_overwrite_device():
    for forbidden, path in (("/dev", "/dev/sda"), ("/proc", "/proc/1/mem"), ("/sys", "/sys/power/state")):
        report = _scan(_policy([forbidden]), f'open("{path}", "wb")\n')
        assert "FILE_OVERWRITE_DEVICE" in _rule_ids(report), forbidden
        assert report.decision == Decision.DENY


def test_config_driven_add_and_remove_no_code_change():
    """The same script flips decision purely by editing the policy."""
    script = 'open("/data/prod/secret.txt", "w")\n'

    # Path NOT in the policy -> nothing detected, allowed.
    before = _scan(_policy(["/etc"]), script)
    assert "FILE_FORBIDDEN_PATH" not in _rule_ids(before)
    assert before.decision == Decision.ALLOW

    # Add the path to the policy -> now flagged and denied (no code change).
    after = _scan(_policy(["/etc", "/data/prod"]), script)
    assert "FILE_FORBIDDEN_PATH" in _rule_ids(after)
    assert after.decision == Decision.DENY


def test_home_path_expanded():
    report = _scan(_policy(["~/.ssh"]), 'open("~/.ssh/config")\n')
    assert "FILE_FORBIDDEN_PATH" in _rule_ids(report)


def test_no_false_positive_on_similar_prefix():
    """A forbidden ``/etc`` must not match ``/etcd`` or a URL path component."""
    script = (
        'open("/etcd/data/state.db")\n'
        'url = "https://example.com/etc/info"\n'
    )
    report = _scan(_policy(["/etc"]), script)
    assert "FILE_FORBIDDEN_PATH" not in _rule_ids(report)


def test_root_path_is_skipped_as_too_broad():
    """A bare ``/`` entry is ignored (every path contains it)."""
    report = _scan(_policy(["/"]), 'open("/tmp/workdir/output.txt", "w")\n')
    assert "FILE_FORBIDDEN_PATH" not in _rule_ids(report)


def test_bash_forbidden_path():
    report = _scan(_policy(["/etc"]), "cat /etc/shadow", language=Language.BASH)
    assert "FILE_FORBIDDEN_PATH" in _rule_ids(report)
    assert report.decision == Decision.DENY
