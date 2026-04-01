# MCP Tools 示例

本示例展示了如何在 trpc_agent_sdk 中使用 MCP（Model Context Protocol）协议集成外部工具服务器。

## 功能说明

- 通过 `MCPToolset` 连接外部 MCP 服务器，自动发现并使用服务器提供的工具
- 支持三种连接方式：`stdio`、`sse`、`streamable-http`
- 包含一个示例 MCP 服务器，提供天气查询和数学计算工具

## 环境要求

Python版本: 3.10+（强烈建议使用3.12）

## 在trpc-agent-python框架代码下如何运行此代码示例

1. 下载trpc-agent-python代码并安装

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

2. 运行此代码示例

在 `.env` 文件中设置使用 LLM 相关的变量（也可以通过export设置）:
- TRPC_AGENT_API_KEY
- TRPC_AGENT_BASE_URL
- TRPC_AGENT_MODEL_NAME

然后运行下面的命令：

```bash
cd examples/tool_with_mcp/
python3 run_agent.py
```

## 项目结构

```
tool_with_mcp/
├── .env                # 环境变量配置
├── README.md           # 本文件
├── run_agent.py        # 入口脚本
├── mcp_server.py       # MCP 服务器（提供天气和计算工具）
└── agent/
    ├── __init__.py
    ├── agent.py        # Agent 定义
    ├── config.py       # 模型配置
    ├── prompts.py      # 提示词
    └── tools.py        # MCPToolset 定义（stdio/sse/streamable-http）
```

## 连接方式说明

### 1. stdio 模式（默认）

通过标准输入输出与 MCP 服务器通信，`StdioMCPToolset` 会自动启动 `mcp_server.py` 作为子进程，无需手动启动服务器。

默认配置即可直接运行：

```bash
python3 run_agent.py
```

### 2. SSE 模式

通过 HTTP Server-Sent Events 通信。切换步骤：

**Step 1** — 修改 `mcp_server.py`，启用 SSE transport：

```python
# app.run(transport="stdio")
app.run(transport="sse")
# app.run(transport="streamable-http")
```

**Step 2** — 修改 `agent/agent.py`，将 import 和 toolset 替换为 `SseMCPToolset`：

```python
from .tools import SseMCPToolset

def create_agent() -> LlmAgent:
    mcp_toolset = SseMCPToolset()
    ...
```

**Step 3** — 先启动 MCP 服务器，再运行 Agent：

```bash
# 终端 1：启动 MCP 服务器
python3 mcp_server.py

# 终端 2：运行 Agent
python3 run_agent.py
```

### 3. Streamable-HTTP 模式

基于 HTTP 的双向流式通信。切换步骤：

**Step 1** — 修改 `mcp_server.py`，启用 streamable-http transport：

```python
# app.run(transport="stdio")
# app.run(transport="sse")
app.run(transport="streamable-http")
```

**Step 2** — 修改 `agent/agent.py`，将 import 和 toolset 替换为 `StreamableHttpMCPToolset`：

```python
from .tools import StreamableHttpMCPToolset

def create_agent() -> LlmAgent:
    mcp_toolset = StreamableHttpMCPToolset()
    ...
```

**Step 3** — 先启动 MCP 服务器，再运行 Agent：

```bash
# 终端 1：启动 MCP 服务器
python3 mcp_server.py

# 终端 2：运行 Agent
python3 run_agent.py
```

## FAQ

### 出现 `Attempted to exit a cancel scope that isn't the current tasks's current cancel scope`

这种错误是因为 mcp 官方库使用 AnyIO 库，当进入和退出发生在不同的任务上下文中会报此错误。请确保在使用完 runner 后调用 `await runner.close()`：

```python
async def main():
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)
    async for event in runner.run_async(...):
        ...
    await runner.close()
```

如果仍然出错，请在程序入口执行：

```python
from trpc_agent_sdk.tools import patch_mcp_cancel_scope_exit_issue

patch_mcp_cancel_scope_exit_issue()
```
