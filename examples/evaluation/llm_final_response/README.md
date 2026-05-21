# llm_final_response 评估器示例

使用 **llm_final_response** 指标：由裁判模型对比 Agent 实际最终回答与评测集中的参考答案，给出是否一致的判定。

## 目录结构

- `agent/`：Agent 模块（`agent.py`、`config.py`）、评测集 `llm_final_response.evalset.json`、`test_config.json`
- `test_llm_final_response.py`：pytest 入口

## 环境变量

- `TRPC_AGENT_API_KEY` 或 `API_KEY`（必填，Agent 与裁判模型共用）
- `TRPC_AGENT_BASE_URL`（可选）
- `TRPC_AGENT_MODEL_NAME`（可选，默认 glm-4-flash）

## 运行

```bash
cd examples/evaluation/llm_final_response
pytest test_llm_final_response.py -v --tb=short -s
```

评测集用例中需提供预期的 `final_response`，裁判模型将实际回答与该参考对比后输出 valid/invalid。
