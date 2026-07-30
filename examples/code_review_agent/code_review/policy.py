"""Deterministic validation, line mapping, sorting, and deduplication."""

from __future__ import annotations

from collections.abc import Iterable

from .models import ChangedFile, Finding, ReviewOutput, Severity

_SEVERITY_RANK = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.MEDIUM: 2,
    Severity.LOW: 3,
    Severity.INFO: 4,
}


def apply_finding_policy(
    output: ReviewOutput,
    changed_files: Iterable[ChangedFile],
    *,
    minimum_confidence: float = 0.0,
) -> tuple[ReviewOutput, list[str]]:
    """Validate finding scope and return a stable, deduplicated result."""
    file_map = {changed_file.path: changed_file for changed_file in changed_files}
    accepted: dict[tuple[str, str, int | None, int | None], Finding] = {}
    diagnostics: list[str] = []

    for original in output.findings:
        finding = original.model_copy(deep=True)
        matched_path = _match_changed_path(finding.file_path, file_map)
        if matched_path is None:
            diagnostics.append(f"Dropped finding for unchanged file: {finding.file_path}")
            continue
        finding.file_path = matched_path
        if finding.confidence < minimum_confidence:
            diagnostics.append(f"Dropped low-confidence finding {finding.rule_id} ({finding.confidence:.2f})")
            continue

        changed_file = file_map[matched_path]
        finding.publishable = _is_line_publishable(finding, changed_file)
        if finding.start_line is not None and not finding.publishable:
            diagnostics.append(f"Finding {finding.rule_id} does not point to an added line in {matched_path}")

        key = (
            finding.rule_id.casefold(),
            finding.file_path,
            finding.start_line,
            finding.end_line,
        )
        existing = accepted.get(key)
        if existing is None or _finding_priority(finding) < _finding_priority(existing):
            accepted[key] = finding

    findings = sorted(
        accepted.values(),
        key=lambda item: (
            _SEVERITY_RANK[item.severity],
            -item.confidence,
            item.file_path,
            item.start_line or 0,
            item.rule_id,
        ),
    )
    return ReviewOutput(summary=output.summary.strip(), findings=findings), diagnostics


def _match_changed_path(path: str, file_map: dict[str, ChangedFile]) -> str | None:
    normalized = path.strip().replace("\\", "/")
    candidates = [normalized]
    if normalized.startswith("./"):
        candidates.append(normalized[2:])
    if normalized.startswith(("a/", "b/")):
        candidates.append(normalized[2:])
    for candidate in candidates:
        if candidate in file_map:
            return candidate
    return None


def _is_line_publishable(finding: Finding, changed_file: ChangedFile) -> bool:
    if finding.start_line is None or finding.end_line is None:
        return False
    changed_lines = changed_file.changed_new_lines
    return any(line in changed_lines for line in range(finding.start_line, finding.end_line + 1))


def _finding_priority(finding: Finding) -> tuple[int, float]:
    return _SEVERITY_RANK[finding.severity], -finding.confidence
