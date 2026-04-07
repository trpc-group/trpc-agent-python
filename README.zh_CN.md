[English](README.md) | 中文

# tRPC-Agent-Python

[![PyPI Version](https://img.shields.io/pypi/v/trpc-agent-py.svg)](https://pypi.org/project/trpc-agent-py/)
[![Python Versions](https://img.shields.io/pypi/pyversions/trpc-agent-py.svg)](https://pypi.org/project/trpc-agent-py/)
[![LICENSE](https://img.shields.io/badge/license-Apache--2.0-green.svg)](https://github.com/trpc-group/trpc-agent-python/blob/main/LICENSE)
[![Releases](https://img.shields.io/github/release/trpc-group/trpc-agent-python.svg?style=flat-square)](https://github.com/trpc-group/trpc-agent-python/releases)
[![Coverage](https://codecov.io/gh/trpc-group/trpc-agent-python/branch/main/graph/badge.svg)](https://app.codecov.io/gh/trpc-group/trpc-agent-python/tree/main)
[![Documentation](https://img.shields.io/badge/Docs-Website-blue.svg)](https://trpc-group.github.io/trpc-agent-python/)

**深度融合 Python AI 生态的生产级 Agent 开发框架**。  
tRPC-Agent-Python 提供从 Agent 构建、编排、工具接入、会话记忆，到服务化部署与可观测的完整能力，帮助你快速落地可运行、可扩展、可维护的智能体应用。

## 为什么选择 tRPC-Agent-Python？

- **多范式 Agent 编排**：预设编排支持 ChainAgent / ParallelAgent / CycleAgent / TransferAgent，同时支持 GraphAgent 图编排
- **图编排能力（GraphAgent）**：通过 DSL 统一编排 Agent / Tool / MCP / Knowledge / CodeExecutor
- **高效接入 Python AI 生态扩展**：Agent 生态扩展（claude-agent-sdk / LangGraph 等）/ 工具生态扩展（mcp 等）/ 知识库生态扩展（LangChain 等）/ 模型生态扩展（LiteLLM 等）/ 记忆生态扩展（Mem0 等）
- **Agent 生态扩展**：支持 LangGraphAgent / ClaudeAgent / TeamAgent（Agno-Like）
- **Tool 生态扩展**：FunctionTool / 文件工具 / MCPToolset / LangChain Tool / Agent-as-Tool
- **完善的记忆能力（Session / Memory）**：Session 负责单会话内的消息与状态管理，Memory 负责跨会话长期记忆与个性化信息沉淀。持久化支持 InMemory / Redis / SQL，Memory 还支持 Mem0
- **生产级知识库能力**：知识库能力基于 LangChain 组件构建，支持 RAG 场景
- **CodeExecutor 扩展能力**：支持本地 / 容器执行器，用于支持 Agent 的代码执行与任务落地能力
- **Skills 扩展能力**：支持 SKILL.md 技能体系，用于支持 Agent 的技能复用与动态工具化能力
- **对接多种 LLM Provider**：OpenAI-like / Anthropic / LiteLLM 路由
- **服务化与可观测**：支持通过 FastAPI 提供 HTTP / A2A / AG-UI 的服务，内置 OpenTelemetry 追踪
- **trpc-claw（OpenClaw-like Agent）**：基于 [nanobot](https://github.com/HKUDS/nanobot) 构建，tRPC-Agent 提供 trpc-claw 能力，方便快速开发一个支持 Telegram / 企业微信等通道的 OpenClaw-like 个人 AI Agent

## 使用场景

- 智能客服与知识问答（RAG + 会话记忆）
- 代码生成与工程自动化（ClaudeAgent）
- 代码执行与自动化任务落地（CodeExecutor）
- 使用 Agent Skills
- 多角色协作任务（TeamAgent / Multi-Agent）
- 跨协议 Agent 服务接入（A2A / AG-UI）
- MCP 工具协议接入与工具生态扩展
- 面向网关场景的统一接入与协议转换
- 基于 GraphAgent 的组件化工作流编排
- 复用已有 LangGraph 工作流并接入当前体系
- 快速打造 OpenClaw-like 个人 AI Agent（trpc-claw）

## 目录

- [tRPC-Agent-Python](#trpc-agent-python)
  - [为什么选择 tRPC-Agent-Python？](#为什么选择-trpc-agent-python)
  - [使用场景](#使用场景)
  - [目录](#目录)
  - [快速开始](#快速开始)
  - [trpc-claw 用法](#trpc-claw-用法)
  - [文档](#文档)
  - [示例](#示例)
    - [1. 入门与基础 Agent](#1-入门与基础-agent)
    - [2. 多 Agent 预设编排](#2-多-agent-预设编排)
    - [3. Team 协作](#3-team-协作)
    - [4. 图编排能力（GraphAgent / DSL）](#4-图编排能力graphagent--dsl)
    - [5. Agent 生态扩展（LangGraphAgent / ClaudeAgent）](#5-agent-生态扩展langgraphagent--claudeagent)
    - [6. Tool 与 MCP](#6-tool-与-mcp)
    - [7. Skills](#7-skills)
    - [8. CodeExecutor](#8-codeexecutor)
    - [9. Session / Memory / Knowledge](#9-session--memory--knowledge)
    - [10. 服务化与协议](#10-服务化与协议)
    - [11. 过滤器与执行控制](#11-过滤器与执行控制)
    - [12. LlmAgent 进阶能力](#12-llmagent-进阶能力)
    - [13. LlmAgent 工具调用与交互](#13-llmagent-工具调用与交互)
  - [架构概览](#架构概览)
  - [贡献](#贡献)
  - [致谢](#致谢)

## 快速开始

### 前置条件

- Python 3.10+（推荐 Python 3.12）
- 可用的模型服务 API Key（OpenAI-like / Anthropic，或通过 LiteLLM 路由）

### 安装

```bash
pip install trpc-agent-py
```

按需安装扩展能力：

```bash
pip3 install -e '.[a2a,ag-ui,knowledge,agent-claude,mem0,langfuse]'
```


### 开发天气查询Agent

```python
import asyncio
import os
import uuid

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import Content, Part


async def get_weather_report(city: str) -> dict:
    return {"city": city, "temperature": "25°C", "condition": "Sunny", "humidity": "60%"}


async def main():
    model = OpenAIModel(
        model_name=os.environ["TRPC_AGENT_MODEL_NAME"],
        api_key=os.environ["TRPC_AGENT_API_KEY"],
        base_url=os.environ.get("TRPC_AGENT_BASE_URL", ""),
    )

    agent = LlmAgent(
        name="assistant",
        description="A helpful assistant",
        model=model,
        instruction="You are a helpful assistant.",
        tools=[FunctionTool(get_weather_report)],
    )

    session_service = InMemorySessionService()
    runner = Runner(app_name="demo_app", agent=agent, session_service=session_service)

    user_id = "demo_user"
    session_id = str(uuid.uuid4())
    user_content = Content(parts=[Part.from_text(text="What's the weather in Beijing?")])

    async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=user_content):
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            if part.text and event.partial:
                print(part.text, end="", flush=True)
            elif part.function_call:
                print(f"\n🔧 [{part.function_call.name}({part.function_call.args})]", flush=True)
            elif part.function_response:
                print(f"📊 [{part.function_response.response}]", flush=True)

    print()

if __name__ == "__main__":
    asyncio.run(main())
```

### 运行Agent

```bash
export TRPC_AGENT_API_KEY=xxx
export TRPC_AGENT_BASE_URL=xxxx
export TRPC_AGENT_MODEL_NAME=xxxx
python quickstart.py
```

## trpc-claw 用法

tRPC-Agent 基于 [nanobot](https://github.com/HKUDS/nanobot) 提供了 trpc-claw 能力（`trpc_agent_cmd openclaw`），方便你快速打造一个 OpenClaw-like 的个人 AI Agent：配置好后一条命令启动，可 7×24 小时在线，通过 Telegram、企业微信等常用 IM 与 Agent 交互，或直接在本地 CLI / UI 使用。

详细配置与高级功能参见：[openclaw.md](./docs/mkdocs/zh/openclaw.md)

### 快速上手

**1. 生成配置文件**

```bash
mkdir -p ~/.trpc_claw
trpc_agent_cmd openclaw conf_temp > ~/.trpc_claw/config.yaml
```

**2. 配置环境变量**

```bash
export TRPC_AGENT_API_KEY=your_api_key
export TRPC_AGENT_BASE_URL=your_base_url
export TRPC_AGENT_MODEL_NAME=your_model
```

**3. 本地运行**

```bash
# 强制本地 CLI
trpc_agent_cmd openclaw chat -c ~/.trpc_claw/config.yaml

# 本地 UI
trpc_agent_cmd openclaw ui -c ~/.trpc_claw/config.yaml
```

**4. 接入企业微信 / Telegram**

在 `config.yaml` 中启用对应通道，然后 `run` 启动：

```yaml
channels:
  wecom:
    enabled: true
    bot_id: ${WECOM_BOT_ID}
    secret: ${WECOM_BOT_SECRET}
  # 或 Telegram：
  # telegram:
  #   enabled: true
  #   token: ${TELEGRAM_BOT_TOKEN}
```

```bash
trpc_agent_cmd openclaw run -c ~/.trpc_claw/config.yaml
```

`run` 模式下若无可用通道会自动回退到本地 CLI，方便调试。

## 更多文档

- 文档目录：[`docs/mkdocs/zh`](./docs/mkdocs/zh)

## 示例

`examples` 目录里的示例都可以直接运行。下面按能力分组整理了“建议先看”的示例，并给了简短阅读提示，方便你按场景快速定位。

### 1. 入门与基础 Agent

建议先看：

- [examples/quickstart](./examples/quickstart/README.md) - 最小可运行示例
- [examples/llmagent](./examples/llmagent/README.md) - 基础 LlmAgent
- [examples/litellm](./examples/litellm/README.md) - LiteLLMModel 统一模型后端
- [examples/llmagent_with_custom_prompt](./examples/llmagent_with_custom_prompt/README.md) - 自定义提示词
- [examples/llmagent_with_schema](./examples/llmagent_with_schema/README.md) - 结构化输出

相关文档：[llm_agent.md](./docs/mkdocs/zh/llm_agent.md) / [model.md](./docs/mkdocs/zh/model.md)

这组示例可以帮你：

- 快速跑通从用户输入到工具调用再到模型输出的完整链路
- 理解流式输出中 function_call / function_response 事件的处理方式
- 掌握自定义 Prompt 和结构化输出的基础写法

可以先看这段代码（Runner + 流式事件）：

```python
runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)
async for event in runner.run_async(user_id=user_id, session_id=current_session_id, new_message=user_content):
    if event.partial and event.content:
        ...
```

### 2. 多 Agent 预设编排

建议先看：

- [examples/multi_agent_chain](./examples/multi_agent_chain/README.md) - ChainAgent
- [examples/multi_agent_parallel](./examples/multi_agent_parallel/README.md) - ParallelAgent
- [examples/multi_agent_cycle](./examples/multi_agent_cycle/README.md) - CycleAgent
- [examples/transfer_agent](./examples/transfer_agent/README.md) - TransferAgent 交接执行
- [examples/multi_agent_subagent](./examples/multi_agent_subagent/README.md) - Sub-agent 委派
- [examples/multi_agent_compose](./examples/multi_agent_compose/README.md) - 组合式编排
- [examples/multi_agent_start_from_last](./examples/multi_agent_start_from_last/README.md) - 从上轮 Agent 继续执行

相关文档：[multi_agents.md](./docs/mkdocs/zh/multi_agents.md)

这组示例可以帮你：

- 看懂 Chain / Parallel / Cycle / Transfer 的职责差异
- 按任务形态选择串行、并行、循环或交接的编排方式
- 学会在已有结果基础上续跑与组合子流程

可以先看这段代码（ChainAgent）：

```python
pipeline = ChainAgent(
    name="document_processor",
    sub_agents=[extractor_agent, translator_agent],
)
```

### 3. Team 协作

建议先看：

- [examples/team](./examples/team/README.md) - Team 协调模式
- [examples/team_parallel_execution](./examples/team_parallel_execution/README.md) - Team 并行执行
- [examples/team_with_skill](./examples/team_with_skill/README.md) - Team + Skills
- [examples/team_human_in_the_loop](./examples/team_human_in_the_loop/README.md) - Team 人机协同
- [examples/team_as_sub_agent](./examples/team_as_sub_agent/README.md) - Team 作为子 Agent
- [examples/team_member_message_filter](./examples/team_member_message_filter/README.md) - Team 成员消息过滤
- [examples/team_member_agent_claude](./examples/team_member_agent_claude/README.md) - Team 成员接入 ClaudeAgent
- [examples/team_member_agent_langgraph](./examples/team_member_agent_langgraph/README.md) - Team 成员接入 LangGraphAgent
- [examples/team_member_agent_team](./examples/team_member_agent_team/README.md) - Team 成员嵌套 Team
- [examples/team_with_cancel](./examples/team_with_cancel/README.md) - Team 任务取消

相关文档：[team.md](./docs/mkdocs/zh/team.md) / [human_in_the_loop.md](./docs/mkdocs/zh/human_in_the_loop.md) / [cancel.md](./docs/mkdocs/zh/cancel.md)

这组示例可以帮你：

- 理解 Team 的 Leader / Member 协作模式
- 学会在 Team 中接入 Skills、子 Team、外部 Agent
- 覆盖消息过滤、人机协同、取消控制等常见工程需求

可以先看这段代码（TeamAgent）：

```python
content_team = TeamAgent(
    name="content_team",
    model=model,
    members=[researcher, writer],
    instruction=LEADER_INSTRUCTION,
    share_member_interactions=True,
)
```

### 4. 图编排能力（GraphAgent / DSL）

建议先看：

- [examples/graph](./examples/graph/README.md) - GraphAgent：编排 function / llm / agent / code / mcp / knowledge 节点
- [examples/graph_multi_turns](./examples/graph_multi_turns/README.md) - 多轮图执行
- [examples/graph_with_interrupt](./examples/graph_with_interrupt/README.md) - 图执行中断
- [examples/dsl](./examples/dsl/README.md) - DSL 能力示例
- [examples/dsl/classifier_mcp](./examples/dsl/classifier_mcp/README.md) - DSL + MCP 分类路由

相关文档：[graph.md](./docs/mkdocs/zh/graph.md) / [dsl.md](./docs/mkdocs/zh/dsl.md)

这组示例可以帮你：

- 适合需要显式流程控制的任务（分支、合并、中断、续跑）
- 支持在同一图中混合 Agent / Tool / MCP / CodeExecutor / Knowledge
- 用 DSL 更快搭建可读、可维护的工作流

可以先看这段代码（条件路由）：

```python
graph.add_conditional_edges(
    "decide",
    create_route_choice(set(path_map.keys())),
    path_map,
)
```

### 5. Agent 生态扩展（LangGraphAgent / ClaudeAgent）

建议先看：

- [examples/langgraph_agent](./examples/langgraph_agent/README.md) - 对接用户使用 LangGraph 开发并 compile 的 Agent 工作流
- [examples/langgraph_agent_with_cancel](./examples/langgraph_agent_with_cancel/README.md) - LangGraphAgent 任务取消
- [examples/langgraphagent_with_human_in_the_loop](./examples/langgraphagent_with_human_in_the_loop/README.md) - LangGraphAgent 人机协同
- [examples/claude_agent](./examples/claude_agent/README.md) - ClaudeAgent 基础用法
- [examples/claude_agent_with_streaming_tool](./examples/claude_agent_with_streaming_tool/README.md) - ClaudeAgent 流式工具调用
- [examples/claude_agent_with_skills](./examples/claude_agent_with_skills/README.md) - ClaudeAgent + Skills
- [examples/claude_agent_with_code_writer](./examples/claude_agent_with_code_writer/README.md) - ClaudeAgent 代码生成
- [examples/claude_agent_with_travel_planner](./examples/claude_agent_with_travel_planner/README.md) - ClaudeAgent 任务编排
- [examples/claude_agent_with_cancel](./examples/claude_agent_with_cancel/README.md) - ClaudeAgent 任务取消

相关文档：[langgraph_agent.md](./docs/mkdocs/zh/langgraph_agent.md) / [claude_agent.md](./docs/mkdocs/zh/claude_agent.md) / [human_in_the_loop.md](./docs/mkdocs/zh/human_in_the_loop.md) / [cancel.md](./docs/mkdocs/zh/cancel.md)

这组示例可以帮你：

- 用 LangGraphAgent 复用已有 LangGraph 实现并接入当前运行时
- 用 ClaudeAgent 处理代码生成、工程自动化、流式工具调用
- 覆盖人机协同与取消控制，便于落地真实业务流程

可以先看这段代码（ClaudeAgent）：

```python
root_agent = ClaudeAgent(
    name="claude_weather_agent",
    model=_create_model(),
    instruction=INSTRUCTION,
    tools=[FunctionTool(get_weather)],
    enable_session=True,
)
```

### 6. Tool 与 MCP

建议先看：

- [examples/function_tools](./examples/function_tools/README.md) - FunctionTool
- [examples/file_tools](./examples/file_tools/README.md) - 文件工具
- [examples/tools](./examples/tools/README.md) - Tool 基础组合
- [examples/toolsets](./examples/toolsets/README.md) - ToolSet 组合
- [examples/streaming_tools](./examples/streaming_tools/README.md) - 流式工具调用
- [examples/mcp_tools](./examples/mcp_tools/README.md) - MCPToolset（stdio / sse / streamable-http）
- [examples/langchain_tools](./examples/langchain_tools/README.md) - LangChain 工具接入
- [examples/agent_tools](./examples/agent_tools/README.md) - Agent 作为 Tool

相关文档：[tool.md](./docs/mkdocs/zh/tool.md)

这组示例可以帮你：

- 从函数工具到协议工具（MCP）再到组合工具，接入路径完整
- 覆盖流式工具调用与 Agent-as-Tool 等高级模式
- 便于把现有工具实现复用到多 Agent 场景

可以先看这段代码（MCPToolset）：

```python
class StdioMCPToolset(MCPToolset):
    def __init__(self):
        super().__init__()
        self._connection_params = StdioConnectionParams(
            server_params=McpStdioServerParameters(command="python3", args=["mcp_server.py"]),
            timeout=5,
        )
```

### 7. Skills

建议先看：

- [examples/skills](./examples/skills/README.md) - SkillToolSet 基础
- [examples/skills_with_container](./examples/skills_with_container/README.md) - 容器运行 Skill
- [examples/skills_with_dynamic_tools](./examples/skills_with_dynamic_tools/README.md) - 动态工具 Skill

相关文档：[skill.md](./docs/mkdocs/zh/skill.md)

这组示例可以帮你：

- 把可复用能力沉淀为 Skills，减少重复开发
- 支持按场景动态装配工具能力
- 便于沉淀可复用的业务技能模块

可以先看这段代码（SkillToolSet）：

```python
workspace_runtime = create_local_workspace_runtime()
repository = create_default_skill_repository(skill_paths, workspace_runtime=workspace_runtime)
skill_tool_set = SkillToolSet(repository=repository, run_tool_kwargs=tool_kwargs)
```

### 8. CodeExecutor

建议先看：

- [examples/code_executors](./examples/code_executors/README.md) - UnsafeLocalCodeExecutor / ContainerCodeExecutor

相关文档：[code_executor.md](./docs/mkdocs/zh/code_executor.md)

这组示例可以帮你：

- 按执行环境选择本地或容器执行器
- 让 Agent 在可控边界内完成代码执行与任务落地
- 与 Skills/Tool 组合形成“规划 + 执行”闭环

### 9. Session / Memory / Knowledge

建议先看：

- Session：[examples/session_service_with_in_memory](./examples/session_service_with_in_memory/README.md) / [examples/session_service_with_redis](./examples/session_service_with_redis/README.md) / [examples/session_service_with_sql](./examples/session_service_with_sql/README.md) / [examples/session_summarizer](./examples/session_summarizer/README.md) / [examples/session_state](./examples/session_state/README.md)
- Memory：[examples/memory_service_with_in_memory](./examples/memory_service_with_in_memory/README.md) / [examples/memory_service_with_redis](./examples/memory_service_with_redis/README.md) / [examples/memory_service_with_sql](./examples/memory_service_with_sql/README.md) / [examples/memory_service_with_mem0](./examples/memory_service_with_mem0/README.md) / [examples/mem_0](./examples/mem_0/README.md)
- Knowledge：[examples/knowledge_with_documentloader](./examples/knowledge_with_documentloader/README.md) / [examples/knowledge_with_vectorstore](./examples/knowledge_with_vectorstore/README.md) / [examples/knowledge_with_rag_agent](./examples/knowledge_with_rag_agent/README.md) / [examples/knowledge_with_searchtool_rag_agent](./examples/knowledge_with_searchtool_rag_agent/README.md) / [examples/knowledge_with_prompt_template](./examples/knowledge_with_prompt_template/README.md) / [examples/knowledge_with_custom_components](./examples/knowledge_with_custom_components/README.md)

相关文档：
- Session：[session.md](./docs/mkdocs/zh/session.md) / [session_redis.md](./docs/mkdocs/zh/session_redis.md) / [session_sql.md](./docs/mkdocs/zh/session_sql.md) / [session_summary.md](./docs/mkdocs/zh/session_summary.md)
- Memory：[memory.md](./docs/mkdocs/zh/memory.md)
- Knowledge：[knowledge.md](./docs/mkdocs/zh/knowledge.md) / [knowledge_document_loader.md](./docs/mkdocs/zh/knowledge_document_loader.md) / [knowledge_retrievers.md](./docs/mkdocs/zh/knowledge_retrievers.md) / [knowledge_vectorstore.md](./docs/mkdocs/zh/knowledge_vectorstore.md) / [knowledge_prompt_template.md](./docs/mkdocs/zh/knowledge_prompt_template.md) / [knowledge_custom_components.md](./docs/mkdocs/zh/knowledge_custom_components.md)

这组示例可以帮你：

- Session：管理单会话的消息、摘要与状态
- Memory：管理跨会话长期记忆（含 Mem0）
- Knowledge：覆盖文档加载、检索、RAG、提示模板等链路

### 10. 服务化与协议

建议先看：

- [examples/fastapi_server](./examples/fastapi_server/README.md) - HTTP 服务（同步 + SSE）
- [examples/a2a](./examples/a2a/README.md) / [examples/a2a_with_cancel](./examples/a2a_with_cancel/README.md) - A2A 服务与取消
- [examples/agui](./examples/agui/README.md) / [examples/agui_with_cancel](./examples/agui_with_cancel/README.md) - AG-UI 服务与取消

相关文档：[a2a.md](./docs/mkdocs/zh/a2a.md) / [agui.md](./docs/mkdocs/zh/agui.md) / [cancel.md](./docs/mkdocs/zh/cancel.md)

这组示例可以帮你：

- 演示 HTTP / A2A / AG-UI 三种服务暴露方式
- 覆盖流式返回与取消机制，便于接入网关和前端
- 可直接作为服务化落地的最小模板

### 11. 过滤器与执行控制

建议先看：

- [examples/filter_with_model](./examples/filter_with_model/README.md) - Model 级过滤
- [examples/filter_with_tool](./examples/filter_with_tool/README.md) - Tool 级过滤
- [examples/filter_with_agent](./examples/filter_with_agent/README.md) - Agent 级过滤
- [examples/llmagent_with_branch_filtering](./examples/llmagent_with_branch_filtering/README.md) - 分支过滤
- [examples/llmagent_with_timeline_filtering](./examples/llmagent_with_timeline_filtering/README.md) - 时间线过滤
- [examples/llmagent_with_cancel](./examples/llmagent_with_cancel/README.md) - 执行取消

相关文档：[filter.md](./docs/mkdocs/zh/filter.md) / [cancel.md](./docs/mkdocs/zh/cancel.md)

这组示例可以帮你：

- 展示 Model / Tool / Agent 三层过滤策略
- 覆盖分支过滤、时间线过滤、执行取消等控制能力
- 适合治理、审计、风控等强约束场景

### 12. LlmAgent 进阶能力

建议先看：

- [examples/llmagent_with_tool_prompt](./examples/llmagent_with_tool_prompt/README.md) - Tool 调用提示词增强
- [examples/llmagent_with_thinking](./examples/llmagent_with_thinking/README.md) - 思考模式
- [examples/llmagent_with_user_history](./examples/llmagent_with_user_history/README.md) - 用户历史消息管理
- [examples/llmagent_with_max_history_messages](./examples/llmagent_with_max_history_messages/README.md) - 历史消息窗口限制
- [examples/llmagent_with_model_create_fn](./examples/llmagent_with_model_create_fn/README.md) - 动态模型创建函数
- [examples/llmagent_with_custom_agent](./examples/llmagent_with_custom_agent/README.md) - 自定义 Agent 扩展

相关文档：[llm_agent.md](./docs/mkdocs/zh/llm_agent.md) / [model.md](./docs/mkdocs/zh/model.md) / [custom_agent.md](./docs/mkdocs/zh/custom_agent.md)

这组示例可以帮你：

- 聚焦 LlmAgent 在上下文、提示词、模型路由上的可扩展点
- 适合把通用 Agent 调整为贴近业务策略的 Agent
- 便于沉淀可复用的行为模板

### 13. LlmAgent 工具调用与交互

建议先看：

- [examples/llmagent_with_streaming_tool_simple](./examples/llmagent_with_streaming_tool_simple/README.md) - 简单流式工具调用
- [examples/llmagent_with_streaming_tool_complex](./examples/llmagent_with_streaming_tool_complex/README.md) - 复杂流式工具调用
- [examples/llmagent_with_parallal_tools](./examples/llmagent_with_parallal_tools/README.md) - 并行工具调用（目录名沿用 `parallal`）
- [examples/llmagent_with_human_in_the_loop](./examples/llmagent_with_human_in_the_loop/README.md) - 人机协同决策

相关文档：[llm_agent.md](./docs/mkdocs/zh/llm_agent.md) / [tool.md](./docs/mkdocs/zh/tool.md) / [human_in_the_loop.md](./docs/mkdocs/zh/human_in_the_loop.md)

这组示例可以帮你：

- 覆盖简单 / 复杂两类流式工具调用模式
- 演示并行工具调度与人机协同确认节点
- 可与过滤器、取消机制组合，形成更稳的执行链路

> 更多示例请查看 [examples](./examples) 目录下各子目录 README.md。

## 架构概览

![tRPC-Agent-Python Architecture](./docs/mkdocs/assets/imgs/architecture.png)

框架采用事件驱动方式组织组件，各层可独立扩展：

- **Agent 层**：LlmAgent / ChainAgent / ParallelAgent / CycleAgent / TransferAgent
- **Agent 生态扩展层**：LangGraphAgent / ClaudeAgent / TeamAgent
- **图能力层**：GraphAgent / trpc_agent_sdk.dsl.graph（DSL 组件编排能力）
- **Runner 层**：统一执行入口，负责 Session/Memory/Artifact 等服务协同
- **Tool 层**：FunctionTool / 文件工具 / MCPToolset / Skill 工具
- **Model 层**：OpenAIModel / AnthropicModel / LiteLLMModel
- **Memory 层**：SessionService / MemoryService / SessionSummarizer / Mem0MemoryService
- **Knowledge 层**：基于 LangChain 的生产级知识库能力（RAG）
- **执行与技能层**：CodeExecutor（本地/容器）/ Skills
- **服务层**：FastAPI / A2A / AG-UI
- **观测层**：OpenTelemetry tracing/metrics，可对接 Langfuse 等平台
- **生态适配层**：claude-agent-sdk / mcp / LangChain / LiteLLM / Mem0，通过模型/工具/记忆适配器接入主链路

关键包一览：

| Package | 主要职责 |
| --- | --- |
| trpc_agent_sdk.agents | Agent 抽象 / 多 Agent 编排 / 生态扩展（LangGraphAgent / ClaudeAgent / TeamAgent） |
| trpc_agent_sdk.runners | 统一执行与事件输出 |
| trpc_agent_sdk.models | 模型适配层 |
| trpc_agent_sdk.tools | 工具体系与 MCP 支持 |
| trpc_agent_sdk.sessions | 会话管理与总结压缩 |
| trpc_agent_sdk.memory | 长期记忆服务 |
| trpc_agent_sdk.dsl.graph | DSL 图编排引擎 |
| trpc_agent_sdk.teams | Team 协作模式 |
| trpc_agent_sdk.code_executors | 代码执行与工作区运行时 |
| trpc_agent_sdk.skills | Skill 仓库 / Skill 工具 |
| trpc_agent_sdk.server | FastAPI / A2A / AG-UI 等服务能力 |

## 贡献

我们热爱贡献！加入我们不断壮大的开发者社区，共同构建 AI Agent 的未来。

### **贡献方式**

- **报告 bug** 或通过 [Issues](https://github.com/trpc-group/trpc-agent-python/issues) 建议新功能
- **改进文档** - 帮助他人更快学习
- **提交 PR** - bug 修复、新功能或示例
- **分享您的用例** - 用您的 Agent 应用启发他人

### **快速贡献设置**

```bash
# Fork 并克隆仓库
git clone https://github.com/YOUR_USERNAME/trpc-agent-python.git
cd trpc-agent-python

# 安装开发依赖并运行测试
pip install -e ".[dev]"
pytest

# 进行您的更改并提交 PR！
```

**请阅读** [CONTRIBUTING.md](CONTRIBUTING.md) 了解详细指南和编码标准。  
**请遵循** [CODE-OF-CONDUCT.md](CODE-OF-CONDUCT.md) 共同维护友好、尊重、包容的社区。

## 致谢

### **企业验证**

感谢腾讯理财通、腾讯广告等业务团队在真实业务场景中的持续验证与反馈，帮助我们不断打磨框架能力。

### **开源灵感**

也感谢 **ADK**、**Agno**、**CrewAI**、**AutoGen** 等优秀开源框架带来的启发，让我们能够站在巨人的肩膀上持续前进。

---

如果这个项目对你有帮助，欢迎在 GitHub 上点个 Star，这是对我们最直接的鼓励，也能帮助更多开发者发现这个项目。
