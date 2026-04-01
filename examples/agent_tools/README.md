# AgentTool 使用示例

本示例展示了如何使用 `AgentTool` 将 Agent 包装成工具，实现 Agent 间的协作：

- **翻译 Agent** — 使用 `LlmAgent` 创建一个专业翻译 Agent
- **AgentTool 包装** — 用 `AgentTool(agent=...)` 将翻译 Agent 包装为工具
- **主 Agent 调用** — 主 Agent 通过工具调用的方式委托翻译任务

示例中包含以下 Agent：

| Agent | 角色 | 说明 |
|-------|------|------|
| `content_processor` | 主 Agent | 内容处理助手，根据用户需求决定是否调用翻译工具 |
| `translator` | 子 Agent（工具） | 专业中英文翻译工具，通过 AgentTool 包装后供主 Agent 调用 |

## 环境要求

Python版本: 3.10+（强烈建议使用3.12）

## 在trpc-agent-python框架代码下如何运行此代码示例

1. 下载trpc-agent-python代码并安装

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent
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
cd examples/tool_with_agent_tool/
python3 run_agent.py
```
