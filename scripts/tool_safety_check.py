# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Standalone CLI for scanning tool script samples or files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from trpc_agent_sdk.tools.safety._cli_helpers import format_mismatches
    from trpc_agent_sdk.tools.safety._cli_helpers import load_policy
    from trpc_agent_sdk.tools.safety._cli_helpers import load_samples
    from trpc_agent_sdk.tools.safety._cli_helpers import scan_file
    from trpc_agent_sdk.tools.safety._cli_helpers import scan_samples
    from trpc_agent_sdk.tools.safety._cli_helpers import write_audit_log
    from trpc_agent_sdk.tools.safety._cli_helpers import write_json_report
except ModuleNotFoundError:  # pragma: no cover - exercised by direct script execution in fresh checkouts.
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    from trpc_agent_sdk.tools.safety._cli_helpers import format_mismatches
    from trpc_agent_sdk.tools.safety._cli_helpers import load_policy
    from trpc_agent_sdk.tools.safety._cli_helpers import load_samples
    from trpc_agent_sdk.tools.safety._cli_helpers import scan_file
    from trpc_agent_sdk.tools.safety._cli_helpers import scan_samples
    from trpc_agent_sdk.tools.safety._cli_helpers import write_audit_log
    from trpc_agent_sdk.tools.safety._cli_helpers import write_json_report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan tool script samples or a single file with the tRPC-Agent safety guard.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--samples", type=Path, help="YAML file containing sample scan cases.")
    source.add_argument("--file", type=Path, help="Single script file to scan.")
    parser.add_argument("--language", choices=["python", "bash", "shell", "unknown"], default="unknown")
    parser.add_argument("--policy", type=Path, help="Safety policy YAML file.")
    parser.add_argument("--report-out", type=Path, help="Write aggregate JSON report to this path.")
    parser.add_argument("--audit-out", type=Path, help="Write JSONL audit events to this path.")
    parser.add_argument("--no-verify", action="store_true", help="Skip expected decision/rule checks in samples mode.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    policy = load_policy(args.policy)

    if args.samples is not None:
        report, audit_events, mismatches = scan_samples(load_samples(args.samples), policy)
        if args.no_verify:
            mismatches = []
    else:
        report, audit_events = scan_file(args.file, policy, language=args.language)
        mismatches = []

    if args.report_out is not None:
        write_json_report(report, args.report_out)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.audit_out is not None:
        write_audit_log(audit_events, args.audit_out)

    if mismatches:
        print(format_mismatches(mismatches), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
