# llm_rubric_knowledge_recall 评估器示例

使用 **llm_rubric_knowledge_recall** 指标：从 Agent 实际轨迹中提取**知识检索类工具**（默认 `knowledge_search`）的调用结果，由裁判模型根据 **rubrics** 判定检索内容是否足以支撑问题或细则，适用于 RAG 召回质量评估。

## 目录结构

- `agent/`：Agent 带 `knowledge_search` 工具、评测集 `llm_rubric_knowledge_recall.evalset.json`、`test_config.json`（含 `rubrics` 与可选 `knowledge_tool_names`）
- `test_llm_rubric_knowledge_recall.py`：pytest 入口

## 环境变量

- `TRPC_AGENT_API_KEY` 或 `API_KEY`（必填）
- `TRPC_AGENT_BASE_URL`（可选）
- `TRPC_AGENT_MODEL_NAME`（可选，默认 glm-4-flash）

## 运行

```bash
cd examples/evaluation/llm_rubric_knowledge_recall
pytest test_llm_rubric_knowledge_recall.py -v --tb=short -s
```

Agent 必须在实际运行中调用 `knowledge_search`（或你在 `knowledge_tool_names` 中配置的工具名），否则轨迹中无检索结果，裁判无法稳定打分。本示例中 `knowledge_search` 返回模拟文档，裁判据此与 rubrics 判定。
