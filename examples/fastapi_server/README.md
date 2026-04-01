# TRPC Agent FastAPI Server

将 TRPC Agent 以 HTTP 服务的形式对外提供，支持同步和 SSE 流式两种接口。

## 目录结构

```
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

---

## 快速开始

### 1. 安装依赖

```bash
pip3 install trpc_agent
```

### 2. 启动服务

在 `examples/fastapi_server` 目录下直接运行 `run_server.py`：

```bash
cd examples/fastapi_server

# 使用环境变量提供凭据
export TRPC_AGENT_API_KEY=your-api-key
export TRPC_AGENT_BASE_URL=http://v2.open.venus.woa.com/llmproxy
export TRPC_AGENT_MODEL_NAME=deepseek-v3-local-II
python3 run_server.py

# 或通过命令行参数直接传入
python3 run_server.py \
    --model_key   your-api-key \
    --model_url   http://v2.open.venus.woa.com/llmproxy \
    --model_name  deepseek-v3-local-II \
    --ip          0.0.0.0 \
    --port        8080
```

启动成功后终端输出：

```
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
INFO:     TRPC Agent FastAPI server starting up.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8080 (Press CTRL+C to quit)
```

---

## 启动参数说明

`run_server.py` 支持以下选项：

```
python3 run_server.py [OPTIONS]
```

### 模型凭据

| 参数 | 环境变量回退 | 默认值 | 说明 |
|---|---|---|---|
| `--model_key KEY` | `TRPC_AGENT_API_KEY` | —（必填） | LLM 提供商 API Key |
| `--model_url URL` | `TRPC_AGENT_BASE_URL` | — | LLM API Base URL，如 `https://api.openai.com/v1` |
| `--model_name NAME` | `TRPC_AGENT_MODEL_NAME` | `gpt-4o-mini` | 模型名称 |

> CLI 参数优先级高于环境变量；使用 `--agent_module` 时模型参数由被加载的模块自行管理。

### 服务器选项

| 参数 | 环境变量回退 | 默认值 | 说明 |
|---|---|---|---|
| `--ip IP` | `TRPC_AGENT_HOST` | `0.0.0.0` | 监听网卡地址 |
| `--port PORT` | `TRPC_AGENT_PORT` | `8080` | 监听端口 |
| `--app_name NAME` | `TRPC_AGENT_APP_NAME` | `trpc_agent_server` | 应用名称（出现在日志中） |

### Agent 配置

| 参数 | 环境变量回退 | 默认值 | 说明 |
|---|---|---|---|
| `--agent_module MODULE` | `TRPC_AGENT_MODULE` | 无 | 点分 Python 模块路径，需导出 `root_agent`（实例）或 `create_agent()`（工厂函数）；不设置则自动创建默认 Assistant Agent |
| `--instruction TEXT` | `TRPC_AGENT_INSTRUCTION` | 无 | 覆盖默认 Agent 的系统指令；`--agent_module` 存在时忽略 |

---

## API 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/health` | 存活检测 |
| `POST` | `/v1/chat` | 同步聊天，等待完整回复后返回 |
| `POST` | `/v1/chat/stream` | SSE 流式聊天，实时推送文本块 |

### 请求体（`/v1/chat` 与 `/v1/chat/stream` 共用）

```json
{
  "message": "你好",
  "session_id": "可选，不填则自动生成 UUID",
  "user_id": "user_001"
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `message` | string | ✅ | 用户发送的消息 |
| `session_id` | string | ❌ | 会话 ID，传入则延续历史上下文 |
| `user_id` | string | ❌ | 用户标识，默认 `"default"` |

---

## 测试

### 1. 健康检查

```bash
curl http://127.0.0.1:8080/health
```

**返回结果**：

```json
{"status":"ok","app_name":"trpc_agent_server","version":"1.0.0"}
```

---

### 2. 同步聊天

```bash
curl -X POST http://127.0.0.1:8080/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "你好，介绍一下你自己", "user_id": "user_001"}'
```

**返回结果**：

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

---

### 3. SSE 流式聊天（原始输出）

```bash
curl -X POST http://127.0.0.1:8080/v1/chat/stream \
  -H "Content-Type: application/json" \
  -N \
  -d '{"message": "请用中文写一首关于春天的短诗", "user_id": "user_001"}'
```

**原始 SSE 输出**：

```
data: {"type":"text_delta","data":"春风","session_id":"f490d6f3-..."}

data: {"type":"text_delta","data":"轻抚","session_id":"f490d6f3-..."}

data: {"type":"text_delta","data":"万物生，","session_id":"f490d6f3-..."}

data: {"type":"text_delta","data":"\n细雨","session_id":"f490d6f3-..."}

data: {"type":"done","data":null,"session_id":"f490d6f3-..."}
```

---

### 4. 工具调用（启动天气 Agent）

先以天气 Agent 启动服务：

```bash
# 在 examples/fastapi_server 目录下执行
python3 run_server.py --agent_module agent.agent --port 8080
```

查询天气（可触发工具调用）：

```bash
curl -X POST http://127.0.0.1:8080/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "北京现在天气怎么样？", "user_id": "user_001"}'
```

**返回结果**：

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

---

## Python 交互式客户端

### 启动

```bash
# 默认：流式模式，连接 127.0.0.1:8080
python3 test/client.py --url http://127.0.0.1:8080 --user alice

# 同步模式（等全部回复后再显示）
python3 test/client.py --url http://127.0.0.1:8080 --user alice --sync
```

### 客户端参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--url URL` | `http://127.0.0.1:8080` | 服务器地址 |
| `--user USER` | `user_001` | 用户 ID |
| `--sync` | 流式 | 切换为同步模式 |

### 内置命令

| 命令 | 说明 |
|---|---|
| `/new` | 开启新会话，清除上下文 |
| `/sync` | 在流式 ↔ 同步模式间切换 |
| `/help` | 显示帮助 |
| `/quit` | 退出 |

### 测试结果

```
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

> **说明**：工具调用结果需使用天气 Agent（`--agent_module trpc_agent.server.fastapi.agent.agent`）启动服务才能触发；默认 Assistant Agent 不含工具。

---

## SSE 事件格式参考

流式端点 `/v1/chat/stream` 的每条事件为 `data: <JSON>\n\n`：

| `type` | `data` 内容 | 说明 |
|---|---|---|
| `text_delta` | 字符串 | Agent 回复的增量文本 |
| `tool_call` | `{"name": "...", "args": {...}}` | Agent 调用工具 |
| `tool_result` | `{"name": "...", "response": ...}` | 工具返回结果 |
| `done` | `null` | 流结束，正常退出 |
| `error` | 错误信息字符串 | 流中发生异常 |

---

## 加载自定义 Agent

自定义模块只需满足以下任意一种导出方式：

```python
# 方式一：导出 root_agent 实例
root_agent = LlmAgent(name="my-agent", ...)

# 方式二：导出工厂函数
def create_agent() -> LlmAgent:
    return LlmAgent(name="my-agent", ...)
```

启动时指定模块路径：

```bash
# 在 examples/fastapi_server 目录下执行, 这里的  my_package.agents 可以是 agent.agent，因为在这里的agent文件包含 root_agent，最终的文件包含 root_agent 即可 
python3 run_server.py --agent_module my_package.agents --port 8080

# 以内置天气 Agent 为例
python3 run_server.py --agent_module agent.agent --port 8080
```
