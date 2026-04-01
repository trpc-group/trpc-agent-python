# LangChain 工具集成示例

本示例展示如何将 LangChain 的 Tavily 搜索工具封装为 `FunctionTool`，集成到 trpc-agent 中使用。

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

2. 安装额外依赖

```bash
pip3 install langchain-tavily
```

3. 运行此代码示例

在 `.env` 文件中设置使用 LLM 相关的变量（也可以通过export设置）:
- TRPC_AGENT_API_KEY
- TRPC_AGENT_BASE_URL
- TRPC_AGENT_MODEL_NAME
- TAVILY_API_KEY（Tavily 搜索的 API Key，可在 https://tavily.com 获取）

然后运行下面的命令：

```bash
cd examples/tool_with_langchain/
python3 run_agent.py
```
