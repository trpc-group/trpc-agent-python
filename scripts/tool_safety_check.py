# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Scan Python and Bash files with the tRPC Agent tool safety policy."""

from __future__ import annotations

import argparse
from datetime import datetime
from datetime import timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any
from typing import Sequence

if __package__ in (None, ""):
    _REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
    if str(_REPOSITORY_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPOSITORY_ROOT))

from trpc_agent_sdk.tools.safety import JsonlAuditSink  # noqa: E402
from trpc_agent_sdk.tools.safety import SafetyAuditEvent  # noqa: E402
from trpc_agent_sdk.tools.safety import SafetyDecision  # noqa: E402
from trpc_agent_sdk.tools.safety import SafetyScanRequest  # noqa: E402
from trpc_agent_sdk.tools.safety import ScriptLanguage  # noqa: E402
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy  # noqa: E402
from trpc_agent_sdk.tools.safety import ToolSafetyScanner  # noqa: E402

_LANGUAGE_BY_SUFFIX = {
    ".bash": ScriptLanguage.BASH,
    ".py": ScriptLanguage.PYTHON,
    ".sh": ScriptLanguage.BASH,
}
_DECISION_RANK = {
    SafetyDecision.ALLOW: 0,
    SafetyDecision.NEEDS_HUMAN_REVIEW: 1,
    SafetyDecision.DENY: 2,
}
_RISK_RANK = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}
_EXIT_CODE = {
    SafetyDecision.ALLOW: 0,
    SafetyDecision.DENY: 1,
    SafetyDecision.NEEDS_HUMAN_REVIEW: 2,
}


class ToolSafetyCliError(ValueError):
    """Expected CLI input or file-system error with no script content."""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Statically scan Python and Bash tool scripts before execution.", )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="One or more .py/.sh/.bash files or directories (directories are recursive).",
    )
    parser.add_argument("--policy", type=Path, help="Strict YAML policy file.")
    parser.add_argument("--report", type=Path, help="Write the complete JSON report to this path.")
    parser.add_argument("--audit", type=Path, help="Append one redacted JSONL audit event per scanned file.")
    parser.add_argument(
        "--tool-name",
        default="tool_safety_check",
        help="Tool name recorded in reports and audit events.",
    )
    return parser


def _collect_files(input_paths: Sequence[Path]) -> list[Path]:
    files: dict[str, Path] = {}
    for input_path in input_paths:
        if not input_path.exists():
            raise ToolSafetyCliError(f"input path does not exist: {input_path}")
        if input_path.is_file():
            if input_path.suffix.lower() not in _LANGUAGE_BY_SUFFIX:
                raise ToolSafetyCliError(f"unsupported script extension: {input_path}")
            files.setdefault(str(input_path.resolve()), input_path)
            continue
        if not input_path.is_dir():
            raise ToolSafetyCliError(f"input path is not a regular file or directory: {input_path}")

        directory_files = [
            candidate for candidate in input_path.rglob("*")
            if candidate.is_file() and candidate.suffix.lower() in _LANGUAGE_BY_SUFFIX
        ]
        if not directory_files:
            raise ToolSafetyCliError(f"directory contains no supported scripts: {input_path}")
        for candidate in directory_files:
            files.setdefault(str(candidate.resolve()), candidate)

    return sorted(files.values(), key=lambda path: str(path.resolve()))


def _serialize_model(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _strictest_decision(reports: Sequence[Any]) -> SafetyDecision:
    return max((report.decision for report in reports), key=_DECISION_RANK.__getitem__)


def _highest_risk(reports: Sequence[Any]) -> str:
    values = [report.risk_level.value for report in reports]
    return max(values, key=_RISK_RANK.__getitem__)


def _build_audit_event(report: Any) -> SafetyAuditEvent:
    return SafetyAuditEvent(
        tool_name=report.tool_name,
        decision=report.decision,
        risk_level=report.risk_level,
        rule_id=report.rule_id,
        rule_ids=list(report.rule_ids),
        duration_ms=report.duration_ms,
        redacted=report.redacted,
        blocked=report.blocked,
        human_review_approved=report.human_review_approved,
        script_sha256=report.script_sha256,
        policy_version=report.policy_version,
    )


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                delete=False,
        ) as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
            temporary_path = Path(temporary_file.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _append_audit_events(path: Path, reports: Sequence[Any]) -> None:
    sink = JsonlAuditSink(path)
    for report in reports:
        sink.record(_build_audit_event(report))


def _error_payload(error: Exception) -> dict[str, Any]:
    if isinstance(error, ToolSafetyCliError):
        message = str(error)
    elif isinstance(error, ValueError):
        message = f"invalid safety configuration: {error}"
    else:
        message = f"tool safety scan failed ({type(error).__name__})"
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "decision": SafetyDecision.DENY.value,
        "risk_level": "high",
        "files_scanned": 0,
        "reports": [],
        "error": {
            "type": type(error).__name__,
            "message": message,
        },
    }


def _run(args: argparse.Namespace) -> tuple[dict[str, Any], list[Any]]:
    policy = ToolSafetyPolicy.from_yaml(args.policy) if args.policy else ToolSafetyPolicy()
    scanner = ToolSafetyScanner(policy)
    files = _collect_files(args.paths)
    report_entries = []
    reports = []

    for script_path in files:
        language = _LANGUAGE_BY_SUFFIX[script_path.suffix.lower()]
        try:
            script = script_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ToolSafetyCliError(f"unable to read script {script_path}: {type(exc).__name__}") from exc
        request = SafetyScanRequest(
            script=script,
            language=language,
            tool_name=args.tool_name,
            cwd=str(script_path.parent),
            metadata={"source_path": str(script_path)},
        )
        report = scanner.scan(request)
        reports.append(report)
        report_entries.append({
            "path": str(script_path),
            "report": _serialize_model(report),
        })

    decision = _strictest_decision(reports)
    payload = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": policy.version,
        "decision": decision.value,
        "risk_level": _highest_risk(reports),
        "files_scanned": len(reports),
        "reports": report_entries,
    }
    return payload, reports


def main(argv: Sequence[str] | None = None) -> int:
    """Run the scanner and return the strictest decision as a process code."""

    args = _build_parser().parse_args(argv)
    try:
        payload, reports = _run(args)
        serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.report:
            _write_text_atomic(args.report, serialized)
        if args.audit:
            _append_audit_events(args.audit, reports)
        sys.stdout.write(serialized)
        return _EXIT_CODE[SafetyDecision(payload["decision"])]
    except Exception as exc:  # pylint: disable=broad-except
        payload = _error_payload(exc)
        serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.report:
            try:
                _write_text_atomic(args.report, serialized)
            except OSError:
                pass
        sys.stdout.write(serialized)
        return _EXIT_CODE[SafetyDecision.DENY]


if __name__ == "__main__":
    raise SystemExit(main())
