"""Parse diffs for the code review skill."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    """Build CLI arguments for the diff parser script."""

    parser = argparse.ArgumentParser(description="Parse a diff file for review skill diagnostics.")
    parser.add_argument("--diff-file", required=True, help="Path to the diff file.")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""

    return build_parser().parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Summarize diff shape for sandboxed skill diagnostics."""

    args = parse_args(argv)
    diff_path = Path(args.diff_file).expanduser().resolve()
    diff_text = diff_path.read_text(encoding="utf-8")
    payload = {
        "diff_file": str(diff_path),
        "line_count": len(diff_text.splitlines()),
        "file_count": diff_text.count("diff --git "),
        "has_security_keywords": any(
            token in diff_text
            for token in ("eval(", "shell=True", "pickle.loads(", "yaml.load(")
        ),
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
