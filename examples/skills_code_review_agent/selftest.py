# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Scored acceptance harness (issue #92, criterion 1 & the 80/15 metrics).

Default: run every fixture in ``fixtures/diffs`` through the deterministic pipeline, compare active
findings to the gold labels in ``fixtures/expected/labels.json``, and print detection / false-positive
rate. This public set is what the rule/severity policy is tuned against.

``--holdout``: score the *held-out* danger/safe set in ``fixtures/holdout`` (paired danger/safe cases
using patterns the detectors were NOT tuned on) — independent evidence for criterion 2's hidden-sample
detection >= 80% / false-positive <= 15%. A danger case counts as detected when its category is
surfaced at any tier (active / warning / needs-human-review); a safe case is a false positive when its
paired category is surfaced. Exit code is non-zero if the thresholds aren't met (usable in CI).
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


def score_holdout(runtime: str = "local") -> tuple[float, float, list]:
    """Score the held-out danger/safe set: return (detection_rate, fp_rate, rows).

    detected/false-positive keys on whether the paired category is surfaced at any non-duplicate tier.
    """
    labels = json.loads((HERE / "fixtures" / "expected" / "holdout_labels.json").read_text())
    d_hit = d_tot = fp = safe_tot = 0
    rows: list = []
    for name, spec in sorted(labels.items()):
        result = run_review(diff_text=(HERE / "fixtures" / "holdout" / name).read_text(), runtime=runtime)
        surfaced = {f.category for f in result.findings if f.status != "duplicate"}
        hit = spec["category"] in surfaced
        if spec["kind"] == "danger":
            d_tot += 1
            d_hit += hit
        else:
            safe_tot += 1
            fp += hit
        rows.append((name, spec["kind"], spec["category"], hit, sorted(surfaced)))
    return d_hit / max(1, d_tot), fp / max(1, safe_tot), rows


def _run_holdout() -> int:
    detection, fp_rate, rows = score_holdout(runtime="local")
    print(f"{'held-out case':24} {'kind':7} {'category':15} {'result':10} surfaced")
    print("-" * 78)
    for name, kind, cat, hit, surfaced in rows:
        verdict = ("DETECTED" if hit else "MISSED") if kind == "danger" else ("FALSE-POS" if hit else "clean")
        print(f"{name:24} {kind:7} {cat:15} {verdict:10} {surfaced}")
    print("-" * 78)
    print(f"detection rate: {detection:.1%} (target >= {DETECTION_TARGET:.0%})")
    print(f"false-positive rate: {fp_rate:.1%} (target <= {FP_TARGET:.0%})")
    ok = detection >= DETECTION_TARGET and fp_rate <= FP_TARGET
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


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
    sys.exit(_run_holdout() if "--holdout" in sys.argv[1:] else main())
