# Claude Agent 旅游规划助手示例

本示例演示如何使用 ClaudeAgent 构建一个旅游规划助手，根据用户需求综合考虑交通、住宿、饮食、景点等因素，给出合理的旅游规划。

## 关键特性

- **Claude-Code 内置工具**：使用 TodoWrite 内置工具进行任务管理
- **MCP 搜索工具**：集成 DuckDuckGo MCP Server，支持实时搜索机票、酒店、景点等信息
- **自定义工具**：提供日期获取工具，自动根据当前日期推荐旅游方案


## 环境要求
Python版本: 3.10+（强烈建议使用3.12）

## 在trpc-agent-python框架代码下如何运行此代码示例

1. 下载trpc-agent-python代码并安装

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e ".[agent-claude]"
```

2. 安装 Claude Code CLI

```bash
npm install -g @anthropic-ai/claude-code
```

3. 安装 DuckDuckGo MCP Server

```bash
# (可选)安装uv
curl -LsSf https://astral.sh/uv/install.sh | sh
# 安装mcp
uv pip install duckduckgo-mcp-server
```

4. 运行此代码示例

在 `.env` 文件中设置使用 LLM 相关的变量（也可以通过export设置）:
- TRPC_AGENT_API_KEY
- TRPC_AGENT_BASE_URL
- TRPC_AGENT_MODEL_NAME

然后运行下面的命令：

```bash
cd examples/claude_agent_with_travel_planner/
python3 run_agent.py
```

## 结果输出暂存

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