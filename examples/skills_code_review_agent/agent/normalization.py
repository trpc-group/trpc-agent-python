"""Normalize model findings before they leave the trusted application boundary."""

from reports.models import ReviewAnalysis
from reports.models import ReviewFinding
from inputs.models import ParsedReviewInput
from security import redact_analysis

CONFIDENCE_THRESHOLD = 0.70


def enforce_analysis_scope(
    analysis: ReviewAnalysis,
    parsed_input: ParsedReviewInput,
) -> ReviewAnalysis:
    """Reject model issues that do not point to evidence in the selected input."""
    allowed_files = set(parsed_input.summary.files)
    candidate_lines: dict[str, set[int]] = {}
    if parsed_input.summary.kind in {"diff_file", "fixture"}:
        for file_data in parsed_input.files:
            path = file_data.get("new_path")
            if not path or path == "/dev/null":
                path = file_data.get("old_path")
            if not path:
                continue
            candidate_lines[str(path)] = {
                int(line)
                for hunk in file_data.get("hunks", [])
                for line in hunk.get("candidate_lines", [])
                if isinstance(line, int) and line > 0
            }

    rejected = 0

    def in_scope(item: ReviewFinding, *, allow_input: bool = False) -> bool:
        nonlocal rejected
        if allow_input and item.file == "input" and item.line is None:
            return True
        if item.file not in allowed_files:
            rejected += 1
            return False
        lines = candidate_lines.get(item.file)
        if lines is not None and item.line is not None and item.line not in lines:
            rejected += 1
            return False
        return True

    findings = [item for item in analysis.findings if in_scope(item)]
    warnings = [item for item in analysis.warnings if in_scope(item)]
    human_review = [
        item
        for item in analysis.needs_human_review
        if in_scope(item, allow_input=True)
    ]
    if rejected:
        human_review.append(
            ReviewFinding(
                severity="medium",
                category="agent_evidence_validation",
                file="input",
                line=None,
                title="Model output contained out-of-scope findings",
                evidence=f"{rejected} finding(s) lacked selected-input evidence.",
                recommendation="Review the input manually and rerun with bounded evidence.",
                confidence=1.0,
                source="scope-validator",
            )
        )
    return analysis.model_copy(
        update={
            "findings": findings,
            "warnings": warnings,
            "needs_human_review": human_review,
        }
    )


def normalize_analysis(analysis: ReviewAnalysis) -> ReviewAnalysis:
    """Deduplicate findings, route low confidence, and redact free text."""
    selected: dict[
        tuple[str, int | None, str],
        tuple[ReviewFinding, str],
    ] = {}
    bucket_priority = {"finding": 0, "warning": 1, "human_review": 2}

    def select(
        item: ReviewFinding,
        bucket: str,
    ) -> None:
        key = (item.file, item.line, item.category)
        current = selected.get(key)
        candidate_rank = (item.confidence, bucket_priority[bucket])
        if current is None:
            selected[key] = (item, bucket)
            return
        current_item, current_bucket = current
        current_rank = (
            current_item.confidence,
            bucket_priority[current_bucket],
        )
        if candidate_rank > current_rank:
            selected[key] = (item, bucket)

    for item in analysis.findings:
        select(item, "finding")
    for item in analysis.warnings:
        select(item, "warning")
    for item in analysis.needs_human_review:
        select(item, "human_review")

    findings: list[ReviewFinding] = []
    warnings: list[ReviewFinding] = []
    human_review: list[ReviewFinding] = []
    for item, bucket in selected.values():
        if bucket == "human_review":
            human_review.append(item)
        elif bucket == "warning" or item.confidence < CONFIDENCE_THRESHOLD:
            warnings.append(item)
        else:
            findings.append(item)

    normalized = analysis.model_copy(
        update={
            "findings": findings,
            "warnings": warnings,
            "needs_human_review": human_review,
        }
    )
    return redact_analysis(normalized)
