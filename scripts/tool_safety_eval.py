#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Evaluate Tool Script Safety Guard detection / false-positive rates.

Reads a manifest.yaml of the form::

    cases:
      - file: 02_dangerous_delete.sh
        expect: deny
        risk: high
      - file: 01_safe_python.py
        expect: allow

Prints detection rate, false-positive rate, and average scan duration.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def main(argv=None) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    parser = argparse.ArgumentParser(description="Evaluate tool safety detection rates")
    parser.add_argument("--samples", required=True, help="Samples directory")
    parser.add_argument("--manifest", required=True, help="manifest.yaml path")
    parser.add_argument(
        "--policy",
        default="examples/tool_safety/tool_safety_policy.yaml",
        help="Policy YAML path",
    )
    args = parser.parse_args(argv)

    import yaml
    from trpc_agent_sdk.safety import PolicyConfig
    from trpc_agent_sdk.safety import SafetyScanner
    from trpc_agent_sdk.safety import ScanInput

    policy = PolicyConfig.from_yaml(args.policy)
    scanner = SafetyScanner(policy=policy)
    samples_dir = Path(args.samples)
    manifest = yaml.safe_load(Path(args.manifest).read_text(encoding="utf-8")) or {}
    cases = manifest.get("cases") or manifest.get("samples") or []

    dangerous_total = 0
    dangerous_hit = 0
    safe_total = 0
    safe_fp = 0
    durations = []

    for case in cases:
        name = case.get("file") or case.get("name")
        expect = case.get("expect") or case.get("decision")
        path = samples_dir / name
        if not path.is_file():
            print(f"missing sample: {name}", file=sys.stderr)
            continue
        script = path.read_text(encoding="utf-8")
        lang = "python" if path.suffix == ".py" else "bash"
        t0 = time.perf_counter()
        report = scanner.scan(ScanInput(script=script, language=lang, tool_name=name))
        durations.append((time.perf_counter() - t0) * 1000)

        if expect == "allow":
            safe_total += 1
            if report.decision.value != "allow":
                safe_fp += 1
                print(f"FP  {name}: got {report.decision.value}")
        else:
            dangerous_total += 1
            # For needs_human_review expectations, accept exact match;
            # for deny, require deny.
            if expect == "needs_human_review":
                if report.decision.value in ("needs_human_review", "deny"):
                    dangerous_hit += 1
                else:
                    print(f"MISS {name}: got {report.decision.value}, expect {expect}")
            elif report.decision.value == expect:
                dangerous_hit += 1
            else:
                print(f"MISS {name}: got {report.decision.value}, expect {expect}")

    det = (dangerous_hit / dangerous_total) if dangerous_total else 1.0
    fpr = (safe_fp / safe_total) if safe_total else 0.0
    avg_ms = sum(durations) / len(durations) if durations else 0.0
    print(
        f"detection_rate={det:.1%} ({dangerous_hit}/{dangerous_total})  "
        f"false_positive_rate={fpr:.1%} ({safe_fp}/{safe_total})  "
        f"avg_scan_ms={avg_ms:.3f}"
    )
    if det < 0.9 or fpr > 0.1:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
