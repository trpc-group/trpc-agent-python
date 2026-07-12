"""Typed records shared by the trust-aware pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class CounterfactualEvidence:
    intervention: str
    valid: bool
    status: str
    changed_fail_to_pass: bool
    repaired_metrics: list[str]
    unchanged_metrics: list[str]
    before_metrics: dict[str, float]
    after_metrics: dict[str, float]
    structurally_valid: bool = True
    semantically_coherent: bool = True
    coherence_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FailureAttribution:
    case_id: str
    failure_domain: str
    primary_category: str
    secondary_categories: list[str] = field(default_factory=list)
    prompt_actionable: bool = False
    confidence: float = 0.0
    evidence: list = field(default_factory=list)
    recommended_target_prompts: list[str] = field(default_factory=list)
    evaluations_used: int = 0

    def to_dict(self) -> dict:
        value = asdict(self)
        value["evidence"] = [e.to_dict() if hasattr(e, "to_dict") else e for e in self.evidence]
        return value
