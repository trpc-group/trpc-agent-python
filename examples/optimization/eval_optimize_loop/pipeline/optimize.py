"""Optimization stage — wraps AgentOptimizer for GEPA-based prompt optimization.

Supports two execution modes:
- fake: Simulates GEPA iterations with deterministic improvements, no API calls.
- live: Calls AgentOptimizer.optimize() with real GEPA reflective algorithm.

Records per-round optimization results for audit trail.
"""

import os
import time
from dataclasses import dataclass, field
from typing import Any

from .attribution import AttributionReport
from .config import PipelineConfig


@dataclass
class RoundRecord:
    """A single round of optimization."""
    round_index: int
    score: float
    best_so_far: float
    prompt_changes: list[str] = field(default_factory=list)
    cost: float = 0.0
    duration_ms: float = 0.0


@dataclass
class OptimizeResult:
    """Result of the optimization stage."""
    algorithm: str = "gepa_reflective"
    rounds: list[RoundRecord] = field(default_factory=list)
    best_prompt: dict[str, str] = field(default_factory=dict)
    optimized_fields: list[str] = field(default_factory=list)
    total_cost: float = 0.0
    total_duration_ms: float = 0.0
    total_iterations: int = 0
    converged: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def best_score(self) -> float:
        if not self.rounds:
            return 0.0
        return max(r.score for r in self.rounds)


def run_optimize_fake(
    attribution: AttributionReport,
    config: PipelineConfig,
) -> OptimizeResult:
    """Run optimization in fake mode — simulate GEPA iterations.

    In fake mode, each "round" deterministically improves by fixing
    one category of failures identified in attribution. This simulates
    the reflective mutation behavior of real GEPA without API calls.

    Args:
        attribution: Failure attribution from baseline evaluation.
        config: Pipeline configuration.

    Returns:
        OptimizeResult with simulated round records.
    """
    result = OptimizeResult(algorithm=config.algorithm)

    if attribution.total_failures == 0:
        # No failures to fix — optimization has nothing to do
        result.converged = True
        result.optimized_fields = []
        result.best_prompt = {}
        return result

    # Determine categories to fix, sorted by severity (most failures first)
    categories_to_fix = sorted(
        attribution.by_category.items(),
        key=lambda x: x[1],
        reverse=True,
    )

    # Simulate GEPA rounds: each round fixes one category
    max_rounds = min(config.max_iterations, len(categories_to_fix))
    optimized_fields = set()
    prompt_changes: dict[str, str] = {}

    for i in range(max_rounds):
        cat_name, cat_count = categories_to_fix[i]
        start = time.monotonic()

        # Simulate improvement: each fixed category adds to the score
        base_score = 0.5  # Assume baseline starts at 50%
        fix_contribution = (cat_count / attribution.total_failures) * 0.5
        score = min(1.0, base_score + fix_contribution * (i + 1))
        best_so_far = score

        # Simulate prompt changes from reflective mutation
        changes = [_simulate_prompt_change(cat_name)]
        optimized_fields.add("system.md")

        cost = 0.01 * cat_count  # Simulate cheap GEPA cost
        duration = time.monotonic() - start

        prompt_changes[cat_name] = changes[0]

        result.rounds.append(RoundRecord(
            round_index=i + 1,
            score=score,
            best_so_far=best_so_far,
            prompt_changes=changes,
            cost=cost,
            duration_ms=round(duration * 1000, 1),
        ))
        result.total_cost += cost
        result.total_duration_ms += duration * 1000

    result.total_iterations = max_rounds
    result.optimized_fields = sorted(optimized_fields)
    result.best_prompt = {"system.md": _build_optimized_prompt(prompt_changes)}
    result.converged = result.total_iterations < config.max_iterations

    return result


def run_optimize_live(
    optimizer_config_path: str,
    config: PipelineConfig,
) -> OptimizeResult:
    """Run optimization using real AgentOptimizer (GEPA reflective).

    This path requires:
    - gepa package installed (pip install trpc-agent-python[gepa])
    - Valid API keys configured
    - Agent module importable

    Args:
        optimizer_config_path: Path to optimizer.json.
        config: Pipeline configuration.

    Returns:
        OptimizeResult from actual GEPA run.
    """
    result = OptimizeResult(algorithm=config.algorithm)

    try:
        from trpc_agent_sdk.evaluation import AgentOptimizer, TargetPrompt

        # Register target prompts for optimization
        target = TargetPrompt()
        prompt_dir = config.prompt_dir
        if os.path.isdir(prompt_dir):
            for fname in os.listdir(prompt_dir):
                if fname.endswith(".md"):
                    field_name = fname.replace(".md", "")
                    target.add_path(field_name, os.path.join(prompt_dir, fname))

        # Run optimization
        opt_result = AgentOptimizer.optimize(
            config_path=optimizer_config_path,
            target_prompt=target,
            train_dataset_path=config.train_evalset,
            validation_dataset_path=config.val_evalset,
            output_dir=config.output_dir,
        )

        # Extract results
        result.total_cost = getattr(opt_result, 'total_cost', 0.0)
        result.converged = getattr(opt_result, 'converged', False)
        result.total_iterations = getattr(opt_result, 'total_iterations', 0)
        result.optimized_fields = getattr(opt_result, 'optimized_fields', [])

        if hasattr(opt_result, 'best_prompt'):
            result.best_prompt = opt_result.best_prompt
        if hasattr(opt_result, 'rounds'):
            result.rounds = [
                RoundRecord(
                    round_index=getattr(r, 'index', i),
                    score=getattr(r, 'score', 0.0),
                    best_so_far=getattr(r, 'best_so_far', 0.0),
                )
                for i, r in enumerate(opt_result.rounds)
            ]

    except ImportError as e:
        result.errors.append(
            f"SDK AgentOptimizer not available: {e}. "
            f"Install with: pip install trpc-agent-python[gepa]"
        )
    except Exception as e:
        result.errors.append(f"Optimization failed: {e}")

    return result


def _simulate_prompt_change(category: str) -> str:
    """Generate a simulated prompt change for a failure category.

    This mimics what GEPA's reflective mutation would produce.
    """
    changes = {
        "final_response_mismatch": (
            "Added: 'Ensure the final answer matches the expected format exactly. "
            "Use precise numerical values without extra commentary.'"
        ),
        "tool_call_error": (
            "Added: 'When using tools, always validate parameters before calling. "
            "Check argument types and required fields.'"
        ),
        "wrong_tool_selected": (
            "Added: 'Before invoking a tool, verify it is the correct one for the task. "
            "Review available tools and their descriptions.'"
        ),
        "tool_parameter_error": (
            "Added: 'Double-check all tool parameters. Ensure numeric arguments are "
            "correctly typed and string arguments are properly formatted.'"
        ),
        "llm_rubric_not_met": (
            "Added: 'Responses must meet quality standards: clarity, completeness, "
            "and correctness. Include step-by-step reasoning when appropriate.'"
        ),
        "knowledge_recall_insufficient": (
            "Added: 'Leverage available knowledge sources before responding. "
            "Cross-reference facts when uncertain.'"
        ),
        "format_not_as_required": (
            "Added: 'Output must follow the specified format strictly. "
            "Use the required structure: fields, delimiters, and encoding.'"
        ),
        "missing_expected_output": (
            "Added: 'Always produce complete output. Do not truncate responses. "
            "Include all expected sections and calculations.'"
        ),
        "unknown": (
            "Added: 'Review and improve response quality. Identify and correct "
            "any inconsistencies in reasoning or output.'"
        ),
    }
    return changes.get(
        category,
        f"Optimized for: {category} — improved handling based on failure analysis.",
    )


def _build_optimized_prompt(changes: dict[str, str]) -> str:
    """Build a simulated optimized system prompt from category-specific changes.

    Args:
        changes: Mapping from failure category to prompt change text.

    Returns:
        Full optimized system prompt string.
    """
    header = (
        "# Optimized System Prompt\n\n"
        "This prompt was automatically optimized based on failure attribution.\n\n"
        "## Instructions\n\n"
    )

    instructions = []
    for cat, change in changes.items():
        instructions.append(f"<!-- Fix for: {cat} -->\n{change}")

    footer = (
        "\n\n## Original Baseline\n\n"
        "Answer the user's question accurately and concisely. "
        "Show your work when performing calculations."
    )

    return header + "\n\n".join(instructions) + footer
