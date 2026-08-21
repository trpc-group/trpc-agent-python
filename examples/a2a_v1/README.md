# A2A Agent 示例（a2a-sdk 1.x）

本示例演示如何通过 **a2a-sdk 1.x** 运行 A2A 服务，并用远程客户端发起多轮对话。
0.3 示例见独立目录 [examples/a2a](../a2a/README.md)。

## 功能说明

- 使用 `create_a2a_application` 装配 A2A HTTP 服务（`trpc_agent_sdk.server.a2a_v1`）
- 使用 `TrpcRemoteA2aAgent` 作为远程客户端
- 演示三轮会话上下文保持
- 演示工具调用（`get_weather_report`）

## 环境要求

- Python 3.12
- 已安装项目依赖

## 运行步骤

### 1. 安装依赖

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
uv pip install -e '.[a2a-v1]'
pip3 install python-dotenv
```

`[a2a]` 与 `[a2a-v1]` 不能同时安装。

### 2. 配置环境变量

在 [examples/a2a_v1/.env](./.env) 中设置（也可通过 export）：

```bash
TRPC_AGENT_API_KEY=...
TRPC_AGENT_BASE_URL=...
TRPC_AGENT_MODEL_NAME=...
```

### 3. 启动服务端

```bash
cd examples/a2a_v1
python3 run_server.py
```

服务地址：

- API：`http://127.0.0.1:18081`
- Agent Card：`http://127.0.0.1:18081/.well-known/agent-card.json`

兼容旧 0.3 客户端时：

```bash
A2A_V03_COMPAT=1 python3 run_server.py
```

### 4. 启动客户端

新开终端执行：

```bash
cd examples/a2a_v1
python3 test_a2a.py
```

对端是 0.3 服务且卡片不可用时：

```bash
A2A_FORCE_V03=1 python3 test_a2a.py
```

## 文件说明

| 文件 | 说明 |
|---|---|
| `run_server.py` | A2A 服务端入口（`create_a2a_application`） |
| `test_a2a.py` | A2A 客户端示例（3 轮对话） |
| `agent/agent.py` | Agent 定义（LlmAgent + 天气工具） |
| `agent/config.py` | 模型配置（从环境变量读取） |
| `agent/prompts.py` | Agent 提示词 |
| `agent/tools.py` | 天气查询工具（`get_weather_report`） |
| `.env` | 环境变量配置文件 |

## a2a 实现

参考：[trpc_agent_sdk/server/a2a_v1/README.md](../../trpc_agent_sdk/server/a2a_v1/README.md)
