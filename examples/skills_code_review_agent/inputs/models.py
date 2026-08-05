"""Detailed input models used during one review run."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic import Field

from reports.models import ReviewInputSummary


class ParsedReviewInput(BaseModel):
    """Normalized input with detailed parsed diff data."""

    summary: ReviewInputSummary
    files: list[dict[str, Any]] = Field(default_factory=list)
    diff_text: str = Field(default="", exclude=True)
    input_root: Path
    repository_path: Path | None = None
    temporary_input_root: Path | None = Field(default=None, exclude=True)
    observed_git_modes: set[str] = Field(default_factory=set, exclude=True)
    git_evidence_digests: dict[str, str] = Field(default_factory=dict, exclude=True)
    pagination_next_cursors: dict[str, int | None] = Field(
        default_factory=dict,
        exclude=True,
    )
    pagination_seen_cursors: dict[str, set[int]] = Field(
        default_factory=dict,
        exclude=True,
    )
    inspected_files: set[str] = Field(default_factory=set, exclude=True)
    untracked_files: set[str] = Field(default_factory=set, exclude=True)
    exact_cache_available: bool = Field(default=False, exclude=True)
    review_scope: str = Field(default="changed", exclude=True)
    input_changed_during_review: bool = Field(default=False, exclude=True)
    input_evidence_incomplete: bool = Field(default=False, exclude=True)
