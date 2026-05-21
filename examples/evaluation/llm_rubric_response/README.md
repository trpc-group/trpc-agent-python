# llm_rubric_response 评估器示例

使用 **llm_rubric_response** 指标：裁判模型根据配置的 **rubrics**（评估细则）逐条判定 Agent 最终回答是否满足，单轮分数为各细则得分平均值。

## 目录结构

- `agent/`：Agent 模块、评测集 `llm_rubric_response.evalset.json`、`test_config.json`（含 `rubrics`）
- `test_llm_rubric_response.py`：pytest 入口

## 环境变量

- `TRPC_AGENT_API_KEY` 或 `API_KEY`（必填）
- `TRPC_AGENT_BASE_URL`（可选）
- `TRPC_AGENT_MODEL_NAME`（可选，默认 glm-4-flash）

## 运行

```bash
cd examples/evaluation/llm_rubric_response
pytest test_llm_rubric_response.py -v --tb=short -s
```

`test_config.json` 中需配置 `criterion.llm_judge.judge_model` 与 `criterion.llm_judge.rubrics`，每条 rubric 的 `content.text` 会展示给裁判模型用于判定。
