# Claude Agent Code Writer 示例

本示例演示如何使用 ClaudeAgent 构建一个代码生成助手，根据用户描述自动生成 Python 代码并写入文件。

## 关键特性

- **Claude-Code 内置工具**：使用 Read、Write、Edit、Glob、Grep、TodoWrite 等内置工具，无需自定义工具即可完成文件读写与代码检索
- **模型配置外部化**：通过环境变量配置模型，支持任意 OpenAI 兼容 API
- **Proxy Server 架构**：自动启动 Anthropic Proxy 子进程，将 Claude-Code 的模型请求转发到用户配置的模型服务
- **自主纠错能力**：Agent 遇到写入失败等错误时会自动调整策略重试

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

3. 运行此代码示例

在 `.env` 文件中设置使用 LLM 相关的变量（也可以通过export设置）:
- TRPC_AGENT_API_KEY
- TRPC_AGENT_BASE_URL
- TRPC_AGENT_MODEL_NAME

然后运行下面的命令：

```bash
cd examples/claude_agent_with_code_writer/
python3 run_agent.py
```
