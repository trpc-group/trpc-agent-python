# WebUI 书籍查找 Agent 示例

按优先级查找书籍：本地图书馆 → 本地书店 → 在线零售商。

## 目录结构

- `webui/`：`--agents` 指向此目录
- `agent/`：子目录名须与 `root_agent.name` 一致（`"agent"`），内含 `agent.py`、`agent.evalset.json`、`config.py`、`prompts.py`、`tools.py`、`test_config.json` 等

## 环境要求

Python 3.10+（建议 3.12）

## 环境变量

在 `.env` 中或通过 `export` 设置：

- `TRPC_AGENT_API_KEY` 或 `API_KEY`
- `TRPC_AGENT_BASE_URL`（可选，有默认值）
- `TRPC_AGENT_MODEL_NAME`（可选，有默认值）

## 运行示例

```bash
cd examples/evaluation/webui
python run_agent.py
```

## 运行评估测试

```bash
cd examples/evaluation/webui
pytest test_book_finder.py -v --tb=short -s
```

需已设置上述环境变量。
