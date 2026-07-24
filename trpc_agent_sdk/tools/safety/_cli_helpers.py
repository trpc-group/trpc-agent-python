# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Private helpers shared by the tool safety CLI and examples."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from ._audit import build_safety_audit_event
from ._policy import SafetyPolicy
from ._policy import load_safety_policy
from ._scanner import SafetyScanner
from ._types import SafetyAuditEvent
from ._types import SafetyDecision
from ._types import SafetyReport
from ._types import ScanTarget
from ._types import ScriptLanguage

FIXTURE_GENERATED_AT = "2026-07-04T00:00:00Z"
TOOL_NAME = "tool_safety_check"
DECISION_VALUES = tuple(decision.value for decision in SafetyDecision)


def load_samples(path: str | Path) -> list[dict[str, Any]]:
    """Load and validate the public sample YAML file."""

    sample_path = Path(path)
    loaded = yaml.safe_load(sample_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, list):
        raise ValueError("samples YAML must contain a list of sample mappings.")

    samples: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(loaded):
        if not isinstance(item, dict):
            raise ValueError(f"sample at index {index} must be a mapping.")
        sample_id = _string_field(item, "id")
        if not sample_id:
            raise ValueError(f"sample at index {index} must have a non-empty id.")
        if sample_id in seen_ids:
            raise ValueError(f"duplicate sample id: {sample_id}")
        seen_ids.add(sample_id)

        if not _string_field(item, "description"):
            raise ValueError(f"sample {sample_id} must have a non-empty description.")
        if not _string_field(item, "language"):
            raise ValueError(f"sample {sample_id} must have a non-empty language.")
        if not (_string_field(item, "content") or _string_field(item, "command")):
            raise ValueError(f"sample {sample_id} must define content or command.")
        if not _string_field(item, "expected_decision"):
            raise ValueError(f"sample {sample_id} must define expected_decision.")

        expected_rules = item.get("expected_rules", [])
        if not isinstance(expected_rules, list) or not all(isinstance(rule, str) for rule in expected_rules):
            raise ValueError(f"sample {sample_id} expected_rules must be a list of strings.")

        samples.append(dict(item))
    return samples


def load_policy(path: str | Path | None) -> SafetyPolicy:
    """Load an explicit policy when provided, otherwise use package defaults."""

    if path is None:
        from ._policy import default_safety_policy

        return default_safety_policy()
    return load_safety_policy(path)


def scan_samples(
    samples: list[dict[str, Any]],
    policy: SafetyPolicy,
    *,
    generated_at: str = FIXTURE_GENERATED_AT,
    stable_elapsed_ms: float | None = None,
) -> tuple[dict[str, Any], list[SafetyAuditEvent], list[str]]:
    """Scan sample mappings and return aggregate report, audit events, and mismatches."""

    scanner = SafetyScanner(policy)
    results: list[dict[str, Any]] = []
    audit_events: list[SafetyAuditEvent] = []
    mismatches: list[str] = []

    for sample in samples:
        report = scanner.scan(build_target_from_sample(sample))
        if stable_elapsed_ms is not None:
            report = report.model_copy(update={"elapsed_ms": stable_elapsed_ms})
        result = build_sample_result(sample, report)
        results.append(result)
        audit_events.append(build_audit_event(result["sample_id"], report, generated_at=generated_at))
        if not result["match"]:
            mismatches.append(_mismatch_message(result))

    return build_aggregate_report(
        policy_name=policy.name,
        results=results,
        generated_at=generated_at,
    ), audit_events, mismatches


def scan_file(
    path: str | Path,
    policy: SafetyPolicy,
    *,
    language: str = "unknown",
    generated_at: str = FIXTURE_GENERATED_AT,
) -> tuple[dict[str, Any], list[SafetyAuditEvent]]:
    """Scan one local file and return an aggregate report with one result."""

    file_path = Path(path)
    content = file_path.read_text(encoding="utf-8")
    target = ScanTarget(
        content=content,
        language=parse_language(language),
        tool_name=TOOL_NAME,
        tool_metadata={
            "source": "file",
            "file_name": file_path.name
        },
    )
    report = SafetyScanner(policy).scan(target)
    sample = {
        "id": file_path.name,
        "description": f"Scan file {file_path.name}",
        "expected_decision": report.decision.value,
        "expected_rules": [finding.rule_id for finding in report.findings],
    }
    result = build_sample_result(sample, report)
    event = build_audit_event(result["sample_id"], report, generated_at=generated_at)
    aggregate = build_aggregate_report(
        policy_name=policy.name,
        results=[result],
        generated_at=generated_at,
    )
    return aggregate, [event]


def build_target_from_sample(sample: dict[str, Any]) -> ScanTarget:
    """Normalize one sample mapping into a scanner target."""

    env_keys = sample.get("env_keys", [])
    if env_keys is None:
        env_keys = []
    if not isinstance(env_keys, list) or not all(isinstance(key, str) for key in env_keys):
        raise ValueError(f"sample {sample.get('id', '<unknown>')} env_keys must be a list of strings.")

    return ScanTarget(
        content=_string_field(sample, "content"),
        command=_string_field(sample, "command"),
        language=parse_language(_string_field(sample, "language")),
        env={key: ""
             for key in env_keys},
        tool_name=TOOL_NAME,
        tool_metadata={"sample_id": _string_field(sample, "id")},
    )


def parse_language(value: str) -> ScriptLanguage:
    """Parse public CLI/sample language names into ScriptLanguage."""

    normalized = (value or "unknown").strip().lower()
    if normalized in {"python", "python3", "py"}:
        return ScriptLanguage.PYTHON
    if normalized == "bash":
        return ScriptLanguage.BASH
    if normalized in {"sh", "shell"}:
        return ScriptLanguage.SHELL
    return ScriptLanguage.UNKNOWN


def build_sample_result(sample: dict[str, Any], report: SafetyReport) -> dict[str, Any]:
    """Build the report entry for one sample without including full source text."""

    expected_decision = _string_field(sample, "expected_decision")
    expected_rules = list(sample.get("expected_rules") or [])
    actual_rules = [finding.rule_id for finding in report.findings]
    return {
        "sample_id": _string_field(sample, "id"),
        "description": _string_field(sample, "description"),
        "expected_decision": expected_decision,
        "expected_rules": expected_rules,
        "match": expected_decision == report.decision.value and _is_subset(expected_rules, actual_rules),
        "report": report.model_dump(mode="json"),
    }


def build_aggregate_report(
    *,
    policy_name: str,
    results: list[dict[str, Any]],
    generated_at: str = FIXTURE_GENERATED_AT,
) -> dict[str, Any]:
    """Build the stable public JSON report shape."""

    decisions = Counter(str(result["report"]["decision"]) for result in results)
    return {
        "policy_name": policy_name,
        "generated_at": generated_at,
        "sample_count": len(results),
        "decision_summary": {
            decision: decisions.get(decision, 0)
            for decision in DECISION_VALUES
        },
        "results": results,
    }


def build_audit_event(sample_id: str, report: SafetyReport, *, generated_at: str) -> SafetyAuditEvent:
    """Build a stable audit event for sample/file scans."""

    event = build_safety_audit_event(
        report,
        tool_name=TOOL_NAME,
        function_call_id=sample_id,
    )
    return event.model_copy(update={"timestamp": generated_at})


def write_json_report(report: dict[str, Any], path: str | Path) -> None:
    """Write aggregate report JSON with stable formatting."""

    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def write_audit_log(events: list[SafetyAuditEvent], path: str | Path) -> None:
    """Write one audit event per JSONL line."""

    audit_path = Path(path)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(event.model_dump_json() + "\n" for event in events)
    audit_path.write_text(text, encoding="utf-8")


def format_mismatches(mismatches: list[str]) -> str:
    """Format validation mismatch lines for CLI output."""

    if not mismatches:
        return ""
    return "\n".join(mismatches)


def _string_field(item: dict[str, Any], key: str) -> str:
    value = item.get(key, "")
    return value if isinstance(value, str) else ""


def _is_subset(expected_rules: list[str], actual_rules: list[str]) -> bool:
    actual = set(actual_rules)
    return all(rule in actual for rule in expected_rules)


def _mismatch_message(result: dict[str, Any]) -> str:
    report = result["report"]
    actual_rules = [finding["rule_id"] for finding in report.get("findings", [])]
    return (f"{result['sample_id']}: expected decision={result['expected_decision']} "
            f"rules={result['expected_rules']}, got decision={report['decision']} rules={actual_rules}")
