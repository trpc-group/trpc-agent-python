# Claude Agent 旅游规划助手示例

本示例演示如何基于 `ClaudeAgent` 构建一个旅游规划助手，结合 Claude-Code 内置工具、MCP 搜索工具和自定义工具，根据用户需求综合考虑交通、住宿、饮食、景点等因素，给出合理的旅游规划。

## 关键特性

- **Claude-Code 内置工具**：通过 `ClaudeAgentOptions(allowed_tools=["TodoWrite"])` 启用 Claude-Code 原生的任务管理工具
- **MCP 搜索工具**：集成 DuckDuckGo MCP Server（`MCPToolset`），支持实时搜索机票、酒店、景点等信息
- **自定义工具**：通过 `FunctionTool(get_current_date)` 提供日期获取能力，自动根据当前日期推荐旅游方案
- **流式事件处理**：通过 `runner.run_async(...)` 消费事件流，区分打印工具调用、工具返回和文本分片
- **多轮交互对话**：支持 `while True` 循环持续对话，直到用户输入 `quit` 退出

## Agent 层级结构说明

本例是单 Agent 示例，不涉及多 Agent 分层路由：

```text
travel_planner (ClaudeAgent)
├── model: OpenAIModel
├── claude_agent_options:
│   └── allowed_tools: ["TodoWrite"]
├── tools:
│   ├── FunctionTool(get_current_date)
│   └── DuckDuckGoSearchMCP (MCPToolset → duckduckgo-mcp-server)
└── session: InMemorySessionService
```

关键文件：

- [examples/claude_agent_with_travel_planner/agent/agent.py](./agent/agent.py)：构建 `ClaudeAgent`、挂载工具、配置 Claude 环境
- [examples/claude_agent_with_travel_planner/agent/tools.py](./agent/tools.py)：DuckDuckGo MCP 搜索工具集 + 日期获取工具
- [examples/claude_agent_with_travel_planner/agent/prompts.py](./agent/prompts.py)：旅游规划提示词模板
- [examples/claude_agent_with_travel_planner/agent/config.py](./agent/config.py)：环境变量读取
- [examples/claude_agent_with_travel_planner/run_agent.py](./run_agent.py)：测试入口，启动交互式对话

## 关键代码解释

这一节用于快速定位"Claude 环境管理、工具集成、流式对话"三条核心链路。

### 1) Agent 组装与 Claude 环境配置（`agent/agent.py`）

- 使用 `ClaudeAgent` 组装旅游规划助手，挂载 `FunctionTool(get_current_date)` 与 `DuckDuckGoSearchMCP`
- 通过 `ClaudeAgentOptions(allowed_tools=["TodoWrite"])` 启用 Claude-Code 内置工具
- 通过 `setup_claude_env()` 启动 Anthropic Proxy Server 子进程，将请求转发至 OpenAI 兼容模型

### 2) MCP 工具集成与自定义工具（`agent/tools.py`）

- `DuckDuckGoSearchMCP` 继承 `MCPToolset`，通过 `StdioConnectionParams` 以 `uvx duckduckgo-mcp-server` 方式启动 MCP Server
- `get_current_date()` 返回当前日期字符串，供 Agent 在未指定日期时自动获取

### 3) 流式事件处理与交互式对话（`run_agent.py`）

- 通过 `setup_claude()` → `create_agent()` → `agent.initialize()` 完成环境与 Agent 初始化
- 使用 `runner.run_async(...)` 消费事件流
- `event.partial=True` 时逐字打印文本分片
- 完整事件中区分并打印：
  - `function_call`（工具调用）
  - `function_response`（工具返回）
- 退出时依次执行 `runner.close()` → `agent.destroy()` → `cleanup_claude()` 清理资源

## 环境与运行

### 环境要求

- Python 3.12

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e ".[agent-claude]"
```

安装 Claude Code CLI：

```bash
npm install -g @anthropic-ai/claude-code
```

安装 DuckDuckGo MCP Server：

```bash
# (可选)安装uv
curl -LsSf https://astral.sh/uv/install.sh | sh
# 安装mcp
uv pip install duckduckgo-mcp-server
```

### 环境变量要求

在 [examples/claude_agent_with_travel_planner/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/claude_agent_with_travel_planner
python3 run_agent.py
```

## 运行结果（实测）

```text
[2026-04-01 19:40:31][INFO][trpc_agent_sdk][trpc_agent_sdk/server/agents/claude/_setup.py:227][48891] Proxy server proxy process started (PID: 48948)
[2026-04-01 19:40:32][INFO][trpc_agent_sdk][trpc_agent_sdk/server/agents/claude/_setup.py:244][48891] Proxy server is ready at http://0.0.0.0:8082
[2026-04-01 19:40:32][INFO][trpc_agent_sdk][trpc_agent_sdk/server/agents/claude/_runtime.py:27][48891] ClaudeAgent event loop thread started
🆔 Session ID: 3d329114...
👤 User ID: Alice

💬 请输入您的旅游需求（输入 'quit' 或 'exit' 退出）: 
> beijing
📝 用户: beijing

🤖 Agent: ⠋ Resolving dependencies...                                                                                          
Installed 39 packages in 65ms
DuckDuckGo MCP Server initialized:
  SafeSearch: MODERATE (kp=-1)
  Default Region: none
[04/01/26 19:40:44] INFO     Processing request of type ListToolsRequest                                          server.py:720
                    INFO     Processing request of type ListToolsRequest                                          server.py:720

🔧 [Tool Call: mcp__travel_planner_tools__get_current_date({})]

🔧 [Tool Call: mcp__travel_planner_tools__search({"query": "Beijing travel guide 2026", "max_results": 5, "region": "cn-zh"})]
📊 [Tool Result: mcp__travel_planner_tools__get_current_date({"result": "2026-04-01"})]
[04/01/26 19:40:51] INFO     Processing request of type CallToolRequest                                           server.py:720
[04/01/26 19:40:52] INFO     HTTP Request: POST https://html.duckduckgo.com/html "HTTP/1.1 200 OK"              _client.py:1740
📊 [Tool Result: mcp__travel_planner_tools__search({"result": "Found 5 search results:\n\n1. Beijing Travel Guide 2026: Top Attractions, Best Time & Insider Tips\n   URL: https://bespokechinatravel.com/travel-guide/beijing/\n   Summary: Plan yourBeiji)]
今天是2026年4月1日，以下是关于北京旅游的一些建议和资源：

### 1. [北京旅游指南2026：顶级景点、最佳时间和内部贴士](https://bespokechinatravel.com/travel-guide/beijing/)
   - 提供全面的旅游指南，包括景点推荐、最佳旅行时间、行程安排、美食、交通和实用贴士。

### 2. [2026年北京旅行计划：7个步骤](https://www.chinahighlights.com/beijing/beijing-trip-planner.htm)
   - 帮助规划行程，包括停留时间、最佳季节、交通方式和预算。

### 3. [2026年北京旅游指南：历史、胡同与城市生活](https://www.thechinajourney.com/zh_cn/%E5%8C%97%E4%BA%AC%E6%97%85%E6%B8%B8%E6%8C%87%E5%8D%97/)
   - 推荐春季（3月至5月）和秋季（9月至11月）为最佳旅行季节，气候舒适，风景优美。

### 4. [中国旅游指南：北京](https://global.chinadaily.com.cn/a/202603/27/WS69c5dda4a310d6866eb402e5.html)
   - 介绍北京的传统与现代结合的魅力，从胡同到商业街。

### 5. [北京2026：何时去、住哪里、做什么](https://www.nationalgeographic.com/travel/best-of-the-world-2026/article/beijing-china)
   - 推荐2026年北京的新景点和必看之地，从故宫到隐藏的庭院。

如果您有具体的需求（如住宿、交通、景点推荐等），请告诉我，我可以为您进一步规划！
----------------------------------------
> quit
👋 再见！
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **Claude 环境正常启动**：Proxy Server 成功启动并监听 `http://0.0.0.0:8082`，ClaudeAgent 事件循环线程正常运行
- **工具调用正确**：Agent 自动调用 `get_current_date` 获取当前日期，并调用 `search` 搜索北京旅游信息
- **MCP 工具集成正常**：DuckDuckGo MCP Server 成功初始化，返回 5 条搜索结果
- **工具结果被正确消费**：回复内容基于搜索结果组织为可读的旅游建议，包含标题、链接和摘要

## 适用场景建议

- 快速验证 ClaudeAgent + MCP 工具 + FunctionTool 的集成链路：适合使用本示例
- 验证 Claude-Code 内置工具（如 TodoWrite）的启用方式：适合使用本示例
- 需要测试不依赖 Claude 的纯 LLM Agent 工具调用：建议使用 `examples/llmagent`
