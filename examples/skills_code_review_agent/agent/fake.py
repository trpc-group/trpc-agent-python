"""Deterministic fake model that reuses the code-review Skill rules."""

import importlib.util
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType

from inputs.models import ParsedReviewInput
from inputs.parser import _diff_parser_module
from reports.models import ReviewAnalysis
from reports.models import ReviewFinding

from .normalization import normalize_analysis

EXAMPLE_ROOT = Path(__file__).resolve().parent.parent
RULE_RUNNER_PATH = (
    EXAMPLE_ROOT / "skills" / "code-review" / "scripts" / "run_review_rules.py"
)


@lru_cache(maxsize=1)
def _rule_runner_module() -> ModuleType:
    """Load trusted Skill rules only for the explicit development fallback."""
    spec = importlib.util.spec_from_file_location(
        "code_review_fake_rule_runner",
        RULE_RUNNER_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load fake rule runner: {RULE_RUNNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    scripts_path = str(RULE_RUNNER_PATH.parent)
    sys.path.insert(0, scripts_path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(scripts_path)
    return module


def analyze_with_fake_model(parsed_input: ParsedReviewInput) -> ReviewAnalysis:
    """Run the same deterministic candidates used inside the Docker Skill."""
    if not parsed_input.files:
        return ReviewAnalysis(
            summary="Input was normalized, but fake mode had no diff content to inspect.",
            needs_human_review=[
                ReviewFinding(
                    severity="medium",
                    category="input_evidence",
                    file=parsed_input.summary.files[0]
                    if parsed_input.summary.files
                    else "input",
                    line=None,
                    title="Fake mode requires current diff content",
                    evidence="Only paths or a worktree reference were supplied.",
                    recommendation="Use real Docker mode or provide --diff-file/--fixture.",
                    confidence=1.0,
                    source="fake-model-rule",
                )
            ],
            checks_performed=["input normalization"],
        )

    # Reparse with sandbox-equivalent redaction before deterministic rules run.
    parsed = _diff_parser_module().parse_unified_diff(parsed_input.diff_text)
    candidates = _rule_runner_module().run_all(parsed)
    findings = [ReviewFinding.model_validate(item) for item in candidates]
    return normalize_analysis(
        ReviewAnalysis(
            summary=(
                f"Deterministic review of {parsed_input.summary.file_count} "
                "changed file(s)."
            ),
            findings=findings,
            checks_performed=[
                "unified diff parsing",
                "six deterministic code-review Skill rules",
            ],
        )
    )
