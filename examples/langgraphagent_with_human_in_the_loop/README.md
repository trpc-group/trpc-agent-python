# LangGraph Agent Human-in-the-Loop 示例

## 概述

本示例演示了如何基于 trpc-agent 框架，使用 **LangGraph** 的 `interrupt()` 机制实现 Human-in-the-Loop（人机协同）模式。通过 `StateGraph` 构建有向图工作流，当 Agent 执行数据库操作等高风险任务时，在工具调用完成后自动进入人工审批节点，审批通过则执行操作，拒绝则取消操作。

与 `LlmAgent` 示例不同，本示例采用 LangGraph 的图编排能力，以显式的节点和边定义执行流程，并通过 `interrupt` + `Command` 实现条件分支路由。

## 核心内容

### 架构设计

```
START → chatbot（LLM 决策）
            │
       tools_condition（是否调用工具？）
            │
            ├── 否 → END
            │
            └── 是 → tools（执行工具）→ human_approval（interrupt 暂停，等待人工审批）
                                                │
                                          Command 路由
                                           ├── approved_path → END
                                           └── rejected_path → END
```

### 关键组件

| 组件 | 说明 |
|------|------|
| `StateGraph` + `State` | LangGraph 有向图，`State` 定义了 `messages`、`task_description`、`approval_status` 三个状态字段 |
| `chatbot` 节点 | 使用 `@langgraph_llm_node` 装饰，LLM 绑定工具后进行意图识别和工具调用决策 |
| `tools` 节点 | `ToolNode` 预构建节点，执行 LLM 选择的工具 |
| `human_approval` 节点 | 核心节点，调用 `interrupt()` 暂停图执行，将审批信息推送给人工；收到决策后通过 `Command` 路由到对应分支 |
| `execute_database_operation` | 使用 `@tool` + `@langgraph_tool_node` 双装饰器定义的数据库操作工具 |
| `InMemorySaver` | Checkpointer，保存图执行状态以支持 `interrupt` 后恢复 |
| `LangGraphAgent` | trpc-agent 框架的 LangGraph 适配器，将 LangGraph 图封装为统一的 Agent 接口 |

### 执行流程

1. **用户发起请求** — 描述需要执行的数据库操作
2. **chatbot 节点** — LLM 解析意图，决定调用 `execute_database_operation` 工具
3. **tools 节点** — 执行工具调用，返回操作结果
4. **human_approval 节点** — 调用 `interrupt()` 暂停执行，将操作详情推送给审批者，触发 `LongRunningEvent`
5. **人工审批** — 审批者返回 `approved` 或 `rejected` 决策
6. **Command 路由** — 根据决策跳转到 `approved_path`（执行）或 `rejected_path`（取消）

### 项目结构

```
langgraphagent_with_human_in_the_loop/
├── run_agent.py          # 入口文件，Runner 驱动执行和审批恢复
├── .env                  # LLM 配置（API Key、Base URL、Model Name）
└── agent/
    ├── __init__.py
    ├── agent.py          # StateGraph 图定义、节点构建、LangGraphAgent 创建
    ├── tools.py          # 数据库操作工具（@tool + @langgraph_tool_node）
    ├── prompts.py        # Agent 指令提示词
    └── config.py         # 模型配置读取
```

## 环境要求
Python版本: 3.10+（强烈建议使用3.12）

## 在trpc-agent-python框架代码下如何运行此代码示例

1. 下载trpc-agent-python代码并安装

```bash
git clone https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent
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
cd examples/langgraphagent_with_human_in_the_loop/
python3 run_agent.py
```
