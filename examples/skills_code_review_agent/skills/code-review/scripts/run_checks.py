# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Sandbox entry: run all review rules over a parsed changeset.

Usage (inside the code-review skill workspace)::

    python3 skills/code-review/scripts/run_checks.py <diff.json> <out/findings.json> \
        [--files-dir work/inputs/files] [--force-fail]

``<diff.json>`` is either the ``parse_diff.py`` output (``{"changeset": ...}``)
or a bare changeset (``{"files": [...]}``). ``--files-dir`` optionally points
at a directory tree with full new-file contents for higher-accuracy checks.
``--force-fail`` deterministically raises — used by tests and the CLI flag
``--inject-sandbox-failure`` to prove that a sandbox crash never kills the
review task. Stdlib-only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.engine import run_all_rules  # noqa: E402


def _load_changeset(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    return payload.get("changeset", payload)


def _load_file_contents(files_dir: str) -> dict:
    contents = {}
    for root, _dirs, files in os.walk(files_dir):
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, files_dir).replace(os.sep, "/")
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as fh:
                    contents[rel] = fh.read()
            except OSError:
                continue
    return contents


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description="Run code-review rules over a parsed changeset.")
    parser.add_argument("diff_json", help="parsed changeset JSON (parse_diff.py output)")
    parser.add_argument("out_json", help="findings output path")
    parser.add_argument("--files-dir", default="", help="optional dir with full new-file contents")
    parser.add_argument("--force-fail", action="store_true",
                        help="raise deterministically (sandbox-failure test injection)")
    args = parser.parse_args(argv[1:])

    if args.force_fail:
        raise RuntimeError("forced sandbox failure (test injection)")

    changeset = _load_changeset(args.diff_json)
    file_contents = _load_file_contents(args.files_dir) if args.files_dir else {}
    findings = run_all_rules(changeset, file_contents)

    os.makedirs(os.path.dirname(os.path.abspath(args.out_json)), exist_ok=True)
    with open(args.out_json, "w", encoding="utf-8") as fh:
        json.dump({"findings": findings}, fh, ensure_ascii=False, indent=2)
    print(f"emitted {len(findings)} finding(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
