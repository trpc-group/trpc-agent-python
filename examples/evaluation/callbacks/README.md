# 回调 Callbacks 示例

在评测中注册 **Callbacks**：在推理集/用例推理、打分集/用例打分的 8 个生命周期节点挂载钩子，用于打点、日志、采样或上报。

## 目录结构

- `callbacks/`：示例根目录
- `agent/`：内含 `agent.py`、`callbacks_example.evalset.json`、`test_config.json`、`config.py`
- `test_callbacks.py`：调用 `AgentEvaluator.evaluate(..., callbacks=callbacks)`，注册 `before_inference_set`、`after_inference_case`、`before_evaluate_set`、`after_evaluate_case` 并打日志

## 环境要求

Python 3.10+。需配置 `TRPC_AGENT_API_KEY` 等环境变量（同 quickstart）。

## 运行

```bash
cd examples/evaluation/callbacks
pytest test_callbacks.py -v --tb=short -s
```

`-s` 可看到回调中的 print 输出。
