#!/usr/bin/env python3
"""Scoring harness for the code review agent.

Runs the deterministic review pipeline over a labelled diff set and reports the
two numbers issue #92 grades on:

- detection rate: share of expected *high risk* findings the agent reports as
  confident findings (acceptance criterion 2, threshold >= 80%).
- false positive rate: share of confident findings that no label accounts for
  (acceptance criterion 2, threshold <= 15%).

It also scores secret redaction against a labelled corpus (acceptance criterion
5, threshold >= 95%), and reports the false redaction rate so that widening the
secret patterns cannot quietly trade precision for recall.

Matching deliberately ignores severity: labels record the risk a human reviewer
would assign, the engine records its own, and the two are allowed to disagree.
A predicted finding matches an expected one when the file and category are equal
and the line numbers are within ``line_tolerance``.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # imported as examples.skills_code_review_agent.evaluate
    from .agent.redaction import redact_text
    from .agent.review_engine import ReviewConfig, run_review
except ImportError:  # executed as a script from the example directory
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agent.redaction import redact_text
    from agent.review_engine import ReviewConfig, run_review


HIGH_RISK_SEVERITIES = {"critical", "high"}
DEFAULT_LINE_TOLERANCE = 2


@dataclass
class ExpectedFinding:
    """One finding a labelled diff is expected to produce."""

    file: str
    line: int
    category: str
    severity: str = ""
    note: str = ""

    @property
    def is_high_risk(self) -> bool:
        return self.severity.lower() in HIGH_RISK_SEVERITIES


@dataclass
class LabelledCase:
    """A diff plus the findings a reviewer must report for it."""

    diff: str
    kind: str
    expected: list[ExpectedFinding] = field(default_factory=list)
    note: str = ""


@dataclass
class CaseResult:
    """Per-diff scoring outcome."""

    name: str
    kind: str
    expected_total: int
    expected_high_risk: int
    matched_high_risk: int
    matched_any_bucket: int
    confident_total: int
    false_positives: list[dict[str, Any]] = field(default_factory=list)
    missed: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "expected_total": self.expected_total,
            "expected_high_risk": self.expected_high_risk,
            "matched_high_risk": self.matched_high_risk,
            "matched_any_bucket": self.matched_any_bucket,
            "confident_total": self.confident_total,
            "false_positives": self.false_positives,
            "missed": self.missed,
        }


@dataclass
class EvalReport:
    """Aggregate scores across a labelled diff set."""

    cases: list[CaseResult]
    detection_rate: float
    recall_including_warnings: float
    false_positive_rate: float
    expected_high_risk: int
    matched_high_risk: int
    confident_total: int
    false_positive_total: int
    per_category: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "detection_rate": round(self.detection_rate, 4),
            "recall_including_warnings": round(self.recall_including_warnings, 4),
            "false_positive_rate": round(self.false_positive_rate, 4),
            "expected_high_risk": self.expected_high_risk,
            "matched_high_risk": self.matched_high_risk,
            "confident_total": self.confident_total,
            "false_positive_total": self.false_positive_total,
            "per_category": self.per_category,
            "cases": [case.to_dict() for case in self.cases],
        }


def load_labels(path: Path) -> tuple[list[LabelledCase], int]:
    """Load a label file and return its cases plus the line tolerance."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    tolerance = int(payload.get("line_tolerance", DEFAULT_LINE_TOLERANCE))
    cases = []
    for raw in payload.get("cases", []):
        cases.append(
            LabelledCase(
                diff=raw["diff"],
                kind=raw.get("kind", "positive"),
                expected=[
                    ExpectedFinding(
                        file=item["file"],
                        line=int(item["line"]),
                        category=item["category"],
                        severity=item.get("severity", ""),
                        note=item.get("note", ""),
                    ) for item in raw.get("expected", [])
                ],
                note=raw.get("note", ""),
            ))
    return cases, tolerance


def matches(predicted: dict[str, Any], expected: ExpectedFinding, tolerance: int) -> bool:
    """Return whether a predicted finding accounts for an expected one."""
    if _normalize_path(predicted.get("file", "")) != _normalize_path(expected.file):
        return False
    if predicted.get("category") != expected.category:
        return False
    predicted_line = predicted.get("line")
    if predicted_line is None:
        return False
    return abs(int(predicted_line) - expected.line) <= tolerance


def review_diff(diff_path: Path, *, workdir: Path) -> dict[str, Any]:
    """Run the deterministic review pipeline over one diff and return its report."""
    case_dir = workdir / diff_path.stem
    result = run_review(
        ReviewConfig(
            diff_file=diff_path,
            output_dir=case_dir,
            db_path=case_dir / "review.sqlite3",
            runtime="local",
            dry_run=True,
            task_id=f"eval-{diff_path.stem}",
        ))
    return result.report


def evaluate_case(case: LabelledCase, diffs_dir: Path, *, workdir: Path, tolerance: int) -> CaseResult:
    """Score a single labelled diff."""
    report = review_diff(diffs_dir / case.diff, workdir=workdir)
    confident = list(report.get("findings", []))
    soft = list(report.get("warnings", [])) + list(report.get("needs_human_review", []))

    claimed: set[int] = set()
    matched_high_risk = 0
    matched_any_bucket = 0
    missed: list[dict[str, Any]] = []

    for expected in case.expected:
        hit_index = next(
            (index for index, predicted in enumerate(confident)
             if index not in claimed and matches(predicted, expected, tolerance)),
            None,
        )
        if hit_index is not None:
            claimed.add(hit_index)
            matched_any_bucket += 1
            if expected.is_high_risk:
                matched_high_risk += 1
            continue
        if any(matches(predicted, expected, tolerance) for predicted in soft):
            matched_any_bucket += 1
            missed.append({**_expected_dict(expected), "found_in": "warnings_or_manual_review"})
            continue
        missed.append({**_expected_dict(expected), "found_in": "nothing"})

    false_positives = [{
        "file": predicted.get("file"),
        "line": predicted.get("line"),
        "category": predicted.get("category"),
        "severity": predicted.get("severity"),
        "title": predicted.get("title"),
        "source": predicted.get("source"),
    } for index, predicted in enumerate(confident) if index not in claimed]

    return CaseResult(
        name=case.diff,
        kind=case.kind,
        expected_total=len(case.expected),
        expected_high_risk=sum(1 for item in case.expected if item.is_high_risk),
        matched_high_risk=matched_high_risk,
        matched_any_bucket=matched_any_bucket,
        confident_total=len(confident),
        false_positives=false_positives,
        missed=missed,
    )


def evaluate(labels_path: Path, diffs_dir: Path, *, workdir: Path | None = None) -> EvalReport:
    """Score every labelled diff and aggregate the acceptance-criterion metrics."""
    cases, tolerance = load_labels(labels_path)
    if workdir is None:
        with tempfile.TemporaryDirectory(prefix="code_review_eval_") as tmp:
            return _evaluate_into(cases, diffs_dir, Path(tmp), tolerance)
    workdir.mkdir(parents=True, exist_ok=True)
    return _evaluate_into(cases, diffs_dir, workdir, tolerance)


def _evaluate_into(cases: list[LabelledCase], diffs_dir: Path, workdir: Path, tolerance: int) -> EvalReport:
    results = [evaluate_case(case, diffs_dir, workdir=workdir, tolerance=tolerance) for case in cases]

    expected_high_risk = sum(result.expected_high_risk for result in results)
    matched_high_risk = sum(result.matched_high_risk for result in results)
    expected_total = sum(result.expected_total for result in results)
    matched_any = sum(result.matched_any_bucket for result in results)
    confident_total = sum(result.confident_total for result in results)
    false_positive_total = sum(len(result.false_positives) for result in results)

    per_category = _per_category(cases, results, tolerance)

    return EvalReport(
        cases=results,
        detection_rate=_ratio(matched_high_risk, expected_high_risk),
        recall_including_warnings=_ratio(matched_any, expected_total),
        false_positive_rate=_ratio(false_positive_total, confident_total),
        expected_high_risk=expected_high_risk,
        matched_high_risk=matched_high_risk,
        confident_total=confident_total,
        false_positive_total=false_positive_total,
        per_category=per_category,
    )


def evaluate_redaction(corpus_path: Path) -> dict[str, Any]:
    """Score secret redaction recall and the false redaction rate."""
    payload = json.loads(corpus_path.read_text(encoding="utf-8"))
    secrets = [case for case in payload.get("cases", []) if case.get("kind") == "secret"]
    benign = [case for case in payload.get("cases", []) if case.get("kind") == "benign"]

    leaked: list[str] = []
    for case in secrets:
        redacted, _ = redact_text(case["text"])
        if case["secret"] in redacted:
            leaked.append(case["id"])

    over_redacted: list[str] = []
    for case in benign:
        redacted, _ = redact_text(case["text"])
        if redacted != case["text"]:
            over_redacted.append(case["id"])

    return {
        "secret_total": len(secrets),
        "secret_redacted": len(secrets) - len(leaked),
        "recall": _ratio(len(secrets) - len(leaked), len(secrets)),
        "leaked": leaked,
        "benign_total": len(benign),
        "false_redaction_rate": _ratio(len(over_redacted), len(benign)),
        "over_redacted": over_redacted,
    }


def render_markdown(report: EvalReport, redaction: dict[str, Any] | None = None) -> str:
    """Render a Markdown summary suitable for pasting into README or a PR body."""
    lines = [
        "## Evaluation",
        "",
        "| Metric | Threshold | Measured |",
        "| --- | --- | --- |",
        (f"| High-risk detection rate | >= 80% | **{report.detection_rate:.1%}** "
         f"({report.matched_high_risk}/{report.expected_high_risk}) |"),
        (f"| False positive rate | <= 15% | **{report.false_positive_rate:.1%}** "
         f"({report.false_positive_total}/{report.confident_total}) |"),
    ]
    if redaction:
        lines.append(f"| Secret redaction recall | >= 95% | **{redaction['recall']:.1%}** "
                     f"({redaction['secret_redacted']}/{redaction['secret_total']}) |")
        lines.append(f"| False redaction rate | informational | {redaction['false_redaction_rate']:.1%} |")
    lines.append(f"| Recall incl. warnings / manual review | informational | "
                 f"{report.recall_including_warnings:.1%} |")

    lines.extend(["", "### Per category", "", "| Category | Expected | Matched | False positives |",
                  "| --- | --- | --- | --- |"])
    for category, stats in sorted(report.per_category.items()):
        lines.append(f"| {category} | {stats['expected']} | {stats['matched']} | {stats['false_positives']} |")

    misses = [(case.name, item) for case in report.cases for item in case.missed]
    if misses:
        lines.extend(["", "### Known misses", ""])
        for name, item in misses:
            lines.append(f"- `{name}` {item['file']}:{item['line']} ({item['category']}) "
                         f"-> {item['found_in']}")

    false_positives = [(case.name, item) for case in report.cases for item in case.false_positives]
    if false_positives:
        lines.extend(["", "### False positives", ""])
        for name, item in false_positives:
            lines.append(f"- `{name}` {item['file']}:{item['line']} ({item['category']}) {item['title']}")

    lines.append("")
    return "\n".join(lines)


def _per_category(cases: list[LabelledCase], results: list[CaseResult], tolerance: int) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, int]] = defaultdict(lambda: {"expected": 0, "matched": 0, "false_positives": 0})
    for case, result in zip(cases, results):
        for expected in case.expected:
            stats[expected.category]["expected"] += 1
        missed_keys = {(item["file"], item["line"], item["category"]) for item in result.missed}
        for expected in case.expected:
            if (expected.file, expected.line, expected.category) not in missed_keys:
                stats[expected.category]["matched"] += 1
        for item in result.false_positives:
            stats[str(item.get("category"))]["false_positives"] += 1
    return {key: dict(value) for key, value in stats.items()}


def _expected_dict(expected: ExpectedFinding) -> dict[str, Any]:
    return {
        "file": expected.file,
        "line": expected.line,
        "category": expected.category,
        "severity": expected.severity,
        "note": expected.note,
    }


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _ratio(numerator: int, denominator: int) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def build_parser() -> argparse.ArgumentParser:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Score the code review agent against a labelled diff set.")
    parser.add_argument("--labels", type=Path, default=here / "evalset" / "labels.json")
    parser.add_argument("--diffs", type=Path, default=here / "evalset" / "holdout")
    parser.add_argument("--secrets-corpus", type=Path, default=here / "evalset" / "secrets_corpus.json")
    parser.add_argument("--skip-redaction", action="store_true", help="Score findings only.")
    parser.add_argument("--out", type=Path, help="Write the full JSON report here.")
    parser.add_argument("--markdown", action="store_true", help="Print a Markdown summary table.")
    parser.add_argument("--fail-under", action="store_true",
                        help="Exit non-zero when a threshold is missed (detection >= 80%%, FP <= 15%%, "
                             "redaction >= 95%%).")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = evaluate(args.labels, args.diffs)
    redaction = None if args.skip_redaction else evaluate_redaction(args.secrets_corpus)

    payload = report.to_dict()
    if redaction:
        payload["redaction"] = redaction

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    if args.markdown:
        print(render_markdown(report, redaction))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))

    if args.fail_under:
        failures = []
        if report.detection_rate < 0.80:
            failures.append(f"detection rate {report.detection_rate:.1%} < 80%")
        if report.false_positive_rate > 0.15:
            failures.append(f"false positive rate {report.false_positive_rate:.1%} > 15%")
        if redaction and redaction["recall"] < 0.95:
            failures.append(f"redaction recall {redaction['recall']:.1%} < 95%")
        if failures:
            print("THRESHOLDS NOT MET: " + "; ".join(failures), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
