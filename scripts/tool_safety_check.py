#!/usr/bin/env python3
"""Scan a Python file or Bash command and print a JSON safety report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the source checkout runnable without requiring an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trpc_agent_sdk.tools.safety import ToolSafetyRequest
from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path, help="Python or shell script to scan")
    source.add_argument("--command", help="Shell command to scan")
    parser.add_argument("--policy", type=Path, default=Path("tool_safety_policy.yaml"))
    parser.add_argument("--tool-name", default="script_check")
    parser.add_argument("--language", choices=("auto", "python", "bash", "shell"), default="auto")
    args = parser.parse_args()

    script = args.file.read_text(encoding="utf-8") if args.file else args.command
    language = args.language
    if language == "auto" and args.file:
        language = "python" if args.file.suffix == ".py" else "bash"
    scanner = ToolScriptSafetyScanner.from_policy_file(args.policy)
    report = scanner.scan(ToolSafetyRequest(tool_name=args.tool_name, script=script, language=language))
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if report.decision.value == "allow" else 2


if __name__ == "__main__":
    raise SystemExit(main())
