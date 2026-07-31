"""Stage 3: 优化执行 — 运行 AgentOptimizer 或加载 demo 结果。

本阶段负责执行 prompt 优化，支持两种模式：
  - Real 模式: 调用 AgentOptimizer.optimize()，通过真实 LLM 进行多轮优化搜索
  - Demo 模式: 从预生成的 OptimizeResult JSON 加载结果（无需 API key）

优化器使用 gepa_reflective 算法，通过反思机制迭代改进 prompt。
优化过程产出最优 prompt 候选、pass rate 变化轨迹和成本统计。

调用方式：
    # Demo 模式
    report = OptimizationExecutor.run_demo("data/demo_optimize_result.json")

    # Real 模式
    report = await OptimizationExecutor.run_real(
        config_path="data/optimizer.json",
        call_agent=my_agent_callable,
        target_prompt=target_prompt,
        train_dataset_path="data/train_baseline.evalset.json",
        validation_dataset_path="data/val_baseline.evalset.json",
        output_dir="output/20260730_120000/",   # SDK 产物落在 .../optimizer/
    )
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trpc_agent_sdk.evaluation._optimize_result import OptimizeResult

from pipeline._models import OptimizationExecutionReport


class OptimizationExecutor:
    """优化执行器：支持真实优化和 demo 模式。

    Demo 模式适合快速验证流水线行为，无需 LLM 调用。
    Real 模式通过 AgentOptimizer 实际运行多轮优化搜索。
    """

    @staticmethod
    async def run_real(
        *,
        config_path: str,
        call_agent,
        target_prompt,
        train_dataset_path: str,
        validation_dataset_path: str,
        output_dir: str,
    ) -> OptimizationExecutionReport:
        """使用 AgentOptimizer 执行真实优化。

        通过 gepa_reflective 算法进行多轮迭代：
        每轮执行 → 失败归因 → 反思 → 生成候选 prompt → 重新评测 → 接受/拒绝。

        Args:
            config_path: optimizer.json 配置文件路径。
            call_agent: agent 推理的异步可调用对象。
            target_prompt: TargetPrompt 注册表，指定优化目标字段。
            train_dataset_path: 训练集 evalset 路径。
            validation_dataset_path: 验证集 evalset 路径。
            output_dir: 本次运行的时间戳目录；SDK 产物会落到其下的
                ``optimizer/`` 子目录，避免与流水线报告混在一起。

        Returns:
            OptimizationExecutionReport: 包含算法、轮次、pass rate 和成本信息。
        """
        from trpc_agent_sdk.evaluation._agent_optimizer import AgentOptimizer

        optimizer_subdir = Path(output_dir) / "optimizer"
        optimizer_subdir.mkdir(parents=True, exist_ok=True)

        result: OptimizeResult = await AgentOptimizer.optimize(
            config_path=config_path,
            call_agent=call_agent,
            target_prompt=target_prompt,
            train_dataset_path=train_dataset_path,
            validation_dataset_path=validation_dataset_path,
            output_dir=str(optimizer_subdir),
            update_source=False,
            verbose=1,  # 实时打印每轮进度，避免长时间盲等。
        )

        return OptimizationExecutor._from_optimize_result(result)

    @staticmethod
    def run_demo(demo_result_path: str) -> OptimizationExecutionReport:
        """从预生成的 OptimizeResult JSON 加载 demo 优化结果。

        适用于快速演示场景，无需 API key。

        Args:
            demo_result_path: demo_optimize_result.json 文件路径。

        Returns:
            OptimizationExecutionReport: 从 JSON 反序列化的优化报告。
        """
        result = OptimizeResult.from_file(demo_result_path)
        return OptimizationExecutor._from_optimize_result(result)

    @staticmethod
    def _from_optimize_result(result: OptimizeResult) -> OptimizationExecutionReport:
        """将 SDK OptimizeResult 转换为流水线内部的报告格式。

        同时透传审计字段（stop_reason / finish_reason / token 用量 /
        metric 调用次数 / 逐轮记录），供 Stage 6 审计轨迹消费。

        Args:
            result: SDK 返回的 OptimizeResult 实例。

        Returns:
            OptimizationExecutionReport: 流水线内部格式的优化报告。
        """
        return OptimizationExecutionReport(
            algorithm=result.algorithm,
            status=result.status,
            total_rounds=result.total_rounds,
            baseline_pass_rate=result.baseline_pass_rate,
            best_pass_rate=result.best_pass_rate,
            pass_rate_improvement=result.pass_rate_improvement,
            duration_seconds=result.duration_seconds,
            total_llm_cost=result.total_llm_cost,
            best_prompts=dict(result.best_prompts),
            stop_reason=getattr(result, "stop_reason", "") or "",
            finish_reason=getattr(result, "finish_reason", "") or "",
            total_token_usage=OptimizationExecutor._as_dict(
                getattr(result, "total_token_usage", None)
            ),
            total_metric_calls=OptimizationExecutor._metric_calls(result),
            rounds=[
                OptimizationExecutor._as_dict(r)
                for r in (getattr(result, "rounds", None) or [])
            ],
        )

    @staticmethod
    def _as_dict(value: Any) -> dict[str, Any]:
        """把 SDK 对象（Pydantic 模型 / dict / 其它）统一转成 dict。

        Pydantic 模型使用 ``by_alias=True`` 输出 camelCase 键，与 SDK
        JSON 产物保持一致；非模型对象退化为 JSON 可序列化形式。
        """
        if value is None:
            return {}
        if hasattr(value, "model_dump"):
            return value.model_dump(by_alias=True)
        if isinstance(value, dict):
            return dict(value)
        if hasattr(value, "__dict__"):
            return dict(vars(value))
        return json.loads(json.dumps(value, default=str))

    @staticmethod
    def _metric_calls(result: OptimizeResult) -> int:
        """读取 metric 调用总次数。

        新版 SDK 直接暴露 ``total_metric_calls``；当前版本把它放在
        ``extras`` 里（由 gepa 回填），再退化到 judge model 调用次数。
        """
        direct = getattr(result, "total_metric_calls", None)
        if direct is not None:
            return int(direct)
        extras = getattr(result, "extras", None) or {}
        if isinstance(extras, dict) and extras.get("total_metric_calls") is not None:
            return int(extras["total_metric_calls"])
        return int(getattr(result, "total_judge_model_calls", 0) or 0)
