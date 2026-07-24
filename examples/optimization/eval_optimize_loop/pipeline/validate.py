"""Validation — re-evaluate candidate prompts on the validation set.

Compares baseline vs candidate on the held-out validation set to detect
overfitting (train improvement without val improvement).
"""

from dataclasses import dataclass, field

from .baseline import BaselineResult, run_baseline_fake
from .config import PipelineConfig


@dataclass
class ValidationDelta:
    """Per-case comparison between baseline and candidate."""
    eval_id: str
    baseline_passed: bool
    candidate_passed: bool
    change: str          # "new_pass", "new_fail", "improved", "degraded", "unchanged"


@dataclass
class ValidationResult:
    """Validation set comparison results."""
    baseline: BaselineResult | None = None
    candidate: BaselineResult | None = None
    deltas: list[ValidationDelta] = field(default_factory=list)

    @property
    def new_passes(self) -> int:
        return sum(1 for d in self.deltas if d.change == "new_pass")

    @property
    def new_failures(self) -> int:
        return sum(1 for d in self.deltas if d.change == "new_fail")

    @property
    def unchanged(self) -> int:
        return sum(1 for d in self.deltas if d.change == "unchanged")

    @property
    def is_overfitting(self) -> bool:
        """Overfitting: candidate introduces new failures that weren't in baseline."""
        return self.new_failures > 0


def run_validation_fake(
    val_evalset_path: str,
    baseline_val: BaselineResult,
    candidate_baseline: BaselineResult,
    config: PipelineConfig,
) -> ValidationResult:
    """Run validation comparison in fake mode.

    Args:
        val_evalset_path: Path to validation evalset.
        baseline_val: Baseline evaluation on validation set.
        candidate_baseline: Candidate evaluation on validation set (simulated).
        config: Pipeline configuration.

    Returns:
        ValidationResult with per-case deltas.
    """
    # In fake mode, the candidate results are simulated
    # We create deltas by comparing baseline vs candidate per-case results
    baseline_map = {
        c.get("eval_id"): c.get("pass", True)
        for c in baseline_val.per_case_results
    }
    candidate_map = {
        c.get("eval_id"): c.get("pass", True)
        for c in candidate_baseline.per_case_results
    }

    deltas = []
    all_ids = set(baseline_map.keys()) | set(candidate_map.keys())

    for case_id in sorted(all_ids):
        bl_pass = baseline_map.get(case_id, True)
        cd_pass = candidate_map.get(case_id, True)

        if not bl_pass and cd_pass:
            change = "new_pass"
        elif bl_pass and not cd_pass:
            change = "new_fail"
        else:
            change = "unchanged"

        deltas.append(ValidationDelta(
            eval_id=case_id,
            baseline_passed=bl_pass,
            candidate_passed=cd_pass,
            change=change,
        ))

    return ValidationResult(
        baseline=baseline_val,
        candidate=candidate_baseline,
        deltas=deltas,
    )
