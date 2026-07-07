# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run the tool safety guard quickstart project."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from examples.tool_safety_guard.quickstart.agent.agent import run_quickstart  # noqa: E402
from examples.tool_safety_guard.quickstart.agent.config import DEFAULT_OUT  # noqa: E402
from examples.tool_safety_guard.quickstart.agent.config import DEFAULT_POLICY  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the tool safety guard quickstart.")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    summary = asyncio.run(run_quickstart(policy_path=args.policy.resolve(), output_dir=args.output_dir.resolve()))
    print("Tool safety quickstart decisions:")
    for case in summary["cases"]:
        rule_ids = ", ".join(case["rule_ids"]) or "no findings"
        print(f"- {case['name']}: {case['decision']} ({rule_ids})")
    print(f"Report written to: {summary['report']}")
    print(f"Audit log written to: {summary['audit_log']}")


if __name__ == "__main__":
    main()
