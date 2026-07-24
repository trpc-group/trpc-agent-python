# Copyright (C) 2026 Tencent. All rights reserved.
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Detection-rate / false-positive-rate eval against the sample manifest.

Satisfies issue #90 acceptance #2 (high-risk detection >= 90%, safe-sample
false-positive <= 10%) with a runnable, reproducible measurement.

A "positive" sample is one whose manifest expected_decision != ALLOW (it should
be flagged as risky); "detected" means the scan also returns != ALLOW. A
"negative" sample expects ALLOW; a false positive is one the scan blocks.

Usage:
    python scripts/tool_safety_eval.py [--manifest m.yaml] [--policy p.yaml]
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import NamedTuple

import yaml

from trpc_agent_sdk.tools.safety._policy import load_policy
from trpc_agent_sdk.tools.safety._scanner import scan

_DEFAULT_MANIFEST = (
    Path(__file__).resolve().parent.parent
    / "tests" / "tools" / "safety" / "samples" / "manifest.yaml"
)


class Outcome(NamedTuple):
    name: str
    expected: str
    actual: str
    correct: bool  # scan agrees with manifest on allow-vs-block


def _evaluate(manifest: Path, policy_path: str | None) -> list[Outcome]:
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    policy = load_policy(policy_path)
    out: list[Outcome] = []
    for s in data["samples"]:
        actual = scan(policy, s["script"], language=s["language"]).decision.name
        expected = s["expected_decision"]
        correct = (actual == "ALLOW") == (expected == "ALLOW")
        out.append(Outcome(s["name"], expected, actual, correct))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", default=str(_DEFAULT_MANIFEST))
    ap.add_argument("--policy", default=None)
    args = ap.parse_args()

    outcomes = _evaluate(Path(args.manifest), args.policy)
    positives = [o for o in outcomes if o.expected != "ALLOW"]
    negatives = [o for o in outcomes if o.expected == "ALLOW"]
    detected = [o for o in positives if o.actual != "ALLOW"]
    false_pos = [o for o in negatives if o.actual != "ALLOW"]

    det_rate = len(detected) / len(positives) if positives else 1.0
    fp_rate = len(false_pos) / len(negatives) if negatives else 0.0

    for o in outcomes:
        flag = "OK " if o.correct else "ERR"
        print(f"  [{flag}] {o.name:<24} expected={o.expected:<14} actual={o.actual}")
    print()
    print(f"positives={len(positives)} detected={len(detected)} "
          f"detection_rate={det_rate:.0%}")
    print(f"negatives={len(negatives)} false_pos={len(false_pos)} "
          f"false_positive_rate={fp_rate:.0%}")

    ok = det_rate >= 0.90 and fp_rate <= 0.10
    print("RESULT:", "PASS" if ok else "FAIL",
          "(acceptance #2: detection>=90%, false_positive<=10%)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
