# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""pipeline.json 配置模型：闸门阈值 / 复现实验参数。

所有闸门都可以在 ``pipeline.json`` 里按业务需要调整；默认值即本 example
演示三场景所用的取值。``PipelineConfig.load`` 从 JSON 文件读入并做 pydantic
校验，非法字段/类型会在 pipeline 启动前 fail-fast（``extra="forbid"``：
写错闸门名不会被静默忽略成默认阈值 —— 对安全闸门而言静默降级比报错危险）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class GateConfig(BaseModel):
    """接受策略（阶段⑤）的六道闸门配置，全部可按业务调整。"""

    model_config = ConfigDict(extra="forbid")

    min_val_pass_rate_improvement: float = Field(
        default=1e-9,
        description="验证集通过率最小提升。默认要求「严格大于 0」；调大即要求显著提升。",
    )
    min_val_score_improvement: float = Field(
        default=0.0,
        description="验证集平均 metric 分最小提升（第二信号，默认不允许下降）。",
    )
    forbid_new_hard_fail: bool = Field(
        default=True,
        description="不允许出现 baseline 通过、candidate 失败的 case（新增 hard fail）。",
    )
    protected_cases: list[str] = Field(
        default_factory=lambda: ["val_identity"],
        description="关键 case 白名单：任何一条出现 new_fail / score_down 即拒绝。",
    )
    max_cost_usd: float = Field(
        default=1.0,
        description="优化过程 LLM 成本预算（对照 OptimizeResult.total_llm_cost）。",
    )
    max_metric_calls: Optional[int] = Field(
        default=None,
        description="预算的第二形态：优化器 metric 调用数上限（对照 rounds[-1].budget_used）。",
    )
    max_duration_seconds: float = Field(
        default=180.0,
        description="整条 pipeline 的墙钟时长预算（秒）。",
    )
    overfit_guard: bool = Field(
        default=True,
        description="过拟合守卫：训练集通过率提升且验证集通过率下降 → 拒绝。",
    )


class PipelineConfig(BaseModel):
    """pipeline.json 的顶层模型。"""

    model_config = ConfigDict(extra="forbid")

    gates: GateConfig = Field(default_factory=GateConfig)
    seed: int = Field(default=42, description="记录进报告的随机种子；与 optimizer.json 的 algorithm.seed 保持一致。")
    score_epsilon: float = Field(default=1e-6, description="逐 case 分数对比的浮点容差。")

    @classmethod
    def load(cls, path: str | Path) -> "PipelineConfig":
        """从 JSON 文件读入并校验。"""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)
