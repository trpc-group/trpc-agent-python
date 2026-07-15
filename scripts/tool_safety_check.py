"""Tool Script Safety Guard CLI.

Usage::

    python scripts/tool_safety_check.py \\
        --policy trpc_agent_sdk/tools/safety/examples/tool_safety_policy.yaml \\
        --language python \\
        --script-file path/to/script.py \\
        --tool-name demo

Exit codes follow the plan:
* ``0`` -- decision was ``allow``.
* ``2`` -- decision was ``deny``.
* ``3`` -- decision was ``needs_human_review``.
* ``4`` -- input/policy/CLI error.

Use ``--manifest <manifest.yaml>`` to scan a batch of samples declared in
YAML. ``--request-json`` accepts a complete JSON request document.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

# Ensure the repo root is on sys.path so ``import trpc_agent_sdk.tools.safety`` works
# when the CLI is invoked directly via ``python scripts/...``.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from trpc_agent_sdk.tools.safety._audit import InMemoryAuditSink, JsonlAuditSink  # noqa: E402
from trpc_agent_sdk.tools.safety._exceptions import SafetyAuditError  # noqa: E402
from trpc_agent_sdk.tools.safety._guard import ToolSafetyGuard  # noqa: E402
from trpc_agent_sdk.tools.safety._models import (  # noqa: E402
    SafetyDecision,
    SafetyReport,
    SafetyScanRequest,
    ScriptLanguage,
    ToolKind,
)
from trpc_agent_sdk.tools.safety._policy import load_safety_policy  # noqa: E402
from trpc_agent_sdk.tools.safety._telemetry import build_audit_event  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        policy = load_safety_policy(args.policy)
    except Exception as exc:
        print(f"policy error: {exc}", file=sys.stderr)
        return 4
    guard = ToolSafetyGuard(policy)
    audit_sink = _resolve_audit_sink(args, policy)
    try:
        if args.manifest:
            return _run_manifest(guard, audit_sink, args)
        if args.request_json:
            return _run_request_json(guard, audit_sink, args)
        return _run_single(guard, audit_sink, args)
    except SafetyAuditError as exc:
        print(f"audit error: {exc}", file=sys.stderr)
        return 4


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tool_safety_check",
        description="Pre-execution static safety scanner for Python and Bash scripts.",
    )
    parser.add_argument("--policy", required=True,
                        help="Path to tool_safety_policy.yaml")
    parser.add_argument("--tool-name", default="cli",
                        help="Tool name to record in audit events")
    parser.add_argument("--tool-kind", default="unknown",
                        choices=[k.value for k in ToolKind],
                        help="Tool kind for metadata")
    parser.add_argument("--output",
                        help="Write JSON report to this path")
    parser.add_argument("--audit-file",
                        help="Append audit JSONL to this path")
    parser.add_argument("--manifest",
                        help="YAML manifest declaring multiple samples")
    parser.add_argument("--manifest-output",
                        help="Write manifest reports as a JSON array here")
    parser.add_argument("--request-json",
                        help="Inline JSON request document")
    # Single-file inputs
    parser.add_argument("--language",
                        choices=[l.value for l in ScriptLanguage],
                        help="Script language")
    parser.add_argument("--script-file",
                        help="Path to a script file")
    parser.add_argument("--script",
                        help="Inline script text")
    parser.add_argument("--cwd", help="Working directory value")
    parser.add_argument("--argv", nargs="*", default=[],
                        help="argv tokens")
    parser.add_argument("--env", nargs="*", default=[],
                        help="KEY=VALUE environment entries")
    parser.add_argument("--timeout", type=float,
                        help="Requested timeout seconds")
    return parser


def _run_single(guard: ToolSafetyGuard,
                audit_sink: Any,
                args: argparse.Namespace) -> int:
    try:
        request = _build_request(args)
    except Exception as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 4
    return _emit(guard, audit_sink, args, request)


def _run_request_json(guard: ToolSafetyGuard,
                      audit_sink: Any,
                      args: argparse.Namespace) -> int:
    try:
        data = json.loads(args.request_json)
        request = SafetyScanRequest.model_validate(data)
    except Exception as exc:
        print(f"request-json error: {exc}", file=sys.stderr)
        return 4
    return _emit(guard, audit_sink, args, request)


def _run_manifest(guard: ToolSafetyGuard,
                  audit_sink: Any,
                  args: argparse.Namespace) -> int:
    try:
        with open(args.manifest, "r", encoding="utf-8") as handle:
            manifest = yaml.safe_load(handle)
    except Exception as exc:
        print(f"manifest error: {exc}", file=sys.stderr)
        return 4
    if not isinstance(manifest, dict) or "samples" not in manifest:
        print("manifest must be a mapping with a 'samples' list",
              file=sys.stderr)
        return 4
    base = Path(args.manifest).resolve().parent
    reports = []
    exit_code = 0
    for sample in manifest["samples"]:
        try:
            request, expected = _build_sample_request(sample, base, args)
        except Exception as exc:
            print(f"sample {sample.get('name', '<unknown>')!r} error: {exc}",
                  file=sys.stderr)
            exit_code = max(exit_code, 4)
            continue
        report = guard.scan(request)
        reports.append({
            "name": sample.get("name"),
            "expected_decision": expected,
            "actual_decision": report.decision.value,
            "rule_ids": list(report.rule_ids),
            "report_id": report.report_id,
            "risk_level": report.risk_level.label(),
            "duration_ms": report.scan_duration_ms,
            "matches_expected": _decision_matches(report, expected),
        })
        blocked = report.decision != SafetyDecision.ALLOW
        asyncio.run(_emit_audit(
            audit_sink,
            report,
            request,
            blocked=blocked,
            required=guard.policy.audit.required,
        ))
        exit_code = max(exit_code, _exit_for_decision(report.decision))
    if args.manifest_output:
        with open(args.manifest_output, "w", encoding="utf-8") as handle:
            json.dump(reports, handle, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(reports, handle, indent=2, ensure_ascii=False)
    print(json.dumps({"summary": _summarize_manifest(reports)}, indent=2))
    return exit_code


def _emit(guard: ToolSafetyGuard,
          audit_sink: Any,
          args: argparse.Namespace,
          request: SafetyScanRequest) -> int:
    report = guard.scan(request)
    asyncio.run(_emit_audit(
        audit_sink,
        report,
        request,
        blocked=report.decision != SafetyDecision.ALLOW,
        required=guard.policy.audit.required,
    ))
    payload = report.model_dump_json(indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload)
    print(payload)
    return _exit_for_decision(report.decision)


async def _emit_audit(audit_sink: Any, report: SafetyReport,
                      request: SafetyScanRequest, *, blocked: bool,
                      required: bool) -> None:
    import datetime as _dt
    event = build_audit_event(
        report=report,
        tool_name=request.tool_name,
        tool_kind=request.tool_kind,
        execution_blocked=blocked,
        timestamp=_dt.datetime.now(_dt.timezone.utc).isoformat(),
    )
    try:
        await audit_sink.emit(event)
    except Exception as exc:
        print(f"audit emit warning: {exc}", file=sys.stderr)
        if required:
            if isinstance(exc, SafetyAuditError):
                raise
            raise SafetyAuditError("unexpected audit emit failure") from exc


def _build_request(args: argparse.Namespace) -> SafetyScanRequest:
    language = ScriptLanguage(args.language) if args.language \
        else _infer_language(args.script_file, args.script)
    script = ""
    if args.script_file:
        with open(args.script_file, "r", encoding="utf-8") as handle:
            script = handle.read()
    elif args.script:
        script = args.script
    env: dict[str, str] = {}
    for entry in args.env or []:
        if "=" not in entry:
            raise ValueError(f"env entries must be KEY=VALUE; got {entry!r}")
        key, value = entry.split("=", 1)
        env[key] = value
    return SafetyScanRequest(
        tool_name=args.tool_name,
        tool_kind=ToolKind(args.tool_kind),
        language=language,
        script=script,
        argv=tuple(args.argv or ()),
        cwd=args.cwd,
        env=env,
        requested_timeout_seconds=args.timeout,
    )


def _build_sample_request(
    sample: Mapping[str, Any],
    base: Path,
    args: argparse.Namespace,
) -> tuple[SafetyScanRequest, str]:
    name = sample.get("name") or sample.get("file") or "<unnamed>"
    language = ScriptLanguage(sample.get("language", "unknown"))
    file_value = sample.get("file")
    script = sample.get("script", "")
    if file_value:
        full = (base / file_value).resolve()
        with open(full, "r", encoding="utf-8") as handle:
            script = handle.read()
    env: dict[str, str] = {}
    for entry in sample.get("env") or []:
        if "=" in entry:
            key, value = entry.split("=", 1)
            env[key] = value
    request = SafetyScanRequest(
        tool_name=args.tool_name or sample.get("tool_name", "manifest"),
        tool_kind=ToolKind(sample.get("tool_kind", "unknown")),
        language=language,
        script=script,
        argv=tuple(sample.get("argv", [])),
        cwd=sample.get("cwd"),
        env=env,
        requested_timeout_seconds=sample.get("timeout"),
    )
    expected = sample.get("expected_decision", "allow")
    return request, expected


def _resolve_audit_sink(args: argparse.Namespace,
                        policy: Any) -> Any:
    path = args.audit_file or (policy.audit.path if policy.audit.enabled
                                else None)
    if path:
        return JsonlAuditSink(path)
    return InMemoryAuditSink()


def _infer_language(script_file: str | None,
                    inline: str | None) -> ScriptLanguage:
    if script_file:
        lower = script_file.lower()
        if lower.endswith((".py", ".python")):
            return ScriptLanguage.PYTHON
        if lower.endswith((".sh", ".bash", ".zsh")):
            return ScriptLanguage.BASH
    if inline is not None:
        stripped = inline.lstrip()
        if stripped.startswith(("#!/", "import ", "from ", "def ", "class ")):
            return ScriptLanguage.PYTHON
    return ScriptLanguage.UNKNOWN


def _decision_matches(report: SafetyReport, expected: str) -> bool:
    expected_norm = expected.lower().strip()
    if expected_norm == "allow":
        return report.decision == SafetyDecision.ALLOW
    if expected_norm == "deny":
        return report.decision == SafetyDecision.DENY
    if expected_norm in ("review", "needs_human_review", "needs-review"):
        return report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    return False


def _exit_for_decision(decision: SafetyDecision) -> int:
    if decision == SafetyDecision.ALLOW:
        return 0
    if decision == SafetyDecision.DENY:
        return 2
    return 3


def _summarize_manifest(reports: list[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(reports)
    matches = sum(1 for r in reports if r.get("matches_expected"))
    by_decision: dict[str, int] = {}
    for r in reports:
        decision = r.get("actual_decision", "unknown")
        by_decision[decision] = by_decision.get(decision, 0) + 1
    return {
        "total": total,
        "matches_expected": matches,
        "by_decision": by_decision,
    }


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
