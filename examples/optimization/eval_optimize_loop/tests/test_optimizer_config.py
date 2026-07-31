"""optimizer.json / gate_metrics.json 两层 metric 策略校验。

Stage 3 的 real 模式通过 ``AgentOptimizer.optimize(call_agent=...)`` 运行，
属于黑盒模式：call_agent 只返回最终文本，SDK 拿不到工具调用轨迹和
``intermediate_data``。因此 optimizer.json 的 evaluate.metrics 不能包含
依赖 trace 的 metric，否则 SDK 在启动时 fail-fast 抛 ValueError。

而 gate_metrics.json 走 trace 模式（Stage 1/4），预录制的 evalset 自带轨迹，
所以可以同时使用 final_response_avg_score 与 tool_trajectory_avg_score。

两层关系：gate_metrics 是 optimizer_metrics 的超集 —— gate 能看到 optimizer
看不到的退化信号（工具调用序列）。
"""

import json
from pathlib import Path

from trpc_agent_sdk.evaluation._agent_optimizer import (
    _DISALLOWED_METRICS_IN_CALL_AGENT_MODE,
)

DATA_DIR = Path(__file__).parent.parent / "data"


def test_optimizer_metrics_compatible_with_call_agent_mode():
    """optimizer.json 不得配置黑盒模式无法采集的 metric。"""
    config = json.loads((DATA_DIR / "optimizer.json").read_text(encoding="utf-8"))
    names = {m["metric_name"] for m in config["evaluate"]["metrics"]}

    assert not (names & _DISALLOWED_METRICS_IN_CALL_AGENT_MODE)
    assert names, "optimizer.json 至少需要保留一个可用于优化的 metric"


def test_gate_metrics_keeps_trajectory_metric():
    """gate_metrics.json 走 trace 模式，应保留 tool_trajectory_avg_score。

    两层 metric 策略的核心断言：门控配置可以（也应该）保留 optimizer
    不允许的 metric —— 因为门控跑的是 trace 模式，SDK 能拿到工具调用轨迹。
    """
    config = json.loads((DATA_DIR / "gate_metrics.json").read_text(encoding="utf-8"))
    names = {m["metric_name"] for m in config["metrics"]}

    assert "tool_trajectory_avg_score" in names
