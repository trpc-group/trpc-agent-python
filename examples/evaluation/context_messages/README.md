# 上下文注入示例

在评测用例上配置 **context_messages**：评估服务在每轮推理前会将其中内容注入会话上下文，用于传递系统说明、领域知识或格式约束。

## 目录结构

- `context_messages/`：示例根目录
- `agent/`：内含 `agent.py`、`context_example.evalset.json`（含带 context_messages 的用例）、`test_config.json`、`config.py`
- `test_context_messages.py`：调用 `AgentEvaluator.evaluate` 跑评测

## 环境要求

Python 3.10+。需配置 `TRPC_AGENT_API_KEY` 等环境变量（同 quickstart）。

## 运行

```bash
cd examples/evaluation/context_messages
pytest test_context_messages.py -v --tb=short -s
```
