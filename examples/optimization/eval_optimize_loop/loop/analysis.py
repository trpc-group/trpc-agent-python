#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Pure regression analysis and promotion policy for the closed loop."""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from trpc_agent_sdk.evaluation import EvalCase
from trpc_agent_sdk.evaluation import EvalConfig
from trpc_agent_sdk.evaluation import EvalSet

from .models import BaselineEvaluation
from .models import CandidateDelta
from .models import CandidateEvaluation
from .models import CaseDelta
from .models import CaseEvaluation
from .models import DataQualityAudit
from .models import FailureAttributionSummary
from .models import GateCheck
from .models import GateDecision
from .models import PairedConfidenceInterval
from .models import ResourceUsage
from .models import SplitDelta
from .models import SplitEvaluation


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _text(content: Any) -> str:
    if content is None:
        return ""
    return "\n".join(str(getattr(part, "text", "") or "") for part in (content.parts or [])).strip()


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


class RegressionAnalyzer:
    """Compute comparable deltas, resource evidence, and promotion decisions."""

    def __init__(
        self,
        *,
        seed: int,
        bootstrap_samples: int,
        confidence_level: float,
    ) -> None:
        self._seed = seed
        self._bootstrap_samples = bootstrap_samples
        self._confidence_level = confidence_level

    def validate_data_quality(
        self,
        train_set: EvalSet,
        validation_set: EvalSet,
        *,
        train_path: Path,
        validation_path: Path,
        prompt_text: str,
    ) -> DataQualityAudit:
        """Reject split contamination and validation-answer leakage."""
        if train_path == validation_path:
            raise ValueError("train and validation paths must differ")
        if len(train_set.eval_cases) < 3 or len(validation_set.eval_cases) < 3:
            raise ValueError("train and validation sets must each contain at least 3 cases")

        all_ids = [case.eval_id for case in train_set.eval_cases + validation_set.eval_cases]
        duplicate_ids = sorted(case_id for case_id, count in Counter(all_ids).items() if count > 1)
        query_groups: dict[str, list[str]] = {}
        for case in train_set.eval_cases + validation_set.eval_cases:
            replay_query = self._case_replay_query(case)
            if not replay_query:
                raise ValueError(f"case {case.eval_id!r} has an empty user query")
            query_groups.setdefault(replay_query, []).append(case.eval_id)
        duplicate_queries = sorted(" == ".join(case_ids) for case_ids in query_groups.values() if len(case_ids) > 1)
        train_fingerprints = {self._case_fingerprint(case): case.eval_id for case in train_set.eval_cases}
        validation_fingerprints = {self._case_fingerprint(case): case.eval_id for case in validation_set.eval_cases}
        overlap = sorted(set(train_fingerprints) & set(validation_fingerprints))
        cross_split = [f"{train_fingerprints[item]} == {validation_fingerprints[item]}" for item in overlap]

        near_cross_split: list[str] = []
        for train_case in train_set.eval_cases:
            train_text = self._case_normalized_content(train_case)
            for validation_case in validation_set.eval_cases:
                validation_text = self._case_normalized_content(validation_case)
                if train_text == validation_text:
                    continue
                similarity = SequenceMatcher(None, train_text, validation_text).ratio()
                if min(len(train_text), len(validation_text)) >= 24 and similarity >= 0.92:
                    near_cross_split.append(f"{train_case.eval_id} ~= {validation_case.eval_id} ({similarity:.3f})")

        prompt_normalized = _normalize(prompt_text)
        leakage: list[str] = []
        for case in validation_set.eval_cases:
            expected = self._expected_response(case)
            normalized = _normalize(expected)
            if len(normalized) >= 12 and normalized in prompt_normalized:
                leakage.append(case.eval_id)
        if duplicate_ids or duplicate_queries or cross_split or near_cross_split or leakage:
            raise ValueError("data quality check failed: "
                             f"duplicate_ids={duplicate_ids}, duplicate_queries={duplicate_queries}, "
                             f"cross_split={cross_split}, "
                             f"near_cross_split={near_cross_split}, prompt_leakage={leakage}")
        return DataQualityAudit(
            passed=True,
            train_cases=len(train_set.eval_cases),
            validation_cases=len(validation_set.eval_cases),
            duplicate_ids=duplicate_ids,
            cross_split_duplicates=cross_split,
            near_cross_split_duplicates=near_cross_split,
            prompt_leakage_matches=leakage,
        )

    @staticmethod
    def _case_fingerprint(case: EvalCase) -> str:
        return _sha256_text(RegressionAnalyzer._case_normalized_content(case))

    @staticmethod
    def _case_normalized_content(case: EvalCase) -> str:
        conversation = case.conversation or []
        payload = "\n".join(f"{_text(invocation.user_content)}\n{_text(invocation.final_response)}"
                            for invocation in conversation)
        return _normalize(payload)

    @staticmethod
    def _case_replay_query(case: EvalCase) -> str:
        conversation = case.conversation or []
        return _text(conversation[0].user_content) if conversation else ""

    @staticmethod
    def _expected_response(case: EvalCase) -> str:
        conversation = case.conversation or []
        return _text(conversation[-1].final_response) if conversation else ""

    def diff(self, baseline: SplitEvaluation, candidate: SplitEvaluation) -> SplitDelta:
        """Build a complete, paired per-case delta for one split."""
        baseline_by_id = {case.case_id: case for case in baseline.cases}
        candidate_by_id = {case.case_id: case for case in candidate.cases}
        if set(baseline_by_id) != set(candidate_by_id):
            raise ValueError("baseline and candidate case sets differ; refusing partial delta")
        buckets: dict[str, list[str]] = {
            "newly_passed": [],
            "newly_failed": [],
            "score_improved": [],
            "score_regressed": [],
            "unchanged": [],
        }
        cases: list[CaseDelta] = []
        for case_id in sorted(baseline_by_id):
            before = baseline_by_id[case_id]
            after = candidate_by_id[case_id]
            score_delta = after.score - before.score
            if not before.passed and after.passed:
                status = "newly_passed"
            elif before.passed and not after.passed:
                status = "newly_failed"
            elif score_delta > 1e-12:
                status = "score_improved"
            elif score_delta < -1e-12:
                status = "score_regressed"
            else:
                status = "unchanged"
            buckets[status].append(case_id)
            cases.append(
                CaseDelta(
                    case_id=case_id,
                    status=status,
                    baseline_passed=before.passed,
                    candidate_passed=after.passed,
                    baseline_score=before.score,
                    candidate_score=after.score,
                    score_delta=score_delta,
                ))
        return SplitDelta(
            split=baseline.split,
            pass_rate_delta=candidate.pass_rate - baseline.pass_rate,
            average_score_delta=candidate.average_score - baseline.average_score,
            paired_pass_rate_ci=self._paired_bootstrap([
                float(candidate_by_id[case_id].passed) - float(baseline_by_id[case_id].passed)
                for case_id in sorted(baseline_by_id)
            ]),
            cases=cases,
            **buckets,
        )

    def _paired_bootstrap(self, paired_deltas: list[float]) -> PairedConfidenceInterval:
        if not paired_deltas:
            raise ValueError("paired bootstrap requires at least one case")
        rng = random.Random(self._seed)
        sample_size = len(paired_deltas)
        estimates = []
        for _ in range(self._bootstrap_samples):
            estimate = sum(paired_deltas[rng.randrange(sample_size)] for _ in range(sample_size)) / sample_size
            estimates.append(estimate)
        estimates.sort()
        tail = (1.0 - self._confidence_level) / 2.0
        lower_index = max(0, min(len(estimates) - 1, int(tail * len(estimates))))
        upper_index = max(
            0,
            min(len(estimates) - 1,
                int((1.0 - tail) * len(estimates)) - 1),
        )
        return PairedConfidenceInterval(
            point_estimate=sum(paired_deltas) / sample_size,
            lower=estimates[lower_index],
            upper=estimates[upper_index],
            confidence_level=self._confidence_level,
            bootstrap_samples=self._bootstrap_samples,
            seed=self._seed,
        )

    @staticmethod
    def optimizer_resources(result: Any) -> ResourceUsage:
        """Normalize AgentOptimizer accounting into the shared audit model."""
        usage = result.total_token_usage or {}
        metric_calls = int((result.extras or {}).get("total_metric_calls", 0))
        return ResourceUsage(
            metric_calls=metric_calls,
            reflection_calls=result.total_reflection_lm_calls,
            judge_calls=None,
            prompt_tokens=int(usage.get("prompt", 0)),
            completion_tokens=int(usage.get("completion", 0)),
            total_tokens=int(usage.get("total", 0)),
            cost_usd=0.0,
            duration_seconds=float(result.duration_seconds),
            cost_measurement="measured_zero_offline",
        )

    @staticmethod
    def candidate_resources(
        *,
        train_set: EvalSet,
        validation_set: EvalSet,
        prompts: dict[str, str],
        eval_config: EvalConfig,
        duration_seconds: float,
    ) -> ResourceUsage:
        """Measure replay evaluation calls, tokens, latency, cost, and time."""
        prompt_text = "\n".join(prompts.values())
        match = re.search(
            r"\[variant:\s*([a-zA-Z0-9_-]+)\]",
            prompt_text,
            re.IGNORECASE,
        )
        variant = match.group(1).lower() if match else "baseline"
        input_tokens = 0
        output_tokens = 0
        replay_costs: list[float] = []
        replay_cost_complete = True
        latencies_ms: list[float] = []
        num_runs = int(eval_config.num_runs)
        if num_runs <= 0:
            raise ValueError("num_runs must be > 0")
        all_cases = train_set.eval_cases + validation_set.eval_cases
        for case in all_cases:
            state = case.session_input.state if case.session_input else {}
            payload = (state.get("variant_traces") or {}).get(variant) or {}
            usage = payload.get("usage") or {}
            input_tokens += int(usage.get("input_tokens", 0))
            output_tokens += int(usage.get("output_tokens", 0))
            raw_cost = usage.get("cost_usd")
            if raw_cost is None:
                raw_cost = usage.get("cost")
            if raw_cost is None:
                replay_cost_complete = False
            else:
                if isinstance(raw_cost, bool) or not isinstance(raw_cost, (int, float)):
                    raise ValueError(f"case {case.eval_id!r} has non-numeric replay cost")
                case_cost = float(raw_cost)
                if not math.isfinite(case_cost) or case_cost < 0:
                    raise ValueError(f"case {case.eval_id!r} has invalid replay cost")
                replay_costs.append(case_cost)
            if usage.get("latency_ms") is not None:
                latencies_ms.append(float(usage["latency_ms"]))
        metric_calls = len(all_cases) * len(eval_config.get_eval_metrics()) * num_runs
        judge_metrics = sum(1 for metric in eval_config.get_eval_metrics() if metric.metric_name.startswith("llm_"))
        p95_latency_ms = None
        if latencies_ms:
            latencies_ms.sort()
            index = max(0, min(len(latencies_ms) - 1, math.ceil(0.95 * len(latencies_ms)) - 1))
            p95_latency_ms = latencies_ms[index]
        measured_cost = math.fsum(replay_costs) * num_runs if replay_cost_complete else None
        if measured_cost is None:
            cost_measurement = "unavailable"
        elif measured_cost == 0.0:
            cost_measurement = "measured_zero_offline"
        else:
            cost_measurement = "measured_from_replay"
        return ResourceUsage(
            metric_calls=metric_calls,
            reflection_calls=0,
            judge_calls=len(all_cases) * judge_metrics * num_runs,
            prompt_tokens=input_tokens * num_runs,
            completion_tokens=output_tokens * num_runs,
            total_tokens=(input_tokens + output_tokens) * num_runs,
            cost_usd=measured_cost,
            duration_seconds=duration_seconds,
            p95_latency_ms=p95_latency_ms,
            cost_measurement=cost_measurement,
        )

    @staticmethod
    def gate(
        *,
        baseline: BaselineEvaluation,
        candidate_train: SplitEvaluation,
        candidate_validation: SplitEvaluation,
        delta: CandidateDelta,
        optimizer_status: str,
        resources: ResourceUsage,
        config: dict[str, Any],
    ) -> GateDecision:
        """Apply configured validation, regression, overfit, and budget policy."""
        minimum_gain = float(config.get("min_validation_gain", 0.0))
        gain = delta.validation.pass_rate_delta
        newly_failed_ids = set(delta.validation.newly_failed)
        candidate_by_id = {case.case_id: case for case in candidate_validation.cases}
        new_hard_failures = sorted(case_id for case_id in newly_failed_ids if candidate_by_id[case_id].hard_fail)
        epsilon = 1e-12
        baseline_validation_by_id = {case.case_id: case for case in baseline.validation.cases}
        key_regressions = sorted(
            case.case_id for case in candidate_validation.cases
            if case.key_case and ((baseline_validation_by_id[case.case_id].passed and not case.passed)
                                  or baseline_validation_by_id[case.case_id].score - case.score > epsilon))

        def _trend(split_delta: SplitDelta) -> int:
            if split_delta.pass_rate_delta > epsilon:
                return 1
            if split_delta.pass_rate_delta < -epsilon:
                return -1
            if split_delta.average_score_delta > epsilon:
                return 1
            if split_delta.average_score_delta < -epsilon:
                return -1
            return 0

        overfitting = _trend(delta.train) > 0 and _trend(delta.validation) < 0
        checks = [
            GateCheck(
                name="optimizer_succeeded",
                passed=optimizer_status == "SUCCEEDED",
                actual=optimizer_status,
                expected="SUCCEEDED",
                reason="AgentOptimizer must finish successfully.",
            ),
            GateCheck(
                name="minimum_validation_gain",
                passed=gain + 1e-12 >= minimum_gain,
                actual=gain,
                expected=f">= {minimum_gain}",
                reason="Candidate validation pass-rate gain must meet the configured floor.",
            ),
            GateCheck(
                name="no_new_hard_failures",
                passed=not new_hard_failures,
                actual=new_hard_failures,
                expected=[],
                reason="A baseline pass may not become a hard failure.",
                required=bool(config.get("forbid_new_hard_failures", True)),
            ),
            GateCheck(
                name="key_cases_no_regression",
                passed=not key_regressions,
                actual=key_regressions,
                expected=[],
                reason="Key validation cases may not lose score.",
                required=bool(config.get("key_cases_no_regression", True)),
            ),
            GateCheck(
                name="no_train_validation_overfit",
                passed=not overfitting,
                actual=overfitting,
                expected=False,
                reason=("Train improvement paired with validation regression is rejected; "
                        "pass rate is primary and average score breaks ties."),
                required=bool(config.get("reject_overfitting", True)),
            ),
        ]
        ci_floor = config.get("min_validation_gain_ci_lower_bound")
        if ci_floor is not None:
            lower = delta.validation.paired_pass_rate_ci.lower
            checks.append(
                GateCheck(
                    name="validation_gain_ci_lower_bound",
                    passed=lower + 1e-12 >= float(ci_floor),
                    actual=lower,
                    expected=f">= {ci_floor}",
                    reason=("The paired bootstrap lower bound must meet the "
                            "configured confidence floor."),
                ))
        budget = config.get("budget") or {}
        for name, actual, key in (
            ("metric_call_budget", resources.metric_calls, "max_metric_calls"),
            ("token_budget", resources.total_tokens, "max_total_tokens"),
            ("duration_budget", resources.duration_seconds, "max_duration_seconds"),
        ):
            limit = budget.get(key)
            if limit is None:
                continue
            checks.append(
                GateCheck(
                    name=name,
                    passed=actual <= float(limit),
                    actual=actual,
                    expected=f"<= {limit}",
                    reason=f"Measured resource usage must stay within {key}.",
                ))
        max_cost = budget.get("max_cost_usd")
        if max_cost is not None:
            cost_known = resources.cost_usd is not None and resources.cost_measurement != "unavailable"
            if isinstance(max_cost, bool) or not isinstance(max_cost, (int, float)):
                raise ValueError("max_cost_usd must be a finite non-negative number")
            max_cost_value = float(max_cost)
            if not math.isfinite(max_cost_value) or max_cost_value < 0:
                raise ValueError("max_cost_usd must be a finite non-negative number")
            within_cost_budget = False
            if cost_known:
                measured_cost = float(resources.cost_usd)
                within_cost_budget = measured_cost < max_cost_value or math.isclose(
                    measured_cost,
                    max_cost_value,
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                )
            checks.append(
                GateCheck(
                    name="cost_budget",
                    passed=within_cost_budget,
                    actual=resources.cost_usd if cost_known else "unavailable",
                    expected=f"<= {max_cost}",
                    reason="Unknown cost fails closed; a measured cost must be within budget.",
                ))
        accepted = all(check.passed for check in checks if check.required)
        return GateDecision(
            accepted=accepted,
            overfitting_detected=overfitting,
            checks=checks,
        )

    @staticmethod
    def candidate_id(
        prompts: dict[str, str],
        known_candidates: dict[str, str],
    ) -> str:
        """Resolve fixture candidates by exact prompt, otherwise use a stable hash."""
        if len(prompts) == 1:
            prompt = next(iter(prompts.values())).strip()
            for candidate_id, candidate_prompt in known_candidates.items():
                if candidate_prompt.strip() == prompt:
                    return candidate_id
        return f"candidate-{_sha256_text(json.dumps(prompts, sort_keys=True))[:12]}"

    @staticmethod
    def unique_proposals(
        proposals: list[tuple[int, dict[str, str]]],
        *,
        best_prompts: dict[str, str],
    ) -> list[tuple[int | None, dict[str, str]]]:
        """Deduplicate full prompt maps while retaining optimizer order."""
        unique: list[tuple[int | None, dict[str, str]]] = []
        seen: set[str] = set()
        for optimizer_round, prompts in proposals:
            fingerprint = _sha256_text(json.dumps(prompts, sort_keys=True, ensure_ascii=False))
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            unique.append((optimizer_round, dict(prompts)))
        if best_prompts:
            fingerprint = _sha256_text(json.dumps(best_prompts, sort_keys=True, ensure_ascii=False))
            if fingerprint not in seen:
                unique.append((None, dict(best_prompts)))
        if not unique:
            raise RuntimeError("AgentOptimizer produced no auditable prompt candidate")
        return unique

    @staticmethod
    def _pareto_latency_ms(resources: ResourceUsage) -> float:
        """Return a dominance-safe latency value for Pareto comparisons."""
        return math.inf if resources.p95_latency_ms is None else resources.p95_latency_ms

    @staticmethod
    def mark_pareto(candidates: list[CandidateEvaluation]) -> None:
        """Mark gate-eligible candidates not dominated on quality, tokens, and latency."""
        eligible = [candidate for candidate in candidates if candidate.gate.accepted]
        for candidate in candidates:
            if not candidate.gate.accepted:
                candidate.pareto_optimal = False
                continue
            candidate_latency = RegressionAnalyzer._pareto_latency_ms(candidate.audit.resources)
            dominated = False
            for other in eligible:
                if other is candidate:
                    continue
                other_latency = RegressionAnalyzer._pareto_latency_ms(other.audit.resources)
                no_worse = (other.validation.pass_rate >= candidate.validation.pass_rate
                            and other.validation.average_score >= candidate.validation.average_score
                            and other.audit.resources.total_tokens <= candidate.audit.resources.total_tokens
                            and other_latency <= candidate_latency)
                strictly_better = (other.validation.pass_rate > candidate.validation.pass_rate
                                   or other.validation.average_score > candidate.validation.average_score
                                   or other.audit.resources.total_tokens < candidate.audit.resources.total_tokens
                                   or other_latency < candidate_latency)
                if no_worse and strictly_better:
                    dominated = True
                    break
            candidate.pareto_optimal = not dominated

    @staticmethod
    def combined_rejection_gate(candidates: list[CandidateEvaluation]) -> GateDecision:
        """Return representative rejection evidence when no candidate passes."""
        if candidates:
            return candidates[-1].gate
        return GateDecision(
            accepted=False,
            checks=[
                GateCheck(
                    name="candidate_available",
                    passed=False,
                    actual=0,
                    expected=">= 1",
                    reason="No independently evaluable candidate was produced.",
                )
            ],
        )

    @staticmethod
    def failure_summary(scoped_cases: dict[str, list[CaseEvaluation]], ) -> FailureAttributionSummary:
        """Summarize evidence coverage and categories across every evaluated scope."""
        failures = [(scope, case) for scope, cases in scoped_cases.items() for case in cases if not case.passed]
        by_case = {f"{scope}/{case.case_id}": case.failure_reasons for scope, case in failures if case.failure_reasons}
        counts = Counter(reason.category for reasons in by_case.values() for reason in reasons)
        explained = len(by_case)
        total = len(failures)
        return FailureAttributionSummary(
            explained_failed_cases=explained,
            total_failed_cases=total,
            coverage_rate=explained / total if total else 1.0,
            category_counts=dict(sorted(counts.items())),
            by_case=by_case,
        )
