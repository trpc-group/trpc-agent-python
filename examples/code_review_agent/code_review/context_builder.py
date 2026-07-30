"""Review scope filtering and prompt context budgeting."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch

from .models import ChangedFile, LineChangeType

DEFAULT_EXCLUDES = (
    ".git/**",
    "**/.git/**",
    "__pycache__/**",
    "**/__pycache__/**",
    "node_modules/**",
    "**/node_modules/**",
    "vendor/**",
    "**/vendor/**",
    "dist/**",
    "**/dist/**",
    "build/**",
    "**/build/**",
    "*.lock",
    "**/*.lock",
    "*.min.js",
    "**/*.min.js",
    "*.map",
    "**/*.map",
)


@dataclass(frozen=True)
class ContextBudget:
    """Limits applied before any repository content reaches the model."""

    max_files: int = 40
    max_patch_chars_per_file: int = 24_000
    max_total_chars: int = 120_000
    exclude_patterns: tuple[str, ...] = DEFAULT_EXCLUDES


@dataclass(frozen=True)
class ReviewContext:
    """Prompt-ready diff context plus scope diagnostics."""

    text: str
    included_files: tuple[str, ...]
    skipped_files: tuple[str, ...]
    truncated_files: tuple[str, ...]
    diagnostics: tuple[str, ...]
    static_analysis: str = ""


def build_review_context(changed_files: list[ChangedFile], budget: ContextBudget | None = None) -> ReviewContext:
    """Build bounded, diff-only model context."""
    budget = budget or ContextBudget()
    sections: list[str] = []
    included: list[str] = []
    skipped: list[str] = []
    truncated: list[str] = []
    diagnostics: list[str] = []
    remaining = max(0, budget.max_total_chars)

    for changed_file in changed_files:
        if len(included) >= budget.max_files:
            skipped.append(changed_file.path)
            continue
        if changed_file.is_binary:
            skipped.append(changed_file.path)
            diagnostics.append(f"Skipped binary file: {changed_file.path}")
            continue
        if _is_excluded(changed_file.path, budget.exclude_patterns):
            skipped.append(changed_file.path)
            diagnostics.append(f"Skipped excluded path: {changed_file.path}")
            continue
        if not changed_file.patch:
            skipped.append(changed_file.path)
            diagnostics.append(f"Skipped file without textual patch: {changed_file.path}")
            continue

        added_line_map = _render_added_line_map(changed_file)
        header = (f"### FILE: {changed_file.path}\n"
                  f"change_type: {changed_file.change_type.value}\n"
                  f"language: {changed_file.language}\n"
                  f"added_lines: {changed_file.added_lines}\n"
                  f"deleted_lines: {changed_file.deleted_lines}\n"
                  f"ADDED LINE MAP (authoritative new-file locations):\n{added_line_map}\n\n"
                  "UNIFIED PATCH:\n")
        allowance = min(budget.max_patch_chars_per_file, max(0, remaining - len(header)))
        if allowance <= 0:
            skipped.append(changed_file.path)
            diagnostics.append("Reached total context budget")
            continue
        patch = changed_file.patch
        truncation_marker = "\n[PATCH TRUNCATED]\n"
        if len(patch) > allowance:
            patch = patch[:max(0, allowance - len(truncation_marker))] + truncation_marker
            changed_file.is_truncated = True
            changed_file.patch = patch
            truncated.append(changed_file.path)
        section = header + patch
        if len(section) > remaining:
            skipped.append(changed_file.path)
            diagnostics.append("Reached total context budget")
            continue
        sections.append(section)
        included.append(changed_file.path)
        remaining -= len(section)

    if skipped:
        diagnostics.append(f"Skipped {len(skipped)} file(s)")
    if truncated:
        diagnostics.append(f"Truncated {len(truncated)} file patch(es)")
    return ReviewContext(
        text="\n\n".join(sections),
        included_files=tuple(included),
        skipped_files=tuple(skipped),
        truncated_files=tuple(truncated),
        diagnostics=tuple(diagnostics),
    )


def _is_excluded(path: str, patterns: tuple[str, ...]) -> bool:
    normalized = path.replace("\\", "/")
    return any(fnmatch(normalized, pattern) or fnmatch(f"/{normalized}", pattern) for pattern in patterns)


def _render_added_line_map(changed_file: ChangedFile) -> str:
    lines = [
        f"L{line.new_line}: {line.content}" for hunk in changed_file.hunks for line in hunk.lines
        if line.change_type == LineChangeType.ADDED and line.new_line is not None
    ]
    return "\n".join(lines) if lines else "(no added lines)"
