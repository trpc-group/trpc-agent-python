"""Run sandboxed lint or static checks for the code review skill."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    """Build CLI arguments for the linter simulation script."""

    parser = argparse.ArgumentParser(description="Run deterministic lint checks on a diff file.")
    parser.add_argument("--diff-file", required=True, help="Path to the diff file.")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""

    return build_parser().parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run deterministic lint-style checks over the diff content."""

    args = parse_args(argv)
    diff_path = Path(args.diff_file).expanduser().resolve()
    diff_text = diff_path.read_text(encoding="utf-8")

    if "TODO_FAIL_SANDBOX" in diff_text:
        print("Simulated linter failure requested by fixture marker.", file=sys.stderr)
        return 2

    warnings: list[str] = []
    if "eval(" in diff_text:
        warnings.append("Security-sensitive call detected: eval")
    if "shell=True" in diff_text:
        warnings.append("Shell execution enabled in subprocess call")
    if "verify=False" in diff_text:
        warnings.append("TLS verification disabled")

    payload = {
        "diff_file": str(diff_path),
        "warning_count": len(warnings),
        "warnings": warnings,
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
