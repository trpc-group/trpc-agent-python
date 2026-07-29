# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Driver script: run every rule check once and emit a single findings JSON.

Executed inside the sandbox via ``skill_run`` as::

    python3 scripts/run_checks.py

Input (first match wins):
  1. ``$WORK_DIR/inputs/review_input.json`` — pre-parsed by the host
     (see the schema in the repository README);
  2. any ``$WORK_DIR/inputs/*.diff`` / ``*.patch`` — parsed on the fly with
     ``parse_diff.py`` so the skill also works without the host pipeline.

Output: ``$OUTPUT_DIR/findings.json`` (collected automatically by skill_run).
stdout stays tiny on purpose: skill_run truncates it at 16 KB, the file
channel is the real transport.

A crashing check must never kill the run: each check is isolated and its
error is recorded in ``stats.errors``.
"""

from __future__ import annotations

import glob
import importlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import parse_diff  # noqa: E402
from checks.common import FileCtx, MODE_DIFF_ONLY, language_for_path  # noqa: E402

CHECK_MODULES = (
    "checks.check_security",
    "checks.check_secrets",
    "checks.check_async",
    "checks.check_resource_leak",
    "checks.check_db_lifecycle",
    "checks.check_missing_tests",
)

SCHEMA_VERSION = 1


def _load_review_input() -> tuple[dict, str]:
    """Locate and load the review input.  Returns (payload, source_desc)."""
    work_dir = os.environ.get("WORK_DIR", "work")
    inputs_dir = os.path.join(work_dir, "inputs")

    pre_parsed = os.path.join(inputs_dir, "review_input.json")
    if os.path.isfile(pre_parsed):
        with open(pre_parsed, "r", encoding="utf-8") as fh:
            return json.load(fh), "review_input.json"

    for pattern in ("*.diff", "*.patch"):
        for path in sorted(glob.glob(os.path.join(inputs_dir, pattern))):
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                parsed = parse_diff.parse_unified_diff(fh.read())
            files = []
            for entry in parsed["files"]:
                content, complete = parse_diff.reconstruct_post_image(entry)
                files.append({
                    "path": entry["path"],
                    "change_type": entry["change_type"],
                    "old_path": entry.get("old_path"),
                    "language": language_for_path(entry["path"] or ""),
                    "candidate_lines": parse_diff.candidate_lines(entry),
                    "content": content,
                    "content_complete": complete,
                })
            payload = {
                "version": SCHEMA_VERSION,
                "mode": MODE_DIFF_ONLY,
                "task_id": "",
                "files": files,
                "parse_errors": parsed["errors"],
            }
            return payload, os.path.basename(path)

    raise FileNotFoundError(f"no review_input.json or *.diff under {inputs_dir}")


def _build_file_ctxs(payload: dict) -> list[FileCtx]:
    ctxs = []
    for f in payload.get("files", []):
        if not f.get("path"):
            continue
        ctxs.append(
            FileCtx(
                path=f["path"],
                change_type=f.get("change_type", "modified"),
                old_path=f.get("old_path"),
                language=f.get("language") or language_for_path(f["path"]),
                content=f.get("content"),
                candidate_lines=set(f.get("candidate_lines") or []),
                content_complete=bool(f.get("content_complete", True)),
            ))
    return ctxs


def main() -> int:
    started = time.monotonic()
    out_dir = os.environ.get("OUTPUT_DIR", "out")
    os.makedirs(out_dir, exist_ok=True)

    errors: list[dict] = []
    findings: list[dict] = []
    checks_run: list[str] = []

    try:
        payload, source = _load_review_input()
    except Exception as ex:  # pylint: disable=broad-except
        result = {
            "version": SCHEMA_VERSION,
            "engine": "static",
            "stats": {
                "files_scanned": 0,
                "checks_run": [],
                "errors": [{
                    "check": "input",
                    "error": str(ex)
                }],
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
            "findings": [],
        }
        with open(os.path.join(out_dir, "findings.json"), "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
        print(f"run_checks: input error: {ex}")
        return 1

    mode = payload.get("mode", MODE_DIFF_ONLY)
    ctxs = _build_file_ctxs(payload)
    for err in payload.get("parse_errors") or []:
        errors.append({"check": "parse_diff", "error": str(err)})

    # Fault-injection channel for fixture 07 (sandbox timeout): explicit,
    # host-controlled, hard-capped.  Chaos-testing the real timeout path is
    # more honest than a test-only branch in the pipeline.
    inject_sleep = float(payload.get("debug", {}).get("sleep_seconds") or 0)
    if inject_sleep > 0:
        time.sleep(min(inject_sleep, 30.0))

    context = {
        "repo_context": payload.get("repo_context") or {},
        "task_id": payload.get("task_id", ""),
    }

    for mod_name in CHECK_MODULES:
        short = mod_name.split(".")[-1].replace("check_", "")
        try:
            mod = importlib.import_module(mod_name)
            produced = mod.run(ctxs, mode, context) or []
            findings.extend(produced)
            checks_run.append(short)
        except Exception as ex:  # pylint: disable=broad-except
            errors.append({"check": short, "error": f"{type(ex).__name__}: {ex}"})

    result = {
        "version": SCHEMA_VERSION,
        "engine": "static",
        "stats": {
            "files_scanned": len(ctxs),
            "checks_run": checks_run,
            "errors": errors,
            "input_source": source,
            "mode": mode,
            "duration_ms": int((time.monotonic() - started) * 1000),
        },
        "findings": findings,
    }
    with open(os.path.join(out_dir, "findings.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    print(f"run_checks: {len(findings)} finding(s) from {len(ctxs)} file(s), "
          f"{len(checks_run)}/{len(CHECK_MODULES)} checks ok -> findings.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
