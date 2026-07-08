"""Evaluate deterministic review rules against labeled fixture expectations."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

EXAMPLE_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXAMPLE_DIR.parents[1]
for import_path in (EXAMPLE_DIR, REPO_ROOT):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from agent.pipeline import run_review  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate code-review fixture precision and recall.")
    parser.add_argument("--expected-file", type=Path, default=EXAMPLE_DIR / "fixtures" / "expected_findings.json")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only.")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    expected = json.loads(args.expected_file.read_text(encoding="utf-8"))
    rows = []
    total_tp = total_fp = total_fn = 0
    with tempfile.TemporaryDirectory(prefix="cr-eval-") as tmp:
        tmp_path = Path(tmp)
        for fixture, expected_ids in sorted(expected.items()):
            report = await run_review(
                fixture=fixture,
                output_dir=tmp_path / fixture,
                db_path=tmp_path / "reviews.sqlite",
                sandbox="fake",
                dry_run=True,
            )
            actual = {item.rule_id for item in report.findings + report.warnings + report.needs_human_review}
            wanted = set(expected_ids)
            tp = len(actual & wanted)
            fp_items = sorted(actual - wanted)
            fn_items = sorted(wanted - actual)
            total_tp += tp
            total_fp += len(fp_items)
            total_fn += len(fn_items)
            rows.append({
                "fixture": fixture,
                "expected": sorted(wanted),
                "actual": sorted(actual),
                "true_positive": tp,
                "false_positive": fp_items,
                "false_negative": fn_items,
            })

    precision = total_tp / (total_tp + total_fp) if total_tp + total_fp else 1.0
    recall = total_tp / (total_tp + total_fn) if total_tp + total_fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    result = {
        "fixture_count": len(rows),
        "true_positive": total_tp,
        "false_positive": total_fp,
        "false_negative": total_fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "fixtures": rows,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if total_fn == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
