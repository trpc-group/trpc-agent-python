# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Scored acceptance harness over the public fixtures (issue #92, criterion 1 & the 80/15 metrics).

Runs every fixture in ``fixtures/diffs`` through the deterministic pipeline, compares active findings
to the gold labels in ``fixtures/expected/labels.json``, and prints detection-rate / false-positive-
rate. The hidden test set is invisible, so this proxy set is how the rule/severity policy is tuned
before claiming the thresholds. Exit code is non-zero if the thresholds aren't met (usable in CI).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from pipeline.engine import run_review

HERE = Path(__file__).parent
DETECTION_TARGET = 0.80
FP_TARGET = 0.15


def _match(expected: list[list], findings) -> tuple[int, int, int]:
    """Return (tp, fn, fp) for one fixture, matching on (line, category)."""
    want = {(ln, cat) for ln, cat in expected}
    got = {(f.line, f.category) for f in findings}
    tp = len(want & got)
    fn = len(want - got)
    fp = len(got - want)
    return tp, fn, fp


def main() -> int:
    labels = json.loads((HERE / "fixtures" / "expected" / "labels.json").read_text())
    tot_tp = tot_fn = tot_fp = tot_active = 0

    print(f"{'fixture':28} {'active':>6} {'tp':>3} {'fn':>3} {'fp':>3}")
    print("-" * 50)
    for name, spec in sorted(labels.items()):
        # Score through the sandbox (the default production path), not the in-process dev fast-path.
        result = run_review(diff_text=(HERE / "fixtures" / "diffs" / name).read_text(), runtime="local")
        findings = result.report.findings
        tp, fn, fp = _match(spec["expected"], findings)
        tot_tp += tp
        tot_fn += fn
        tot_fp += fp
        tot_active += len(findings)
        print(f"{name:28} {len(findings):>6} {tp:>3} {fn:>3} {fp:>3}")

    detection = tot_tp / max(1, tot_tp + tot_fn)
    fp_rate = tot_fp / max(1, tot_active)
    print("-" * 50)
    print(f"detection rate: {detection:.1%} (target >= {DETECTION_TARGET:.0%})")
    print(f"false-positive rate: {fp_rate:.1%} (target <= {FP_TARGET:.0%})")

    ok = detection >= DETECTION_TARGET and fp_rate <= FP_TARGET
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
