"""Optimization stage — wraps AgentOptimizer for GEPA-based prompt optimization.

Supports two execution modes:
- fake: Simulates GEPA iterations with deterministic improvements, no API calls.
- live: Calls AgentOptimizer.optimize() with real GEPA reflective algorithm.

Records per-round optimization results for audit trail.
"""

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any

from .attribution import AttributionReport
from .config import PipelineConfig
from ._paths import ensure_example_and_repo_in_path


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
    # 候选生成策略：fix_attributed（可优化成功）/ noop（优化无效）/ overfit（过拟合退化）
    candidate_strategy: str = "fix_attributed"
    # 被修复的失败类别（fix_attributed 场景下）
    fixed_categories: list[str] = field(default_factory=list)

    @property
    def best_score(self) -> float:
        if not self.rounds:
            return 0.0
        return max(r.score for r in self.rounds)


# 候选场景注册表：场景名 → 描述
SCENARIOS = {
    "fix_attributed": "候选修复了归因的失败类别 → 优化成功",
    "noop": "候选未做实质改动 → 优化无效",
    "overfit": "候选在 train 上提升但 val 回归 → 过拟合",
}


def run_optimize_fake(
    attribution: AttributionReport,
    config: PipelineConfig,
    *,
    scenario: str = "fix_attributed",
) -> OptimizeResult:
    """Run optimization in fake mode — simulate GEPA iterations.

    In fake mode, each "round" deterministically improves by fixing
    one category of failures identified in attribution. This simulates
    the reflective mutation behavior of real GEPA without API calls.

    场景参数 `scenario` 控制候选的生成策略：
    - fix_attributed（默认）：候选修复归因的失败类别 → 优化成功
    - noop：候选未做实质改动 → 优化无效
    - overfit：候选在 train 上提升但 val 回归 → 过拟合

    Args:
        attribution: Failure attribution from baseline evaluation.
        config: Pipeline configuration.
        scenario: 候选生成策略名。

    Returns:
        OptimizeResult with simulated round records.
    """
    result = OptimizeResult(algorithm=config.algorithm)
    result.candidate_strategy = scenario

    if scenario == "noop":
        # 优化无效：无实际改进，返回空优化结果
        result.converged = False
        result.total_iterations = 0
        result.optimized_fields = []
        result.best_prompt = {}
        return result

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

    # overfit 场景：模拟"记住 train"。轮数与 fix_attributed 一致（以类别数为上界），
    # 过拟合的 val 退化由 validate.py 的场景扰动驱动，不依赖优化轮数。
    max_rounds = min(config.max_iterations, len(categories_to_fix))

    optimized_fields = set()
    prompt_changes: dict[str, str] = {}

    for i in range(max_rounds):
        cat_name, cat_count = categories_to_fix[i % len(categories_to_fix)] if categories_to_fix else ("unknown", 0)
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
    result.fixed_categories = [cat for cat, _ in categories_to_fix[:max_rounds]]
    # 收敛按归因覆盖率判定：所有待修类别都已覆盖才算收敛，
    # 而非"未用满迭代轮数"（那可能是被 max_iterations 截断的未完成状态）。
    result.converged = len(result.fixed_categories) >= len(categories_to_fix)

    return result


async def run_optimize_live(
    optimizer_config_path: str,
    config: PipelineConfig,
    call_agent: Any | None = None,
) -> OptimizeResult:
    """Run optimization using real AgentOptimizer (GEPA reflective).

    正确调用 SDK 的 `AgentOptimizer.optimize`（async classmethod，需
    `call_agent` 参数）。默认使用 agent.build_call_agent() 提供确定性
    离线执行（无需 API key）；配置了 TRPC_AGENT_API_KEY 时可跑真实模型。

    Args:
        optimizer_config_path: Path to optimizer.json.
        config: Pipeline configuration.
        call_agent: 可选，SDK CallAgent 签名（Async callable）。

    Returns:
        OptimizeResult from actual GEPA run.
    """
    result = OptimizeResult(algorithm=config.algorithm)

    try:
        # 确保项目根与 example 目录在 sys.path
        ensure_example_and_repo_in_path()
        from trpc_agent_sdk.evaluation import AgentOptimizer, TargetPrompt

        if call_agent is None:
            from agent.agent import build_call_agent
            call_agent = build_call_agent()

        # Register target prompts for optimization。
        # prompt_dir 默认为相对路径 "data/prompts"，按 CWD 解析——若非从 example 目录
        # 运行 live 会解析失败；相对路径锚定到本模块（pipeline/）的上一级即 example 目录。
        prompt_dir = config.prompt_dir
        if not os.path.isabs(prompt_dir):
            prompt_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                prompt_dir,
            )
        target = TargetPrompt()
        if os.path.isdir(prompt_dir):
            for fname in os.listdir(prompt_dir):
                if fname.endswith(".md"):
                    field_name = fname.replace(".md", "")
                    target.add_path(field_name, os.path.join(prompt_dir, fname))

        # 明确提示 reflection_lm 未配置真实 LLM：live GEPA 必然失败并降级，
        # 避免配置中的空占位被误认为可工作的 fake 模型；同时读取 timeout 配置
        _optimize_timeout = 600.0
        try:
            # 用 SDK 公开导出，避免耦合私有模块（同 pipeline/config.py 做法）
            from trpc_agent_sdk.evaluation import load_optimize_config as _sdk_load
            _oc = _sdk_load(optimizer_config_path)
            _rl = _oc.optimize.algorithm.reflection_lm
            _timeout_cfg = getattr(_oc.optimize.algorithm, "timeout_seconds", None)
            _optimize_timeout = float(_timeout_cfg) if _timeout_cfg is not None else _optimize_timeout
            if not (_rl.provider_name and _rl.model_name):
                print("  ⚠️  reflection_lm 未配置真实 LLM（provider_name/model_name 为空），"
                      "live GEPA 优化将失败并降级为空结果")
        except Exception as _e:
            print(f"  ⚠️  无法检查 reflection_lm 配置（{type(_e).__name__}: {_e}）")

        # Run optimization（async，需 await；显式传 call_agent）。
        # 用 wait_for 加超时：真实网络/LLM 调用可能卡顿，避免整条 live 流水线
        # 无限挂起（超时抛 TimeoutError，下方 except 写入 result.errors）
        opt_result = await asyncio.wait_for(
            AgentOptimizer.optimize(
                config_path=optimizer_config_path,
                call_agent=call_agent,
                target_prompt=target,
                train_dataset_path=config.train_evalset,
                validation_dataset_path=config.val_evalset,
                output_dir=config.output_dir,
            ),
            timeout=_optimize_timeout,
        )

        # Extract results（按 SDK OptimizeResult 实际字段映射）
        result.total_cost = getattr(opt_result, 'total_llm_cost', 0.0)
        result.total_iterations = getattr(opt_result, 'total_rounds', 0)
        # SDK status 取值为 SUCCEEDED / FAILED / CANCELED
        opt_status = getattr(opt_result, 'status', '') or ''
        result.converged = opt_status == 'SUCCEEDED'
        result.optimized_fields = []
        result.fixed_categories = []

        best_prompts = getattr(opt_result, 'best_prompts', None)
        if best_prompts:
            result.best_prompt = dict(best_prompts)
            result.optimized_fields = list(best_prompts.keys())

        if opt_status != 'SUCCEEDED':
            # 非成功（含 status 为空/未提供）的运行：清空优化产物，避免把
            # 失败/未确知成功的 "best" 当可回写产物
            result.best_prompt = {}
            result.optimized_fields = []
            result.errors.append(
                f"AgentOptimizer did not succeed (status={opt_status!r}); "
                f"discarding best_prompt/optimized_fields")

        rounds = getattr(opt_result, 'rounds', None)
        if rounds:
            result.rounds = [
                RoundRecord(
                    round_index=getattr(r, 'round', i + 1),
                    score=getattr(r, 'validation_pass_rate', 0.0),
                    best_so_far=getattr(r, 'validation_pass_rate', 0.0),
                    prompt_changes=list(getattr(r, 'optimized_field_names', []) or []),
                )
                for i, r in enumerate(rounds)
            ]

    except ImportError as e:
        result.errors.append(
            f"SDK AgentOptimizer not available: {e}. "
            f"Install with: pip install trpc-agent-python[gepa]"
        )
    except (ValueError, KeyError, TypeError) as e:
        # 已知的 SDK 评测/字段问题 → 记录 error 并返回空结果
        result.errors.append(f"Optimization failed: {e}")
    except asyncio.TimeoutError:
        # wait_for 超时：网络卡顿/慢 LLM 不应让流水线无限挂起
        result.errors.append(
            f"AgentOptimizer timed out after {_optimize_timeout:.0f}s")
    # 其余非预期异常（AttributeError 等 pipeline 自身 bug）向上抛出，
    # 由 run_pipeline 的 try/except 捕获降级，避免把代码缺陷伪装成"优化失败"

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
