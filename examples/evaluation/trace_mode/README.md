# Trace 模式示例

使用 **eval_mode: "trace"**：不调用 Agent 推理，用 evalset 中的 **actual_conversation** 作为「实际轨迹」参与打分，**conversation** 作为预期用于对比。适合回放已有对话、离线评估。

## 目录结构

- `trace_mode/`：示例根目录
- `agent/`：内含 `agent.py`、`trace_example.evalset.json`（含 trace 用例）、`test_config.json`、`config.py`
- `test_trace_mode.py`：调用 `AgentEvaluator.evaluate`，仅执行打分阶段

## 环境要求

Python 3.10+。Trace 模式不跑模型推理，但框架仍会加载 agent 模块；若未配置 `TRPC_AGENT_API_KEY`，加载可能报错，可按需配置或仅用於查看結構。

## 运行

```bash
cd examples/evaluation/trace_mode
pytest test_trace_mode.py -v --tb=short -s
```
