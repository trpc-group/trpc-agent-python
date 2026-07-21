"""Phase 3: Prompt optimization engine.

?? Phase 2 ?????? TargetPrompt?system_prompt / skill_prompt???
????????? prompt ??????

???????
- fake: ??????????? prompt ???? API ???
- real: ?? trpc_agent.optimization.AgentOptimizer API

?????
- failure_driven: ??????????????????????? prompt ??
- iterative: ???????? max_iterations ???
"""

from __future__ import annotations

import json
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.attribution import AttributionReport, AttributionCluster, CATEGORY_META


# ============================================================================
# ?? Prompt ????? PlateAgent ??? prompt ???
# ============================================================================

BASE_PROMPTS: dict[str, str] = {
    "system_prompt": (
        "You are a license plate recognition agent.\n"
        "Follow the pipeline: preprocess -> locate -> segment -> recognize -> output.\n\n"
        "## Workflow\n"
        "1. Preprocess (blur, grayscale, binarize, edge detect, affine correct)\n"
        "2. Locate plate region (morphology + HSV)\n"
        "3. Segment characters (vertical projection)\n"
        "4. Recognize with dual-channel Tesseract OCR\n"
        "5. Verify low-confidence results (LLM or human review)\n"
        "6. Format and output the result\n\n"
        "## Output Format\n"
        "Return a JSON object with plate_number, confidence, blacklist_hit.\n\n"
        "## Notes\n"
        "- Beware confusion characters: B/8, 0/O, S/5, 2/Z\n"
        "- Use length-priority selection for dual-channel results\n"
        "- Trigger human review when confidence < 0.5\n"
    ),
    "skill_prompt": (
        "## Preprocessing Guide\n"
        "Use GaussianBlur(kernel=5) for noisy images, OTSU for clean plates.\n"
        "Skip Canny edge detection for Chinese characters.\n\n"
        "## Locate Guide\n"
        "Morphology coarse + HSV color-space fine localization.\n\n"
        "## Segment Guide\n"
        "Vertical projection character segmentation. Min char width: 8px.\n\n"
        "## Recognize Guide\n"
        "Dual-channel: original + GaussianBlur(kernel=5).\n"
        "Length-priority: prefer 7-char over 6-char results.\n"
        "Confusion pairs: B/8, 0/O, 2/Z, 5/S, 1/I, 7/T, C/G, E/F.\n\n"
        "## Knowledge Base\n"
        "Query confusion_chars, blacklist, and history collections.\n"
    ),
}

CATEGORY_OPTIMIZATION_HINTS: dict[str, dict] = {
    "final_answer_mismatch": {
        "target_section": "Output Format",
        "strategy": (
            "Strengthen output constraints: add plate-number regex validation,\n"
            "require LLM double-check, add post-processing character filter."
        ),
    },
    "tool_call_error": {
        "target_section": "Tool usage guide",
        "strategy": (
            "Improve tool robustness: add retry with exponential backoff,\n"
            "add timeout per tool call, add fallback tool chain."
        ),
    },
    "param_error": {
        "target_section": "Tool parameters",
        "strategy": (
            "Improve parameter handling: validate before tool calls,\n"
            "add default values for optional params, add type hints."
        ),
    },
    "llm_rubric_fail": {
        "target_section": "Evaluation rubric",
        "strategy": (
            "Improve answer quality: add explicit format requirements,\n"
            "add completeness checklist, enforce JSON schema validation."
        ),
    },
    "knowledge_recall_insufficient": {
        "target_section": "Knowledge retrieval",
        "strategy": (
            "Improve knowledge access: add mandatory knowledge lookup,\n"
            "add fallback to broader search terms, add reference format."
        ),
    },
    "format_invalid": {
        "target_section": "Output format",
        "strategy": (
            "Improve format compliance: add strict JSON schema,\n"
            "add format example in system prompt, reject non-compliant outputs."
        ),
    },
}



# ============================================================================
# ????
# ============================================================================

@dataclass
class PromptCandidate:
    """??????? prompt?"""
    candidate_id: str                    # ????????? hash + ????
    iteration: int                       # ??????0-based?
    target_prompt_type: str              # "system_prompt" | "skill_prompt" | "router_prompt"
    prompt_before: str                   # ?????
    prompt_after: str                    # ?????
    change_log: list[str] = field(default_factory=list)  # ??????
    failure_category: str = ""           # ?????????
    attribution_confidence: float = 0.0  # ?????
    estimated_cost: float = 0.0          # ??????

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "iteration": self.iteration,
            "target_prompt_type": self.target_prompt_type,
            "prompt_before": self.prompt_before,
            "prompt_after": self.prompt_after,
            "change_log": self.change_log,
            "failure_category": self.failure_category,
            "attribution_confidence": round(self.attribution_confidence, 3),
            "estimated_cost": round(self.estimated_cost, 6),
        }


@dataclass
class OptimizationResult:
    """???????"""
    candidates: list[PromptCandidate] = field(default_factory=list)
    total_iterations: int = 0
    strategy: str = "failure_driven"
    attribution_summary: dict = field(default_factory=dict)  # ????

    @property
    def latest_candidate(self) -> Optional[PromptCandidate]:
        return self.candidates[-1] if self.candidates else None

    @property
    def optimized_prompt(self) -> Optional[str]:
        """???????? prompt?? validator ??????"""
        c = self.latest_candidate
        return c.prompt_after if c else None

    @property
    def optimized_prompt_type(self) -> Optional[str]:
        c = self.latest_candidate
        return c.target_prompt_type if c else None

    def to_dict(self) -> dict:
        return {
            "candidates": [c.to_dict() for c in self.candidates],
            "total_iterations": self.total_iterations,
            "strategy": self.strategy,
            "attribution_summary": self.attribution_summary,
        }


# ============================================================================
# FakeOptimizer
# ============================================================================

class FakeOptimizer:
    """????????? Prompt ????

    ???????????????????? prompt ?????????
    ??? API ?????????????????

    ????:
        opt = FakeOptimizer()
        result = opt.optimize(attribution_report)
        print(result.latest_candidate.prompt_after)
    """

    def __init__(self, seed: int = 42):
        self.seed = seed
        self._iteration = 0

    def optimize(
        self,
        attribution_report: AttributionReport,
        max_iterations: int = 3,
    ) -> OptimizationResult:
        """???????? prompt ???

        Args:
            attribution_report: Phase 2 ????
            max_iterations: ??????

        Returns:
            OptimizationResult: ?????? prompt ?????
        """
        candidates: list[PromptCandidate] = []

        if not attribution_report.clusters:
            return OptimizationResult(
                candidates=candidates,
                total_iterations=0,
                attribution_summary={"note": "no failures to optimize"},
            )

        # ???????????
        priority_queue = self._build_priority_queue(attribution_report)

        for iteration, target in enumerate(priority_queue[:max_iterations]):
            self._iteration = iteration
            category = target["category"]
            prompt_type = target["prompt_target"]
            confidence = target["confidence"]

            # ???????
            prompt_before = self._get_base_prompt(prompt_type)

            # ???????
            prompt_after, change_log = self._generate_optimization(
                prompt_type, category, prompt_before, confidence
            )

            # ???? ID
            candidate_id = self._make_candidate_id(prompt_after, iteration)

            candidate = PromptCandidate(
                candidate_id=candidate_id,
                iteration=iteration,
                target_prompt_type=prompt_type,
                prompt_before=prompt_before,
                prompt_after=prompt_after,
                change_log=change_log,
                failure_category=category,
                attribution_confidence=confidence,
                estimated_cost=0.0005,  # fake ????????
            )
            candidates.append(candidate)

        attr_summary = {
            "primary_failure": attribution_report.primary_failure_category.category
            if attribution_report.primary_failure_category else "none",
            "total_failures": attribution_report.total_failures,
            "optimization_priority": attribution_report.optimization_priority,
        }

        return OptimizationResult(
            candidates=candidates,
            total_iterations=len(candidates),
            strategy="failure_driven",
            attribution_summary=attr_summary,
        )

    # ?? ???? ????????????????????????????????????????

    def _build_priority_queue(
        self, report: AttributionReport
    ) -> list[dict]:
        """??????????

        ?????????????????? prompt_target?
        """
        queue = []
        for cluster in sorted(report.clusters, key=lambda c: -c.count):
            if cluster.count == 0:
                continue
            queue.append({
                "category": cluster.category,
                "prompt_target": cluster.prompt_target,
                "confidence": cluster.avg_confidence,
                "count": cluster.count,
            })
        return queue

    def _get_base_prompt(self, prompt_type: str) -> str:
        """????????? prompt?"""
        return BASE_PROMPTS.get(prompt_type, f"# {prompt_type} prompt placeholder")

    def _generate_optimization(
        self,
        prompt_type: str,
        category: str,
        prompt_before: str,
        confidence: float,
    ) -> tuple[str, list[str]]:
        """???????????? prompt ???

        Returns:
            (prompt_after, change_log)
        """
        hints = CATEGORY_OPTIMIZATION_HINTS.get(category, {})
        strategy = hints.get("strategy", "????")

        change_log = [
            f"[{category}] confidence={confidence:.2f}",
            f"target: {prompt_type} ? {hints.get('target_section', 'general')}",
        ]

        # ????????? prompt ????? LLM ?????
        optimization_header = (
            f"\n\n<!-- ???? {self._iteration + 1} -->\n"
            f"## ????????????{category}?\n"
            f"{strategy}\n"
        )

        prompt_after = prompt_before + optimization_header

        # ??????
        for line in strategy.strip().split("\n"):
            line = line.strip().lstrip("- ")
            if line and not line.startswith("#"):
                change_log.append(f"  + {line}")

        return prompt_after, change_log

    @staticmethod
    def _make_candidate_id(prompt_text: str, iteration: int) -> str:
        """???? ID????? + ????"""
        content_hash = hashlib.sha256(prompt_text.encode()).hexdigest()[:12]
        ts = int(time.time() * 1000)
        return f"cand_{iteration}_{content_hash}_{ts}"


# ============================================================================
# OptimizationRunner?????
# ============================================================================

class OptimizationRunner:
    """????????

    ?? fake ? real ?????

    ????:
        runner = OptimizationRunner(mode="fake")
        result = runner.run(attribution_report)
        print(result.optimized_prompt)
    """

    def __init__(self, mode: str = "fake", config: Optional[dict] = None, **kwargs):
        if mode not in ("fake", "real"):
            raise ValueError(f"Unknown mode: {mode}. Must be 'fake' or 'real'.")
        if mode == "real":
            import warnings
            warnings.warn("OptimizationRunner real mode is not yet implemented. Use fake mode.", FutureWarning, stacklevel=2)
        self.mode = mode
        self.config = config or {}
        self.kwargs = kwargs
        self.max_iterations = self.config.get("max_iterations", 3)

        if mode == "fake":
            seed = self.config.get("random_seed", 42)
            self._optimizer = FakeOptimizer(seed=seed)

    def run(
        self,
        attribution_report: AttributionReport,
    ) -> OptimizationResult:
        """?????

        Args:
            attribution_report: Phase 2 ????

        Returns:
            OptimizationResult
        """
        if self.mode == "fake":
            return self._optimizer.optimize(
                attribution_report,
                max_iterations=self.max_iterations,
            )
        else:
            return self._run_real(attribution_report)

    def _run_real(
        self, attribution_report: AttributionReport
    ) -> OptimizationResult:
        """Real ????? trpc_agent.optimization.AgentOptimizer?"""
        try:
            from trpc_agent.optimization import AgentOptimizer
        except ImportError:
            raise ImportError(
                "Real mode requires trpc_agent.optimization. "
                "Install trpc-agent package or use mode='fake'."
            )
        # TODO: AgentOptimizer ???? tRPC-Agent SDK?
        raise NotImplementedError(
            "Real mode AgentOptimizer integration pending. Use fake mode."
        )


# ============================================================================
# ????
# ============================================================================

def run_optimization(
    attribution_report: AttributionReport,
    mode: str = "fake",
    config_path: Optional[str | Path] = None,
) -> OptimizationResult:
    """???????

    Args:
        attribution_report: Phase 2 ????
        mode: "fake" | "real"
        config_path: optimizer.json ??

    Returns:
        OptimizationResult
    """
    config = None
    if config_path:
        with open(config_path, "r", encoding="utf-8") as f:
            full = json.load(f)
        config = full.get("pipeline", {})

    runner = OptimizationRunner(mode=mode, config=config)
    return runner.run(attribution_report)
