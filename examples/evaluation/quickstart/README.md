# Quickstart 天气 Agent 示例

最小评测示例：天气查询 Agent，仅包含一个 evalset（单用例）。

## 目录结构

- `quickstart/`：示例根目录
- `agent/`：内含 `agent.py`（`root_agent.name="weather_agent"`）、`weather_agent.evalset.json`、`config.py`、`test_config.json` 等

## 环境要求

Python 3.10+（建议 3.12）

## 环境变量

在 `.env` 或环境中设置：

- `TRPC_AGENT_API_KEY` 或 `API_KEY`
- `TRPC_AGENT_BASE_URL`（可选）
- `TRPC_AGENT_MODEL_NAME`（可选，默认 glm-4.7）

## 运行评测

```bash
cd examples/evaluation/quickstart
pytest test_quickstart.py -v --tb=short -s
```
