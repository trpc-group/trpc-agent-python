"""Run sandboxed tests for the code review skill."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    """Build CLI arguments for the test simulation script."""

    parser = argparse.ArgumentParser(description="Simulate test execution for a diff file.")
    parser.add_argument("--diff-file", required=True, help="Path to the diff file.")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""

    return build_parser().parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Emit a deterministic test summary based on changed paths."""

    args = parse_args(argv)
    diff_path = Path(args.diff_file).expanduser().resolve()
    diff_text = diff_path.read_text(encoding="utf-8")
    changed_test_files = [
        line.split(" b/", maxsplit=1)[1]
        for line in diff_text.splitlines()
        if line.startswith("diff --git ") and "/tests/" in line
    ]
    payload = {
        "diff_file": str(diff_path),
        "changed_test_files": changed_test_files,
        "test_update_present": bool(changed_test_files),
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
