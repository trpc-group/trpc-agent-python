#!/usr/bin/env python3
"""Fixture evaluation framework — precision/recall/F1 per fixture.

Evaluates the code review pipeline against labeled fixture expectations.
Supports cross-validation by splitting fixtures into train/val sets.

Usage:
    python evaluate_fixtures.py --fixtures fixtures/diffs/ --expected fixtures/expected_findings.json
    python evaluate_fixtures.py --fixtures fixtures/diffs/ --expected fixtures/expected_findings.json --cross-validate
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline.config import load_config
from pipeline.diff_parser import parse_diff
from pipeline.filter_chain import FilterChain
from pipeline.scanners import run_scanners
from pipeline.dedup import deduplicate


@dataclass
class FixtureResult:
    """Evaluation result for a single fixture."""
    fixture_name: str
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    total_expected: int = 0
    total_found: int = 0
    elapsed_ms: int = 0


@dataclass
class EvaluationReport:
    """Aggregate evaluation across all fixtures."""
    results: list[FixtureResult] = field(default_factory=list)
    overall_precision: float = 0.0
    overall_recall: float = 0.0
    overall_f1: float = 0.0
    total_time_ms: int = 0
    fixtures_evaluated: int = 0
    fixtures_skipped: int = 0
    train_precision: float = 0.0
    train_recall: float = 0.0
    val_precision: float = 0.0
    val_recall: float = 0.0


def _fingerprint(finding: dict) -> str:
    """Create a comparison fingerprint for matching findings to expectations."""
    return f"{finding.get('file', '')}:{finding.get('line', 0)}:{finding.get('category', '')}"


def evaluate_fixture(
    diff_path: str,
    expected_findings: list[dict],
    cfg=None,
) -> FixtureResult:
    """Evaluate pipeline against a single fixture.

    Args:
        diff_path: Path to the fixture diff file.
        expected_findings: List of expected finding dicts.
        cfg: ReviewConfig (uses defaults if None).

    Returns:
        FixtureResult with precision/recall/F1.
    """
    if cfg is None:
        cfg = load_config()

    fixture_name = os.path.basename(diff_path).replace(".diff", "")
    start = time.monotonic()

    # Run pipeline
    with open(diff_path, "r", encoding="utf-8") as f:
        diff_text = f.read()

    files = parse_diff(diff_text)
    all_findings: list = []
    for df in files:
        if not df.is_binary:
            findings = run_scanners(df, enabled=cfg.enabled_scanners,
                                    min_confidence=cfg.min_confidence)
            all_findings.extend(findings)
    deduped = deduplicate(all_findings)

    elapsed_ms = int((time.monotonic() - start) * 1000)

    # Convert findings to comparable dictionaries
    found_dicts = [
        {
            "file": f.file,
            "line": f.line,
            "category": f.category.value,
            "title": f.title,
        }
        for f in deduped
    ]

    # Build fingerprint sets
    expected_fps = {_fingerprint(e) for e in expected_findings}
    found_fps = {_fingerprint(f) for f in found_dicts}

    tp = len(expected_fps & found_fps)
    fp = len(found_fps - expected_fps)
    fn = len(expected_fps - found_fps)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return FixtureResult(
        fixture_name=fixture_name,
        precision=precision,
        recall=recall,
        f1=f1,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        total_expected=len(expected_findings),
        total_found=len(found_dicts),
        elapsed_ms=elapsed_ms,
    )


def run_evaluation(
    fixtures_dir: str,
    expected_path: str,
    cross_validate: bool = False,
    train_split: float = 0.7,
) -> EvaluationReport:
    """Run evaluation across all fixtures.

    Args:
        fixtures_dir: Directory containing .diff fixture files.
        expected_path: JSON file mapping fixture names to expected findings.
        cross_validate: If True, split fixtures and compute train/val metrics.
        train_split: Fraction of fixtures to use for training (default 0.7).

    Returns:
        EvaluationReport with per-fixture and aggregate metrics.
    """
    with open(expected_path, "r", encoding="utf-8") as f:
        all_expected = json.load(f)

    cfg = load_config()
    results: list[FixtureResult] = []
    skipped = 0

    for fixture_file in sorted(os.listdir(fixtures_dir)):
        if not fixture_file.endswith(".diff"):
            continue

        fixture_name = fixture_file.replace(".diff", "")
        diff_path = os.path.join(fixtures_dir, fixture_file)

        expected = all_expected.get(fixture_name, [])
        if not expected and not cross_validate:
            skipped += 1
            continue

        result = evaluate_fixture(diff_path, expected, cfg)
        results.append(result)

        print(f"  {result.fixture_name:30s} "
              f"P={result.precision:.2f} R={result.recall:.2f} F1={result.f1:.2f} "
              f"(TP={result.true_positives} FP={result.false_positives} FN={result.false_negatives}) "
              f"[{result.elapsed_ms}ms]")

    # Aggregate
    total_tp = sum(r.true_positives for r in results)
    total_fp = sum(r.false_positives for r in results)
    total_fn = sum(r.false_negatives for r in results)

    overall_p = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    overall_r = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    overall_f1 = 2 * overall_p * overall_r / (overall_p + overall_r) if (overall_p + overall_r) > 0 else 0.0

    report = EvaluationReport(
        results=results,
        overall_precision=overall_p,
        overall_recall=overall_r,
        overall_f1=overall_f1,
        total_time_ms=sum(r.elapsed_ms for r in results),
        fixtures_evaluated=len(results),
        fixtures_skipped=skipped,
    )

    # Cross-validation metrics
    if cross_validate and len(results) >= 4:
        split_idx = max(1, int(len(results) * train_split))
        train_results = results[:split_idx]
        val_results = results[split_idx:]

        t_tp = sum(r.true_positives for r in train_results)
        t_fp = sum(r.false_positives for r in train_results)
        t_fn = sum(r.false_negatives for r in train_results)
        report.train_precision = t_tp / (t_tp + t_fp) if (t_tp + t_fp) > 0 else 0.0
        report.train_recall = t_tp / (t_tp + t_fn) if (t_tp + t_fn) > 0 else 0.0

        v_tp = sum(r.true_positives for r in val_results)
        v_fp = sum(r.false_positives for r in val_results)
        v_fn = sum(r.false_negatives for r in val_results)
        report.val_precision = v_tp / (v_tp + v_fp) if (v_tp + v_fp) > 0 else 0.0
        report.val_recall = v_tp / (v_tp + v_fn) if (v_tp + v_fn) > 0 else 0.0

    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate code review fixture accuracy (precision/recall/F1)",
    )
    parser.add_argument("--fixtures", default="fixtures/diffs",
                        help="Directory containing .diff fixture files")
    parser.add_argument("--expected", default="fixtures/expected_findings.json",
                        help="JSON file with expected findings per fixture")
    parser.add_argument("--cross-validate", action="store_true",
                        help="Compute train/val split metrics")
    parser.add_argument("--train-split", type=float, default=0.7,
                        help="Train split fraction (default: 0.7)")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")
    args = parser.parse_args()

    if not os.path.isdir(args.fixtures):
        print(f"Fixtures directory not found: {args.fixtures}", file=sys.stderr)
        return 1

    report = run_evaluation(
        args.fixtures, args.expected,
        cross_validate=args.cross_validate,
        train_split=args.train_split,
    )

    if args.json:
        output = {
            "overall": {
                "precision": round(report.overall_precision, 4),
                "recall": round(report.overall_recall, 4),
                "f1": round(report.overall_f1, 4),
            },
            "fixtures_evaluated": report.fixtures_evaluated,
            "total_time_ms": report.total_time_ms,
            "results": [
                {
                    "fixture": r.fixture_name,
                    "precision": round(r.precision, 4),
                    "recall": round(r.recall, 4),
                    "f1": round(r.f1, 4),
                    "tp": r.true_positives,
                    "fp": r.false_positives,
                    "fn": r.false_negatives,
                }
                for r in report.results
            ],
        }
        if report.fixtures_skipped > 0:
            output["fixtures_skipped"] = report.fixtures_skipped
        if args.cross_validate:
            output["cross_validation"] = {
                "train_precision": round(report.train_precision, 4),
                "train_recall": round(report.train_recall, 4),
                "val_precision": round(report.val_precision, 4),
                "val_recall": round(report.val_recall, 4),
            }
        json.dump(output, sys.stdout, indent=2, ensure_ascii=False)
        return 0

    # Text output
    print(f"\n{'='*60}")
    print(f"Overall: P={report.overall_precision:.4f} "
          f"R={report.overall_recall:.4f} "
          f"F1={report.overall_f1:.4f}")
    print(f"Fixtures evaluated: {report.fixtures_evaluated} "
          f"(skipped: {report.fixtures_skipped})")
    print(f"Total time: {report.total_time_ms}ms")

    if args.cross_validate and report.fixtures_evaluated >= 4:
        print(f"\nCross-validation ({args.train_split:.0%}/{1-args.train_split:.0%} split):")
        print(f"  Train: P={report.train_precision:.4f} R={report.train_recall:.4f}")
        print(f"  Val:   P={report.val_precision:.4f} R={report.val_recall:.4f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
