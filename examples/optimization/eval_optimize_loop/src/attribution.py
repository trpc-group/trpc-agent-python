"""Phase 2: 失败归因引擎。

对 baseline 评测中的失败 case 进行自动分类，按 6 个维度聚类，
输出归因统计和优化建议，为 Phase 3 AgentOptimizer 提供优化方向。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.baseline import BaselineResult, BaselineCaseResult


@dataclass
class AttributionCase:
    """单条 case 的归因结果。"""
    case_id: str
    dataset: str
    category: str
    category_priority: int
    confidence: float
    evidence: list[str] = field(default_factory=list)
    ground_truth: str = ""
    predicted: str = ""
    score: float = 0.0
    char_match_rate: float = 0.0
    judge_scores: dict = field(default_factory=dict)
    trajectory_signals: dict = field(default_factory=dict)
    conditions: dict = field(default_factory=dict)  # from BaselineCaseResult.conditions

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id, "dataset": self.dataset,
            "category": self.category, "category_priority": self.category_priority,
            "confidence": round(self.confidence, 3), "evidence": self.evidence,
            "ground_truth": self.ground_truth, "predicted": self.predicted,
            "score": round(self.score, 4), "char_match_rate": round(self.char_match_rate, 3),
            "judge_scores": self.judge_scores, "trajectory_signals": self.trajectory_signals,
            "conditions": self.conditions,
        }


@dataclass
class AttributionCluster:
    """单个归因类别的聚合统计。"""
    category: str
    priority: int
    count: int = 0
    train_count: int = 0
    val_count: int = 0
    cases: list[str] = field(default_factory=list)
    avg_confidence: float = 0.0
    avg_score: float = 0.0
    dominant_condition: str = ""
    prompt_target: str = ""

    def to_dict(self) -> dict:
        return {
            "category": self.category, "priority": self.priority,
            "count": self.count, "train_count": self.train_count,
            "val_count": self.val_count, "cases": self.cases,
            "avg_confidence": round(self.avg_confidence, 3),
            "avg_score": round(self.avg_score, 4),
            "dominant_condition": self.dominant_condition,
            "prompt_target": self.prompt_target,
        }


@dataclass
class AttributionReport:
    """完整归因报告。"""
    total_failures: int = 0
    train_failures: int = 0
    val_failures: int = 0
    attributed_count: int = 0
    unattributed_count: int = 0
    clusters: list[AttributionCluster] = field(default_factory=list)
    cases: list[AttributionCase] = field(default_factory=list)
    optimization_priority: list[str] = field(default_factory=list)

    @property
    def primary_failure_category(self) -> Optional[AttributionCluster]:
        if not self.clusters:
            return None
        return max(self.clusters, key=lambda c: c.count)

    @property
    def cluster_map(self) -> dict[str, AttributionCluster]:
        return {c.category: c for c in self.clusters}

    def to_dict(self) -> dict:
        return {
            "total_failures": self.total_failures,
            "train_failures": self.train_failures,
            "val_failures": self.val_failures,
            "attributed_count": self.attributed_count,
            "unattributed_count": self.unattributed_count,
            "clusters": [c.to_dict() for c in self.clusters],
            "cases": [c.to_dict() for c in self.cases],
            "optimization_priority": self.optimization_priority,
        }


CATEGORY_META: dict[str, dict] = {
    "final_answer_mismatch":       {"priority": 1, "prompt_target": "system_prompt"},
    "tool_call_error":              {"priority": 2, "prompt_target": "skill_prompt"},
    "param_error":                  {"priority": 3, "prompt_target": "skill_prompt"},
    "llm_rubric_fail":              {"priority": 4, "prompt_target": "system_prompt"},
    "knowledge_recall_insufficient":{"priority": 5, "prompt_target": "skill_prompt"},
    "format_invalid":               {"priority": 6, "prompt_target": "system_prompt"},
}


class AttributionRunner:
    """失败归因运行器。"""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self.categories = self.config.get("categories", list(CATEGORY_META.keys()))

    def run(
        self, train_result: BaselineResult, val_result: BaselineResult
    ) -> AttributionReport:
        all_attrs: list[AttributionCase] = []
        for case in train_result.failed_cases:
            all_attrs.append(self._attribute_case(case, "train"))
        for case in val_result.failed_cases:
            all_attrs.append(self._attribute_case(case, "val"))
        clusters = self._build_clusters(all_attrs)
        opt_priority = [c.category for c in sorted(clusters, key=lambda x: -x.count)]
        attributed = [a for a in all_attrs if a.category != "unattributed"]
        return AttributionReport(
            total_failures=len(all_attrs),
            train_failures=sum(1 for a in all_attrs if a.dataset == "train"),
            val_failures=sum(1 for a in all_attrs if a.dataset == "val"),
            attributed_count=len(attributed),
            unattributed_count=len(all_attrs) - len(attributed),
            clusters=clusters, cases=all_attrs, optimization_priority=opt_priority,
        )

    def _attribute_case(
        self, case: BaselineCaseResult, dataset: str
    ) -> AttributionCase:
        evidence: list[str] = []
        candidates: list[tuple[str, float]] = []

        # Rule 1: failure_reason keyword match
        fr = case.failure_reason.lower()
        if fr:
            kw_map = {
                "final_answer_mismatch": ["final_answer_mismatch", "char_match", "mismatch"],
                "tool_call_error": ["tool_call_error", "tool execution failed", "timeout"],
                "param_error": ["param_error", "parameter invalid", "invalid param"],
                "llm_rubric_fail": ["llm_rubric_fail", "rubric", "judge score"],
                "knowledge_recall_insufficient": ["knowledge_recall", "blacklist miss", "confusion char"],
                "format_invalid": ["format_invalid", "format", "schema", "json parse"],
            }
            for cat, kws in kw_map.items():
                if any(kw in fr for kw in kws):
                    candidates.append((cat, 0.90))
                    evidence.append(f"failure_reason: {case.failure_reason[:80]}")

        # Rule 2: trajectory signals (check raw_steps first, fallback to nodes)
        traj = case.trajectory
        if traj:
            raw_steps = traj.get("raw_steps", [])
            nodes = traj.get("nodes", [])
            search_text = " ".join(raw_steps).lower() if raw_steps else " ".join(nodes).lower()
            human_review = traj.get("human_review_triggered", False)
            conf_val = traj.get("confidence")

            if "error" in search_text or "failed" in search_text:
                candidates.append(("tool_call_error", 0.75))
                evidence.append("trajectory tool error")

            if any(kw in search_text for kw in ["partial", "shifted", "missing"]):
                candidates.append(("param_error", 0.65))
                evidence.append("trajectory param/locate issue")

            if "knowledge_search" in search_text and "miss" in search_text:
                candidates.append(("knowledge_recall_insufficient", 0.85))
                evidence.append("knowledge_search miss in trajectory")

            if human_review and conf_val is not None and conf_val < 0.5:
                candidates.append(("llm_rubric_fail", 0.70))
                evidence.append(f"human_review with low conf={conf_val}")

        # Rule 3: Judge scores
        if case.judge_recognition >= 0 and case.judge_recognition < 0.6:
            candidates.append(("llm_rubric_fail", 0.80))
            evidence.append(f"judge_recognition={case.judge_recognition:.2f} < 0.6")
        if case.judge_blacklist >= 0 and case.judge_blacklist < 0.6:
            candidates.append(("knowledge_recall_insufficient", 0.75))
            evidence.append(f"judge_blacklist={case.judge_blacklist:.2f} < 0.6")
        if case.judge_response >= 0 and case.judge_response < 0.6:
            candidates.append(("llm_rubric_fail", 0.65))
            evidence.append(f"judge_response={case.judge_response:.2f} < 0.6")

        # Rule 4: char match fallback
        char_rate = case.char_correct / max(case.char_total, 1)
        if not case.correct:
            candidates.append(("final_answer_mismatch", 0.85))
            evidence.append(f"pred != gt, char_match={char_rate:.2f}")

        # Select best category (highest priority, then confidence)
        if candidates:
            candidates.sort(key=lambda x: (CATEGORY_META.get(x[0], {}).get("priority", 99), -x[1]))
            best_cat, best_conf = candidates[0]
        else:
            best_cat, best_conf = "unattributed", 0.0
            evidence.append("no matching category")

        cat_priority = CATEGORY_META.get(best_cat, {}).get("priority", 0)

        traj_signals = {}
        if case.trajectory:
            traj_signals = {
                "nodes": case.trajectory.get("nodes", []),
                "human_review_triggered": case.trajectory.get("human_review_triggered", False),
                "confidence": case.trajectory.get("confidence"),
            }

        judge_summary = {}
        for dim in ("recognition", "blacklist", "response"):
            val = getattr(case, f"judge_{dim}", -1)
            if val >= 0:
                judge_summary[dim] = val

        return AttributionCase(
            case_id=case.case_id, dataset=dataset,
            category=best_cat, category_priority=cat_priority,
            confidence=best_conf, evidence=evidence,
            ground_truth=case.ground_truth, predicted=case.predicted,
            score=case.score, char_match_rate=char_rate,
            judge_scores=judge_summary, trajectory_signals=traj_signals,
            conditions=case.conditions,
        )

    def _build_clusters(
        self, attributions: list[AttributionCase]
    ) -> list[AttributionCluster]:
        clusters: dict[str, AttributionCluster] = {}
        for cat_name in self.categories:
            meta = CATEGORY_META.get(cat_name, {})
            clusters[cat_name] = AttributionCluster(
                category=cat_name, priority=meta.get("priority", 99),
                prompt_target=meta.get("prompt_target", ""),
            )
        for attr in attributions:
            if attr.category not in clusters:
                continue
            c = clusters[attr.category]
            c.count += 1
            if attr.dataset == "train":
                c.train_count += 1
            else:
                c.val_count += 1
            c.cases.append(attr.case_id)
            c.avg_score += attr.score
            c.avg_confidence += attr.confidence
        for c in clusters.values():
            if c.count > 0:
                c.avg_score /= c.count
                c.avg_confidence /= c.count
            cluster_attrs = [a for a in attributions if a.category == c.category]
            if cluster_attrs:
                c.dominant_condition = self._guess_dominant_condition(cluster_attrs)
        return [c for c in clusters.values() if c.count > 0]

    @staticmethod
    def _guess_dominant_condition(attributions: list) -> str:
        """Derive dominant condition from real case conditions (not hardcoded map)."""
        counts: dict[str, int] = {}
        for attr in attributions:
            cond_type = attr.conditions.get("type", "unknown") if attr.conditions else "unknown"
            counts[cond_type] = counts.get(cond_type, 0) + 1
        return max(counts, key=counts.get) if counts else "unknown"


def run_attribution(
    train_result: BaselineResult,
    val_result: BaselineResult,
    config_path: Optional[str | Path] = None,
) -> AttributionReport:
    config = None
    if config_path:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f).get("attribution", {})
    return AttributionRunner(config=config).run(train_result, val_result)
