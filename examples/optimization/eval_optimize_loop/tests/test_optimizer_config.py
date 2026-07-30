"""optimizer.json 配置校验测试。

Stage 3 的 real 模式通过 ``AgentOptimizer.optimize(call_agent=...)`` 运行，
属于黑盒模式：call_agent 只返回最终文本，SDK 拿不到工具调用轨迹和
``intermediate_data``。因此 optimizer.json 的 evaluate.metrics 不能包含
依赖 trace 的 metric，否则 SDK 在启动时 fail-fast 抛 ValueError。

注意：这与 test_config.json 不同——后者用于 trace 模式评测（Stage 1/4），
预录制的 evalset 自带轨迹，可以使用 tool_trajectory_avg_score。
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


def test_eval_config_keeps_trajectory_metric():
    """test_config.json 走 trace 模式，应保留 tool_trajectory_avg_score。"""
    config = json.loads((DATA_DIR / "test_config.json").read_text(encoding="utf-8"))
    names = {m["metric_name"] for m in config["metrics"]}

    assert "tool_trajectory_avg_score" in names
