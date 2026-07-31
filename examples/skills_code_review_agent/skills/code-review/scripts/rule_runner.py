#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Validate normalized review input and emit the rule-runner JSON contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from agent.models import ChangedFile
    from agent.models import ChangedLine
    from agent.models import DiffHunk
    from agent.models import InputSummary
    from agent.models import InputType
    from agent.review_rules import run_review_rules
    from agent.sanitizer import redact_mapping
except ImportError:  # pragma: no cover - supports direct script execution from the Skill directory
    sys.path.insert(0, str(Path(__file__).parents[3]))
    from agent.models import ChangedFile
    from agent.models import ChangedLine
    from agent.models import DiffHunk
    from agent.models import InputSummary
    from agent.models import InputType
    from agent.review_rules import run_review_rules
    from agent.sanitizer import redact_mapping

SCHEMA_VERSION = "code-review.rules.v1"
SKILL_NAME = "code-review"


def build_parser() -> argparse.ArgumentParser:
    """Build the rule-runner command parser."""
    parser = argparse.ArgumentParser(description="Validate code-review structured inputs.")
    parser.add_argument("--input", required=True, help="Path to normalized InputSummary JSON.")
    parser.add_argument("--manifest", required=True, help="Path to loaded Skill manifest JSON.")
    parser.add_argument("--output", help="Optional output JSON path. Defaults to stdout.")
    return parser


def run(input_path: str | Path, manifest_path: str | Path) -> dict[str, Any]:
    """Validate input and manifest JSON and return deterministic findings."""
    input_payload = _read_json_object(Path(input_path), "input")
    manifest_payload = _read_json_object(Path(manifest_path), "manifest")
    manifest = manifest_payload.get("manifest", manifest_payload)
    if not isinstance(manifest, dict):
        raise ValueError("manifest JSON must be an object")

    required_input = {"task_id", "input_type", "input_ref", "changed_files", "raw_diff_sha256"}
    missing_input = sorted(required_input.difference(input_payload))
    if missing_input:
        raise ValueError(f"input JSON is missing fields: {', '.join(missing_input)}")
    if not isinstance(input_payload["changed_files"], list):
        raise ValueError("input JSON field changed_files must be a list")
    if manifest.get("name") != SKILL_NAME:
        raise ValueError(f"manifest skill name must be {SKILL_NAME!r}")
    if not manifest.get("digest"):
        raise ValueError("manifest JSON is missing digest")
    input_summary = _input_summary_from_dict(input_payload)
    findings = [finding.to_dict() for finding in run_review_rules(input_summary)]

    return redact_mapping({
        "schema_version": SCHEMA_VERSION,
        "skill_name": SKILL_NAME,
        "findings": findings,
        "diagnostics": list(input_payload.get("diagnostics", [])),
    })


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = build_parser().parse_args(argv)
    try:
        result = run(args.input, args.manifest)
        rendered = json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
        if args.output:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
        return 0
    except (OSError, ValueError) as ex:
        print(f"rule_runner: {ex}", file=sys.stderr)
        return 2


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"{label} file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as ex:
        raise ValueError(f"{label} file is not valid JSON: {path}") from ex
    if not isinstance(payload, dict):
        raise ValueError(f"{label} JSON must be an object")
    return payload


def _input_summary_from_dict(payload: dict[str, Any]) -> InputSummary:
    changed_files = []
    for file_payload in payload.get("changed_files", []):
        hunks = []
        for hunk_payload in file_payload.get("hunks", []):
            lines = [
                ChangedLine(
                    line_type=str(line_payload.get("line_type", "")),
                    content=str(line_payload.get("content", "")),
                    old_line=line_payload.get("old_line"),
                    new_line=line_payload.get("new_line"),
                ) for line_payload in hunk_payload.get("lines", [])
            ]
            hunks.append(
                DiffHunk(
                    old_start=int(hunk_payload.get("old_start", 0)),
                    old_count=int(hunk_payload.get("old_count", 0)),
                    new_start=int(hunk_payload.get("new_start", 0)),
                    new_count=int(hunk_payload.get("new_count", 0)),
                    section_header=str(hunk_payload.get("section_header", "")),
                    lines=lines,
                ))
        changed_files.append(
            ChangedFile(
                path=str(file_payload.get("path", "")),
                old_path=str(file_payload.get("old_path", "")),
                status=str(file_payload.get("status", "modified")),
                hunks=hunks,
                added_lines=int(file_payload.get("added_lines", 0)),
                deleted_lines=int(file_payload.get("deleted_lines", 0)),
                candidate_lines=[int(line) for line in file_payload.get("candidate_lines", [])],
                is_binary=bool(file_payload.get("is_binary", False)),
            ))
    return InputSummary(
        task_id=str(payload["task_id"]),
        input_type=InputType(str(payload["input_type"])),
        input_ref=str(payload["input_ref"]),
        changed_files=changed_files,
        raw_diff_sha256=str(payload.get("raw_diff_sha256", "")),
        file_count=int(payload.get("file_count", len(changed_files))),
        hunk_count=int(payload.get("hunk_count", 0)),
        added_lines=int(payload.get("added_lines", 0)),
        deleted_lines=int(payload.get("deleted_lines", 0)),
        summary=str(payload.get("summary", "")),
        diagnostics=[str(item) for item in payload.get("diagnostics", [])],
        parser_version=str(payload.get("parser_version", "code-review.input.v1")),
        warnings=[str(item) for item in payload.get("warnings", [])],
    )


if __name__ == "__main__":
    raise SystemExit(main())
