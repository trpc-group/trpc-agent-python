# Streaming Progress Tool 示例（长耗时工具实时进度流）

本示例演示如何使用 `StreamingProgressTool`，让长耗时工具在执行过程中实时向用户推送进度事件。

被包装的函数是 `async def` 生成器（`async def fn(...): yield ...`）。每次 `yield` 都会以 `partial=True` 的 Event 形式输出，并带有 `custom_metadata={"tool_progress": True, ...}`。**最后一次** `yield` 的值同时作为最终 `function_response` 返回给 LLM。

```text
yield progress_1       --> partial Event（实时进度）
yield progress_2       --> partial Event（实时进度）
yield progress_3       --> partial Event（实时进度）AND 最终 function_response
```

## 功能说明

- 使用 `StreamingProgressTool` 包装异步生成器工具
- 工具执行过程中实时输出进度事件（`tool_progress`）
- 最后一次 `yield` 作为最终工具结果回传给 LLM
- 演示客户端如何过滤并打印进度事件
- 与同类工具的区别：

| 类 | 流式内容 |
|---|---|
| `StreamingFunctionTool` | LLM 正在生成的工具**参数** |
| `LongRunningFunctionTool` | 无中间进度，仅标记调用耗时长 |
| **`StreamingProgressTool`** | 工具**自身执行进度** |

## 环境要求

- Python3.10+，推荐 Python3.12

## 构建步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
./build.sh
source .venv/bin/activate
```

## 运行步骤

### 配置环境变量

在 [examples/llmagent_with_streaming_progress_tool/.env](./.env) 中设置（也可通过 export）：

```bash
TRPC_AGENT_API_KEY=...
TRPC_AGENT_BASE_URL=...
TRPC_AGENT_MODEL_NAME=...
```

### 启动示例

```bash
cd examples/llmagent_with_streaming_progress_tool
python3 run_agent.py
```

## 运行结果（实测）

```text
+--------------------------------------------------------------+
|        StreamingProgressTool Demo (long-running tool)        |
|                                                              |
| Watch the tool yield progress events live, then the LLM      |
| summarises the final result.                                 |
+--------------------------------------------------------------+

============================================================
User: Please crawl https://example.com and fetch the first 5 pages.
============================================================
[tool-call] crawl_site({'url': 'https://example.com', 'max_pages': 5})
[crawl_site] ⏳ {'status': 'started', 'url': 'https://example.com', 'max_pages': 5}
[crawl_site] ⏳ {'status': 'fetched', 'page': 1, 'total': 5, 'title': 'https://example.com - page 1', 'progress': 0.2}
[crawl_site] ⏳ {'status': 'fetched', 'page': 2, 'total': 5, 'title': 'https://example.com - page 2', 'progress': 0.4}
[crawl_site] ⏳ {'status': 'fetched', 'page': 3, 'total': 5, 'title': 'https://example.com - page 3', 'progress': 0.6}
[crawl_site] ⏳ {'status': 'fetched', 'page': 4, 'total': 5, 'title': 'https://example.com - page 4', 'progress': 0.8}
[crawl_site] ⏳ {'status': 'fetched', 'page': 5, 'total': 5, 'title': 'https://example.com - page 5', 'progress': 1.0}
[crawl_site] ⏳ {'status': 'done', 'url': 'https://example.com', 'pages_fetched': 5, 'titles': [...]}
[tool-result] crawl_site → {'status': 'done', 'url': 'https://example.com', 'pages_fetched': 5, ...}
Assistant: I crawled example.com and fetched 5 pages. ...
------------------------------------------------------------
```

## 客户端消费进度事件

过滤 `event.partial` + `custom_metadata.tool_progress` 即可识别进度块。工具 `yield` 的原始值在 `custom_metadata['payload']` 中（`dict` / `BaseModel`）；纯文本场景也可读 `event.content.parts[0].text`。

```python
async for event in runner.run_async(...):
    meta = event.custom_metadata or {}
    if event.partial and meta.get("tool_progress"):
        print(meta["tool_name"], meta.get("payload") or event.get_text())
        continue
    # ... 按常规处理最终事件
```

说明：

- 进度事件不会写入会话历史（`partial=True`）
- LLM 只会看到**最后一次** `yield` 作为工具响应
- 若同一批次包含进度流工具，框架会强制串行执行工具，以保证中间事件顺序确定（即使 Agent 开启了 `parallel_tool_calls=True`）

## 文件说明

| 文件 | 说明 |
|---|---|
| `run_agent.py` | 示例入口（发起一次爬取请求并打印进度/结果） |
| `agent/agent.py` | Agent 定义（`LlmAgent` + `StreamingProgressTool`） |
| `agent/config.py` | 模型配置（从环境变量读取） |
| `agent/prompts.py` | Agent 提示词 |
| `agent/tools.py` | 模拟站点爬取工具（`crawl_site`） |
| `.env` | 环境变量配置文件 |
