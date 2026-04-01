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
git clone https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent
cd trpc-agent
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
