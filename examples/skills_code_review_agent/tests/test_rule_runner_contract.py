# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Business tests for the bundled rule-runner JSON contract."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from examples.skills_code_review_agent.agent import load_skill
from examples.skills_code_review_agent.agent import parse_unified_diff


def test_rule_runner_accepts_structured_input(tmp_path: Path):
    input_path, manifest_path = _write_contract_inputs(tmp_path)
    result = _run_rule_runner("--input", str(input_path), "--manifest", str(manifest_path))

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {
        "diagnostics": [],
        "findings": [],
        "schema_version": "code-review.rules.v1",
        "skill_name": "code-review",
    }


def test_rule_runner_reports_missing_required_fields(tmp_path: Path):
    input_path = tmp_path / "input.json"
    manifest_path = tmp_path / "manifest.json"
    input_path.write_text(json.dumps({"changed_files": []}), encoding="utf-8")
    manifest_path.write_text(json.dumps(load_skill(_skill_root()).manifest.to_dict()), encoding="utf-8")

    result = _run_rule_runner("--input", str(input_path), "--manifest", str(manifest_path))

    assert result.returncode == 2
    assert "missing fields" in result.stderr


def test_rule_runner_rejects_mismatched_skill_name(tmp_path: Path):
    input_path, manifest_path = _write_contract_inputs(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["name"] = "other"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = _run_rule_runner("--input", str(input_path), "--manifest", str(manifest_path))

    assert result.returncode == 2
    assert "manifest skill name" in result.stderr


def test_rule_runner_writes_output_file(tmp_path: Path):
    input_path, manifest_path = _write_contract_inputs(tmp_path)
    output_path = tmp_path / "out" / "result.json"

    result = _run_rule_runner(
        "--input",
        str(input_path),
        "--manifest",
        str(manifest_path),
        "--output",
        str(output_path),
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert set(payload) == {"schema_version", "skill_name", "findings", "diagnostics"}
    assert payload["findings"] == []


def _write_contract_inputs(tmp_path: Path) -> tuple[Path, Path]:
    input_summary = parse_unified_diff("", task_id="task")
    manifest = load_skill(_skill_root()).manifest
    input_path = tmp_path / "parsed_input.json"
    manifest_path = tmp_path / "skill_manifest.json"
    input_path.write_text(json.dumps(input_summary.to_dict()), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest.to_dict()), encoding="utf-8")
    return input_path, manifest_path


def _run_rule_runner(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_skill_root() / "scripts" / "rule_runner.py"), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _skill_root() -> Path:
    return Path(__file__).parents[1] / "skills" / "code-review"
