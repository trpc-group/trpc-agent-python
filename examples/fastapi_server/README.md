# TRPC Agent FastAPI Server

本示例演示如何将 TRPC Agent 以 HTTP 服务的形式对外提供，支持同步和 SSE 流式两种接口，并提供 Python 交互式客户端进行快速验证。

## 关键特性

- **同步与流式双模式**：提供 `/v1/chat`（同步）和 `/v1/chat/stream`（SSE 流式）两种接口，满足不同场景需求
- **工具调用透传**：流式和同步响应均包含 `tool_call` / `tool_result` 事件，方便前端展示工具调用过程
- **会话上下文管理**：通过 `session_id` 延续历史对话，使用 `InMemorySessionService` 保存会话状态
- **自定义 Agent 加载**：支持通过 `--agent_module` 参数加载任意 Python 模块导出的 Agent，无需修改服务代码
- **CLI 与环境变量双入口**：模型凭据、服务器选项均支持命令行参数与环境变量两种配置方式
- **交互式客户端**：内置 Python 客户端，支持流式/同步模式切换、新会话创建等命令

## 目录结构与关键文件

```text
examples/fastapi_server/
├── run_server.py        # 独立启动脚本（直接 python3 运行）
├── __init__.py          # 包导出
├── _app.py              # FastAPI 路由工厂 + run_server
├── _runner_manager.py   # Agent / Runner 生命周期管理
├── _schemas.py          # Pydantic 请求/响应模型
├── agent/               # 示例 Agent（含天气查询工具）
│   ├── agent.py
│   ├── config.py        # 从环境变量读取模型配置
│   ├── prompts.py
│   └── tools.py         # get_weather_report()
└── test/
    └── client.py        # 交互式 Python 客户端
```

关键文件：

- [examples/fastapi_server/_app.py](./_app.py)：FastAPI 路由定义、SSE 流式处理、服务启动入口
- [examples/fastapi_server/_runner_manager.py](./_runner_manager.py)：Agent / Runner 生命周期管理，负责创建与缓存 Runner 实例
- [examples/fastapi_server/_schemas.py](./_schemas.py)：Pydantic 请求/响应模型定义
- [examples/fastapi_server/run_server.py](./run_server.py)：CLI 入口，解析命令行参数并启动服务
- [examples/fastapi_server/agent/agent.py](./agent/agent.py)：示例天气 Agent，挂载天气查询工具
- [examples/fastapi_server/test/client.py](./test/client.py)：交互式 Python 客户端

## 关键代码解释

这一节用于快速定位"路由、Runner 管理、自定义 Agent 加载"三条核心链路。

### 1) 路由工厂与服务启动（`_app.py`）

- 使用 FastAPI 定义 `/health`、`/v1/chat`、`/v1/chat/stream` 三个端点
- 同步接口等待 Agent 完整回复后返回 JSON；流式接口通过 SSE 逐步推送 `text_delta`、`tool_call`、`tool_result`、`done` 事件
- 服务启动函数 `run_server()` 封装 Uvicorn 配置，支持 IP / 端口等参数

### 2) Agent / Runner 生命周期管理（`_runner_manager.py`）

- `RunnerManager` 负责创建并缓存 Agent 与 Runner 实例
- 支持两种模式：自动创建默认 Assistant Agent，或通过 `--agent_module` 加载外部模块
- 使用 `InMemorySessionService` 管理会话状态

### 3) 请求/响应模型（`_schemas.py`）

- 定义 `ChatRequest`（message / session_id / user_id）和 `ChatResponse`（session_id / user_id / reply / tool_events）
- 统一同步和流式接口的输入格式

### 4) 自定义 Agent 加载

- 自定义模块需导出 `root_agent`（实例）或 `create_agent()`（工厂函数）
- 启动时通过 `--agent_module` 指定点分 Python 模块路径即可加载

```python
# 方式一：导出 root_agent 实例
root_agent = LlmAgent(name="my-agent", ...)

# 方式二：导出工厂函数
def create_agent() -> LlmAgent:
    return LlmAgent(name="my-agent", ...)
```

## API 端点与数据格式

### 端点列表

- **GET `/health`**：存活检测
- **POST `/v1/chat`**：同步聊天，等待完整回复后返回
- **POST `/v1/chat/stream`**：SSE 流式聊天，实时推送文本块

### 请求体（`/v1/chat` 与 `/v1/chat/stream` 共用）

```json
{
  "message": "你好",
  "session_id": "可选，不填则自动生成 UUID",
  "user_id": "user_001"
}
```

- **`message`**（string，必填）：用户发送的消息
- **`session_id`**（string，可选）：会话 ID，传入则延续历史上下文
- **`user_id`**（string，可选）：用户标识，默认 `"default"`

### SSE 事件格式

流式端点 `/v1/chat/stream` 的每条事件为 `data: <JSON>\n\n`：

- **`text_delta`**：Agent 回复的增量文本（data 为字符串）
- **`tool_call`**：Agent 调用工具（data 为 `{"name": "...", "args": {...}}`）
- **`tool_result`**：工具返回结果（data 为 `{"name": "...", "response": ...}`）
- **`done`**：流结束，正常退出（data 为 `null`）
- **`error`**：流中发生异常（data 为错误信息字符串）

## 环境与运行

### 环境要求

- Python 3.12

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 [examples/fastapi_server/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 启动参数说明

`run_server.py` 支持以下选项：

**模型凭据**

- **`--model_key KEY`**（环境变量 `TRPC_AGENT_API_KEY`，必填）：LLM 提供商 API Key
- **`--model_url URL`**（环境变量 `TRPC_AGENT_BASE_URL`）：LLM API Base URL，如 `https://api.openai.com/v1`
- **`--model_name NAME`**（环境变量 `TRPC_AGENT_MODEL_NAME`，默认 `gpt-4o-mini`）：模型名称

> CLI 参数优先级高于环境变量；使用 `--agent_module` 时模型参数由被加载的模块自行管理。

**服务器选项**

- **`--ip IP`**（环境变量 `TRPC_AGENT_HOST`，默认 `0.0.0.0`）：监听网卡地址
- **`--port PORT`**（环境变量 `TRPC_AGENT_PORT`，默认 `8080`）：监听端口
- **`--app_name NAME`**（环境变量 `TRPC_AGENT_APP_NAME`，默认 `trpc_agent_server`）：应用名称（出现在日志中）

**Agent 配置**

- **`--agent_module MODULE`**（环境变量 `TRPC_AGENT_MODULE`）：点分 Python 模块路径，需导出 `root_agent`（实例）或 `create_agent()`（工厂函数）；不设置则自动创建默认 Assistant Agent
- **`--instruction TEXT`**（环境变量 `TRPC_AGENT_INSTRUCTION`）：覆盖默认 Agent 的系统指令；`--agent_module` 存在时忽略

### 运行命令

```bash
cd examples/fastapi_server

# 使用环境变量提供凭据
export TRPC_AGENT_API_KEY=your-api-key
export TRPC_AGENT_BASE_URL=your-base-url
export TRPC_AGENT_MODEL_NAME=your-model-name
python3 run_server.py

# 或通过命令行参数直接传入
python3 run_server.py \
    --model_key   your-api-key \
    --model_url   your-base-url \
    --model_name  your-model-name \
    --ip          0.0.0.0 \
    --port        8080

# 加载自定义 Agent
python3 run_server.py --agent_module agent.agent --port 8080
```

## 运行结果（实测）

### 服务启动

```text
[2026-04-02 12:58:04][INFO][trpc_agent_sdk][examples/fastapi_server/_runner_manager.py:159][64629] Built default agent: model=your-model-name
[2026-04-02 12:58:04][INFO][trpc_agent_sdk][examples/fastapi_server/_runner_manager.py:59][64629] RunnerManager started: app=trpc_agent_server agent=assistant
[2026-04-02 12:58:04][INFO][trpc_agent_sdk][examples/fastapi_server/_app.py:279][64629] Starting TRPC Agent FastAPI server on 0.0.0.0:8080
INFO:     Started server process [64629]
INFO:     Waiting for application startup.
[2026-04-02 12:58:04][INFO][trpc_agent_sdk][examples/fastapi_server/_app.py:64][64629] TRPC Agent FastAPI server starting up.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8080 (Press CTRL+C to quit)
```

### 健康检查

```bash
curl http://127.0.0.1:8080/health
```

```json
{"status":"ok","app_name":"trpc_agent_server","version":"1.0.0"}
```

### 同步聊天

```bash
curl -X POST http://127.0.0.1:8080/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "你好，介绍一下你自己", "user_id": "user_001"}'
```

```json
{
  "session_id": "f490d6f3-72a1-4e8b-b3cd-2a1c9f3d7e05",
  "user_id": "user_001",
  "reply": "你好！我是一个 AI 助手，基于大语言模型构建，可以回答问题、协助分析、撰写内容等。有什么可以帮您的吗？",
  "tool_events": []
}
```

继续上一轮对话（传入 `session_id`）：

```bash
curl -X POST http://127.0.0.1:8080/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "你刚才说自己是什么？",
    "session_id": "f490d6f3-72a1-4e8b-b3cd-2a1c9f3d7e05",
    "user_id": "user_001"
  }'
```

### SSE 流式聊天

```bash
curl -X POST http://127.0.0.1:8080/v1/chat/stream \
  -H "Content-Type: application/json" \
  -N \
  -d '{"message": "请用中文写一首关于春天的短诗", "user_id": "user_001"}'
```

```text
data: {"type":"text_delta","data":"春风","session_id":"f490d6f3-..."}

data: {"type":"text_delta","data":"轻抚","session_id":"f490d6f3-..."}

data: {"type":"text_delta","data":"万物生，","session_id":"f490d6f3-..."}

data: {"type":"text_delta","data":"\n细雨","session_id":"f490d6f3-..."}

data: {"type":"done","data":null,"session_id":"f490d6f3-..."}
```

### 工具调用（天气 Agent）

先以天气 Agent 启动服务：

```bash
python3 run_server.py --agent_module agent.agent --port 8080
```

查询天气（可触发工具调用）：

```bash
curl -X POST http://127.0.0.1:8080/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "北京现在天气怎么样？", "user_id": "user_001"}'
```

```json
{
  "session_id": "a3b2c1d0-...",
  "user_id": "user_001",
  "reply": "北京现在天气晴朗，气温 25°C，湿度 60%，适合外出活动。",
  "tool_events": [
    {
      "type": "tool_call",
      "name": "get_weather_report",
      "data": {"city": "Beijing"}
    },
    {
      "type": "tool_result",
      "name": "get_weather_report",
      "data": {"temperature": "25°C", "condition": "Sunny", "humidity": "60%"}
    }
  ]
}
```

### Python 交互式客户端

```bash
# 默认：流式模式，连接 127.0.0.1:8080
python3 test/client.py --url http://127.0.0.1:8080 --user alice

# 同步模式（等全部回复后再显示）
python3 test/client.py --url http://127.0.0.1:8080 --user alice --sync
```

客户端参数：

- **`--url URL`**（默认 `http://127.0.0.1:8080`）：服务器地址
- **`--user USER`**（默认 `user_001`）：用户 ID
- **`--sync`**（默认流式）：切换为同步模式

内置命令：

- **`/new`**：开启新会话，清除上下文
- **`/sync`**：在流式 ↔ 同步模式间切换
- **`/help`**：显示帮助
- **`/quit`**：退出

```text
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 TRPC Agent Chat Client
 server  : http://127.0.0.1:8080
 user    : alice
 mode    : stream
 type /help for commands
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

You: 你好
Assistant: 你好！有什么可以帮您的吗？
  mode=streaming  session=f490d6f3…

You: 北京天气怎么样
Assistant:
  ⚙  tool_call   get_weather_report  {'city': 'Beijing'}
  ↩  tool_result  get_weather_report  {'temperature': '25°C', 'condition': 'Sunny', 'humidity': '60%'}
北京现在天气晴朗，气温 25°C，湿度 60%，适合外出活动。
  mode=streaming  session=f490d6f3…

You: /new
  ✓ new session started

You: /quit

Goodbye!
```

> **说明**：工具调用结果需使用天气 Agent（`--agent_module agent.agent`）启动服务才能触发；默认 Assistant Agent 不含工具。

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **健康检查正常**：`/health` 返回 `{"status":"ok"}`，服务启动成功
- **同步聊天正常**：`/v1/chat` 返回完整 JSON 响应，reply 内容合理
- **流式聊天正常**：`/v1/chat/stream` 按 SSE 格式推送 `text_delta` 与 `done` 事件
- **工具调用正确**：天气 Agent 能正确路由到 `get_weather_report`，返回结果与工具输出一致
- **会话延续正常**：传入 `session_id` 后能延续上下文对话
- **客户端功能完整**：流式/同步模式切换、新会话创建、工具调用展示均正常

## 适用场景建议

- 快速将 Agent 部署为 HTTP 服务：适合使用本示例
- 验证同步/SSE 流式接口与工具调用透传：适合使用本示例
- 需要交互式客户端进行手动测试：适合使用本示例内置客户端
- 需要加载自定义 Agent 模块：通过 `--agent_module` 参数即可扩展
- 需要测试单 Agent + Tool Calling 主链路（无 HTTP）：建议使用 `examples/llmagent`
