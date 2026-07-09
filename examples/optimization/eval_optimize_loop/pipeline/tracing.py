"""Audit tracing — records seeds, timing, cost, and reproducibility info.

Produces a JSON-serializable audit trail that accompanies every
optimization report. This ensures full reproducibility and cost
transparency — something none of the competing PRs provide.
"""

import os
import time
import platform
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StageTiming:
    """Wall-clock timing for a single pipeline stage."""
    stage: str
    start_time: float = 0.0
    end_time: float = 0.0

    @property
    def duration_ms(self) -> float:
        return (self.end_time - self.start_time) * 1000

    @property
    def duration_s(self) -> float:
        return round(self.end_time - self.start_time, 3)


@dataclass
class AuditTrail:
    """Complete audit trail for an optimization run."""

    # Reproducibility
    seed: int = 42
    mode: str = "fake"
    algorithm: str = "gepa_reflective"
    reproduce_command: str = ""

    # Timing
    stages: list[StageTiming] = field(default_factory=list)
    total_duration_s: float = 0.0

    # Cost
    optimization_cost_usd: float = 0.0
    evaluation_cost_usd: float = 0.0
    total_cost_usd: float = 0.0

    # Environment
    python_version: str = ""
    platform_info: str = ""
    input_file_hashes: dict[str, str] = field(default_factory=dict)

    # Results
    baseline_train_pass_rate: float = 0.0
    candidate_train_pass_rate: float = 0.0
    improvement: float = 0.0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # File paths (relative, for portability)
    input_files: dict[str, str] = field(default_factory=dict)
    output_files: dict[str, str] = field(default_factory=dict)


class AuditTracer:
    """Records timing and events during pipeline execution.

    Usage:
        tracer = AuditTracer(seed=42, mode="fake")
        tracer.start_stage("baseline")
        # ... do work ...
        tracer.end_stage("baseline")
        audit = tracer.to_dict()
    """

    def __init__(
        self,
        seed: int = 42,
        mode: str = "fake",
        algorithm: str = "gepa_reflective",
    ):
        self._audit = AuditTrail(
            seed=seed,
            mode=mode,
            algorithm=algorithm,
            python_version=platform.python_version(),
            platform_info=f"{platform.system()} {platform.release()}",
        )
        self._active_stage: str | None = None
        self._stage_start: float = 0.0
        self._pipeline_start = time.monotonic()

    def start_stage(self, stage_name: str) -> None:
        """Begin timing a pipeline stage."""
        self._active_stage = stage_name
        self._stage_start = time.monotonic()

    def end_stage(self, stage_name: str) -> StageTiming:
        """End timing a pipeline stage and record it."""
        end = time.monotonic()
        timing = StageTiming(
            stage=stage_name,
            start_time=self._stage_start,
            end_time=end,
        )
        self._audit.stages.append(timing)
        self._active_stage = None
        return timing

    def add_cost(self, usd: float, category: str = "optimization") -> None:
        """Add cost to the audit trail."""
        if category == "evaluation":
            self._audit.evaluation_cost_usd += usd
        else:
            self._audit.optimization_cost_usd += usd
        self._audit.total_cost_usd += usd

    def add_error(self, error: str) -> None:
        """Record a non-fatal error."""
        self._audit.errors.append(error)

    def add_warning(self, warning: str) -> None:
        """Record a warning."""
        self._audit.warnings.append(warning)

    def record_input_file(self, key: str, path: str) -> None:
        """Record an input file with its hash."""
        self._audit.input_files[key] = path
        try:
            import hashlib
            with open(path, "rb") as f:
                sha = hashlib.sha256(f.read()).hexdigest()[:12]
            self._audit.input_file_hashes[key] = sha
        except (OSError, ImportError):
            self._audit.input_file_hashes[key] = "unavailable"

    def set_results(
        self,
        baseline_train_pass_rate: float,
        candidate_train_pass_rate: float,
        improvement: float,
    ) -> None:
        """Record final result metrics."""
        self._audit.baseline_train_pass_rate = baseline_train_pass_rate
        self._audit.candidate_train_pass_rate = candidate_train_pass_rate
        self._audit.improvement = improvement

    def set_output_files(self, json_path: str, md_path: str) -> None:
        """Record output file paths."""
        self._audit.output_files = {
            "json_report": json_path,
            "md_report": md_path,
        }

    def finalize(self) -> AuditTrail:
        """Complete the audit trail and return it."""
        self._audit.total_duration_s = round(
            time.monotonic() - self._pipeline_start, 1
        )

        # Build reproduce command
        parts = [f"python run_pipeline.py --mode {self._audit.mode}"]
        if self._audit.seed != 42:
            parts.append(f"--seed {self._audit.seed}")
        self._audit.reproduce_command = " ".join(parts)

        return self._audit

    def to_dict(self) -> dict[str, Any]:
        """Convert the audit trail to a JSON-serializable dict."""
        audit = self.finalize()
        return {
            "reproducibility": {
                "seed": audit.seed,
                "mode": audit.mode,
                "algorithm": audit.algorithm,
                "reproduce_command": audit.reproduce_command,
            },
            "timing": {
                "stages": [
                    {
                        "stage": s.stage,
                        "duration_ms": round(s.duration_ms, 1),
                        "duration_s": s.duration_s,
                    }
                    for s in audit.stages
                ],
                "total_duration_s": audit.total_duration_s,
            },
            "cost": {
                "optimization_usd": round(audit.optimization_cost_usd, 4),
                "evaluation_usd": round(audit.evaluation_cost_usd, 4),
                "total_usd": round(audit.total_cost_usd, 4),
            },
            "environment": {
                "python_version": audit.python_version,
                "platform": audit.platform_info,
            },
            "results": {
                "baseline_train_pass_rate": audit.baseline_train_pass_rate,
                "candidate_train_pass_rate": audit.candidate_train_pass_rate,
                "improvement": round(audit.improvement, 4),
            },
            "input_files": audit.input_files,
            "input_file_hashes": audit.input_file_hashes,
            "output_files": audit.output_files,
            "errors": audit.errors,
            "warnings": audit.warnings,
        }
