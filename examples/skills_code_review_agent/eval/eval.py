# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Scoring harness: run every annotated fixture, output a confusion matrix.

Works on any directory of fixtures (``<dir>/<name>/input.diff`` +
``expected.json``), so a reviewer can point it at their own hidden sample
set unchanged::

    python3 run_agent.py eval --samples fixtures/ --unsafe-local

Matching semantics:
* an ``expected_findings`` entry is a TP when some *reported* finding has the
  same category & file and a line within the given int / [lo, hi] range
  (findings landing in warnings/needs_human_review do NOT count — that is
  the whole point of the triage gate); unmatched entries are FN;
* ``expected_warnings`` entries may match in any bucket (findings included);
* every reported finding not matched by any expectation and hitting a
  ``forbidden`` entry (or any finding for ``expect_clean`` fixtures) is a FP.

High-severity detection rate is additionally reported over the subset of
expectations with min_severity in {critical, high} (the acceptance metric).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from review_agent.diff_parser import parse_diff_file  # noqa: E402
from review_agent.pipeline import ReviewOptions, run_review  # noqa: E402

SEV_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}


def _line_matches(expected_line, actual_line: int) -> bool:
    if expected_line is None:
        return True
    if isinstance(expected_line, list) and len(expected_line) == 2:
        return expected_line[0] <= actual_line <= expected_line[1]
    try:
        return abs(int(expected_line) - actual_line) <= 1
    except (TypeError, ValueError):
        return True


def _entry_matches(entry: dict, finding: dict) -> bool:
    if entry.get("category") and entry["category"] != finding.get("category"):
        return False
    if entry.get("file") and entry["file"] != finding.get("file"):
        return False
    if not _line_matches(entry.get("line"), int(finding.get("line") or 0)):
        return False
    min_severity = entry.get("min_severity")
    if min_severity and SEV_RANK.get(finding.get("severity"), 0) < SEV_RANK.get(min_severity, 0):
        return False
    return True


def score_fixture(expected: dict, payload: dict) -> dict:
    """Score one report against one expected.json."""
    reported = payload.get("findings", [])
    soft_buckets = reported + payload.get("warnings", []) + payload.get("needs_human_review", [])

    tp, fn = [], []
    matched_ids: set[int] = set()
    for entry in expected.get("expected_findings", []) or []:
        hit = next((finding for finding in reported if _entry_matches(entry, finding)), None)
        if hit is not None:
            tp.append(entry)
            matched_ids.add(id(hit))
        else:
            fn.append(entry)

    soft_tp, soft_fn = [], []
    for entry in expected.get("expected_warnings", []) or []:
        hit = next((finding for finding in soft_buckets if _entry_matches(entry, finding)), None)
        if hit is not None:
            soft_tp.append(entry)
            matched_ids.add(id(hit))
        else:
            soft_fn.append(entry)

    fp = []
    forbidden = expected.get("forbidden", []) or []
    for finding in reported:
        if id(finding) in matched_ids:
            continue
        if expected.get("expect_clean"):
            fp.append(finding)
            continue
        for rule in forbidden:
            if _entry_matches({k: v for k, v in rule.items() if k in ("category", "file")}, finding) \
                    or (not rule.get("category") and rule.get("file") == finding.get("file")):
                fp.append(finding)
                break
    return {"tp": tp, "fn": fn, "fp": fp, "soft_tp": soft_tp, "soft_fn": soft_fn, "reported": len(reported)}


def _is_high(entry: dict) -> bool:
    return entry.get("min_severity") in ("critical", "high")


def run_eval(samples_dir: str, db_url: str = "sqlite:///eval.db", unsafe_local: bool = False) -> int:
    samples = Path(samples_dir)
    rows = []
    totals = {"tp": 0, "fn": 0, "fp": 0, "soft_tp": 0, "soft_fn": 0, "reported": 0, "high_tp": 0, "high_fn": 0}

    for fixture in sorted(path for path in samples.iterdir() if path.is_dir()):
        diff = fixture / "input.diff"
        expected_path = fixture / "expected.json"
        if not diff.is_file() or not expected_path.is_file():
            continue
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        meta = expected.get("meta") or {}

        options = ReviewOptions(
            db_url=db_url,
            output_dir=str(fixture / ".eval_out"),
            unsafe_local=unsafe_local,
            dry_run=True,  # eval scores the static channel: it must stand alone
            run_timeout=int(meta.get("run_timeout", 60)),
            inject_sleep=float(meta.get("inject_sleep", 0)),
        )
        repo_dir = fixture / "repo"
        parsed = parse_diff_file(str(diff), repo_path=str(repo_dir) if repo_dir.is_dir() else None)
        parsed.input_type = "fixture"
        parsed.input_ref = fixture.name
        outcome = asyncio.run(run_review(parsed, options))

        score = score_fixture(expected, outcome.payload)
        for key in ("tp", "fn", "fp", "soft_tp", "soft_fn"):
            totals[key] += len(score[key])
        totals["reported"] += score["reported"]
        totals["high_tp"] += sum(1 for entry in score["tp"] if _is_high(entry))
        totals["high_fn"] += sum(1 for entry in score["fn"] if _is_high(entry))
        rows.append((fixture.name, outcome.status, score))

    print(f"{'fixture':<22} {'status':<10} {'TP':>3} {'FN':>3} {'FP':>3} {'softTP':>6} {'softFN':>6}")
    for name, status, score in rows:
        print(f"{name:<22} {status:<10} {len(score['tp']):>3} {len(score['fn']):>3} {len(score['fp']):>3} "
              f"{len(score['soft_tp']):>6} {len(score['soft_fn']):>6}")
        for entry in score["fn"]:
            print(f"    MISS: {entry}")
        for finding in score["fp"]:
            print(f"    FP:   {finding.get('category')}/{finding.get('rule_id')} "
                  f"{finding.get('file')}:{finding.get('line')}")

    tp, fn, fp = totals["tp"], totals["fn"], totals["fp"]
    precision_hits = totals["reported"]
    high_total = totals["high_tp"] + totals["high_fn"]
    print("\n== summary (static channel, dry-run) ==")
    print(f"expected findings: recall {tp}/{tp + fn}"
          f" = {tp / (tp + fn) * 100 if tp + fn else 100:.1f}%")
    if high_total:
        print(f"high-severity recall: {totals['high_tp']}/{high_total}"
              f" = {totals['high_tp'] / high_total * 100:.1f}%  (acceptance: >= 80%)")
    fp_rate = fp / precision_hits * 100 if precision_hits else 0.0
    print(f"false positives: {fp}/{precision_hits} reported = {fp_rate:.1f}%  (acceptance: <= 15%)")
    print(f"soft expectations (warnings bucket): {totals['soft_tp']}/{totals['soft_tp'] + totals['soft_fn']}")
    ok = (totals["high_tp"] >= high_total * 0.8 if high_total else True) and fp_rate <= 15.0
    print(f"result: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", default=str(BASE_DIR / "fixtures"))
    parser.add_argument("--db", default="sqlite:///eval.db")
    parser.add_argument("--unsafe-local", action="store_true")
    args = parser.parse_args()
    sys.exit(run_eval(args.samples, args.db, args.unsafe_local))
