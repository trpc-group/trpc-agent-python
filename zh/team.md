
# Team Agent

TeamAgent 是 trpc-agent 框架中用于实现多 Agent 协作的组件，它实现了类似 Agno 框架的 Coordinate 模式。TeamAgent 内部有一个团队领导（Leader），负责根据用户请求，拆解并委派给合适的成员 Agent，并追踪任务的完成情况，综合成员的响应生成最终答案。

与 [Multi Agents](./multi_agents.md) 中的确定性编排模式（Chain、Parallel、Cycle）不同，TeamAgent 使用 LeaderAgent 拆解子任务、委派任务、追踪任务完成情况、在任务处理失败时重新规划，更适合需要智能协调的复杂场景。

## 为什么需要 Team？
单个 Agent 往往只擅长一个角色。真实应用里，我们通常需要多个角色协作，例如：
- 研究背景信息
- 编写代码
- 审查和纠错

Team 的目标是用一个小而清晰的 API 把这些角色组合起来，且不引入难用的“多层抽象”。
这里的 API 指 Application Programming Interface（应用程序编程接口）。

## 设计概述

TeamAgent 的核心设计理念：

- **Leader-Member 架构**：内部 Leader Agent 使用 LLM 决定将任务委派给哪个成员
- **工具驱动的委派**：Leader 通过调用 `delegate_to_member` 工具来委派任务
- **消息隔离控制**：通过 `override_messages` 机制，TeamAgent 完全控制每个成员看到的消息上下文
- **状态持久化**：使用 `TeamRunContext` 存储在 `session.state` 中，支持多轮对话

### 执行流程

```
用户请求 → Leader 分析任务 → Leader 调用 delegate_to_member(member, task)
    → TeamAgent 拦截信号 → 执行目标 Member
    → 收集 Member 响应 → 更新 TeamRunContext
    → Leader 继续处理或综合最终响应 → 返回给用户
```

## 简单示例

下面是一个完整的内容创作团队示例，展示 TeamAgent 的用法，TeamAgent内置的Leader将会委派合适的任务给成员，并追踪任务完成的情况，如果完成则结束，团队成员（researcher和writer）之间共享Team的历史（share_member_interactions=True），因此writer能基于researcher的内容撰写。

```python
import asyncio
import os
import uuid

from trpc_agent.agents import LlmAgent
from trpc_agent.teams import TeamAgent
from trpc_agent.models import OpenAIModel
from trpc_agent.runners import Runner
from trpc_agent.sessions import InMemorySessionService
from trpc_agent.tools import FunctionTool
from trpc_agent.types import Content, Part


# 定义成员工具
async def search_web(query: str) -> str:
    """Search the web for information."""
    return f"Search results for '{query}': Found relevant information..."

async def check_grammar(text: str) -> str:
    """Check grammar of the text."""
    return f"Grammar check completed: Text quality is good."


def create_team():
    model = OpenAIModel(
        model_name="deepseek-chat",
        api_key=os.environ.get("TRPC_AGENT_API_KEY", ""),
        base_url="https://api.deepseek.com/v1",
    )

    # 研究员 - 负责信息搜索
    researcher = LlmAgent(
        name="researcher",
        model=model,
        description="Research expert",
        instruction="""You are a research expert. When receiving a topic:
1. Use the search_web tool to search for information
2. Provide comprehensive factual information
Keep your response concise.""",
        tools=[FunctionTool(search_web)],
    )

    # 写手 - 负责内容创作
    writer = LlmAgent(
        name="writer",
        model=model,
        description="Writing expert",
        instruction="""You are a professional writer. When receiving information:
1. Transform research into engaging content
2. Use the check_grammar tool to verify quality
Keep your response concise.""",
        tools=[FunctionTool(check_grammar)],
    )

    # 创建团队
    return TeamAgent(
        name="content_team",
        model=model,
        members=[researcher, writer],
        instruction="""You are the content team editor. Your role is:
1. First delegate tasks to the researcher to gather information
2. Then have the writer create content based on research
3. Synthesize the final response for the user""",
        share_member_interactions=True,  # 允许成员间共享交互信息
    )


async def main():
    APP_NAME = "content_team_demo"
    USER_ID = "demo_user"
    session_id = str(uuid.uuid4())

    team = create_team()
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=team, session_service=session_service)

    query = "Please write a short article about AI"
    user_message = Content(parts=[Part.from_text(text=query)])

    async for event in runner.run_async(
            user_id=USER_ID,
            session_id=session_id,
            new_message=user_message,
    ):
        if event.content and event.content.parts and event.partial:
            for part in event.content.parts:
                if part.text:
                    print(part.text, end="", flush=True)

    await runner.close()


if __name__ == "__main__":
    asyncio.run(main())
```

**完整示例：**
- [examples/team/run_agent.py](../../examples/team/run_agent.py) - 基础团队协作示例

## Leader 配置 Skills

TeamAgent 支持为 Leader 配置 Agent Skills，用于扩展Leader的能力（如执行技能脚本、生成中间资料、读取技能文档），使用方式如下例所示：

```python
from trpc_agent.skills import create_default_skill_repository
from trpc_agent.code_executors import create_local_workspace_runtime


workspace_runtime = create_local_workspace_runtime()
repository = create_default_skill_repository("./skills", workspace_runtime=workspace_runtime)

TeamAgent(
    name="content_team_with_skill",
    model=model,
    members=[researcher, writer],
    instruction="""xxx""",
    tools=[skill_tool_set],  # SkillToolSet 实例，提供 Skill 的搜索、读取、执行等工具能力
    skill_repository=repository,
    share_member_interactions=True,
)
```

更多关于 Skill 的用法见 [skill.md](./skill.md)。

**完整示例：**
- [examples/team_with_skill/run_agent.py](../../examples/team_with_skill/run_agent.py) - Leader 集成 Skills 的 TeamAgent 示例
- *注意：该示例在instruction里，为了演示，强制让Leader调用了skills的系列工具，实际使用时不需要加这些prompt*。

## Team历史会话消息管理

TeamAgent 提供了多个参数来控制历史信息的共享：

### share_member_interactions

控制当前轮次中，成员间的交互信息是否共享给其他成员：

- `share_member_interactions=True`：后执行的成员会在看到先执行成员的任务和响应，便于成员间协作和信息衔接。
- `share_member_interactions=False`（默认）：各成员互相隔离，只能看到 Leader 分配给自己的任务，不感知其他成员的执行情况。

```python
team = TeamAgent(
    name="team",
    model=model,
    members=[agent1, agent2],
    share_member_interactions=True,  # 成员可以看到其他成员的任务和响应
)
```

当启用时，后执行的成员可以看到先执行成员的任务和响应，注入格式如下：
```
<member_interaction_context>
See below interactions with other team members.
Member: researcher
Task: Search for AI information
Response: Found relevant AI research...
</member_interaction_context>
```

### num_member_history_runs

控制成员是否看到自己的历史交互记录（同一轮和多轮）：

```python
team = TeamAgent(
    name="team",
    model=model,
    members=[agent1, agent2],
    num_member_history_runs=0,  # 默认值，关闭成员自历史
)
```

- `num_member_history_runs=0`：不注入成员自己的历史。
- `num_member_history_runs=1`：注入最近 1 轮成员自历史，适合"同一轮内 Leader 多次委派同一成员"场景。
- `num_member_history_runs>1`：注入最近 N 轮成员自历史，支持跨多轮延续上下文。

示例（同一轮内保留成员自历史）：

```python
team = TeamAgent(
    name="team",
    model=model,
    members=[agent1, agent2],
    share_member_interactions=True,  # 仍可共享其他成员交互
    num_member_history_runs=1,       # 成员看到自己的最近一轮历史
)
```

当启用后，成员会收到如下格式的自历史上下文：

```
<member_self_history_context>
See below your previous interactions in this team.
Task: ...
Response: ...
</member_self_history_context>
```

### share_team_history

控制是否将团队级别的对话历史共享给成员：

```python
team = TeamAgent(
    name="team",
    model=model,
    members=[agent1, agent2],
    share_team_history=True,        # 与成员共享团队历史
    num_team_history_runs=3,        # 共享最近 3 轮的历史
)
```

### add_history_to_leader

控制 Leader 是否包含过往对话历史（支持多轮对话）：

```python
team = TeamAgent(
    name="team",
    model=model,
    members=[agent1, agent2],
    add_history_to_leader=True,     # Leader 包含历史（默认开启）
    num_history_runs=3,             # 包含最近 3 轮历史
)
```

## 控制Member注入Team的会话

当成员执行完毕后，其产生的所有消息（包括中间的工具调用、工具返回结果、最终文本回复等）会被注入回 Leader 的上下文中，作为委派记录（delegation record）的 response 部分。默认情况下，所有消息都会被保留（`keep_all_member_message`），但当成员执行步骤较多时，这可能会导致 Leader 上下文过长，影响推理效率和 token 消耗。

因此，框架通过 `member_message_filter` 可以对成员的消息进行过滤或摘要，控制哪些内容最终传递给 Leader。过滤器接收成员执行过程中产生的 `List[Content]`，返回一个 `str` 作为委派记录中的响应文本。支持三种配置方式：

- **全局配置**：传入单个过滤函数，对所有成员生效
- **按成员配置**：传入 `Dict[str, filter]`，为不同成员指定不同的过滤策略，未指定的成员使用默认的 `keep_all_member_message`
- **自定义函数**：实现签名为 `(List[Content]) -> str` 的同步或异步函数

### 内置

```python
from trpc_agent.teams import keep_all_member_message, keep_last_member_message

# 保留所有消息（默认行为）
team = TeamAgent(
    name="team",
    model=model,
    members=[analyst],
    member_message_filter=keep_all_member_message,
)

# 只保留最后一条消息（适用于多步骤成员，只关心最终结果）
team = TeamAgent(
    name="team",
    model=model,
    members=[analyst],
    member_message_filter=keep_last_member_message,
)
```

### 为不同成员配置

```python
team = TeamAgent(
    name="team",
    model=model,
    members=[researcher, writer],
    member_message_filter={
        "researcher": keep_all_member_message,   # 研究员保留所有消息
        "writer": keep_last_member_message,      # 写手只保留最后消息
    },
)
```

### 自定义

```python
from typing import List
from trpc_agent.types import Content

async def custom_filter(messages: List[Content]) -> str:
    """自定义消息过滤器"""
    # 只提取文本内容
    texts = []
    for msg in messages:
        if msg.parts:
            for part in msg.parts:
                if part.text:
                    texts.append(part.text)
    return "\n".join(texts[-2:])  # 只保留最后两条

team = TeamAgent(
    name="team",
    model=model,
    members=[analyst],
    member_message_filter=custom_filter,
)
```

**完整示例：**
- [examples/team_member_message_filter/run_agent.py](../../examples/team_member_message_filter/run_agent.py) - 成员消息过滤示例

## Human-in-the-Loop (HITL)

TeamAgent 支持 Human-in-the-Loop，但**只支持 Leader 触发**。成员 Agent 不能配置 `LongRunningFunctionTool`，如果成员尝试触发 HITL 将抛出 `RuntimeError`。

更多关于 HITL 的细节见 [human_in_the_loop.md](./human_in_the_loop.md)。

**重要限制**：
- `LongRunningFunctionTool` 只能配置在 TeamAgent 的 `tools` 参数中（Leader 使用）
- 成员 Agent（包括嵌套的 TeamAgent）不能配置 `LongRunningFunctionTool`
- 如果成员触发 `LongRunningEvent`，将抛出 `RuntimeError`

```python
from trpc_agent.tools import LongRunningFunctionTool, FunctionTool
from trpc_agent.events import LongRunningEvent
from trpc_agent.types import FunctionResponse

# 定义需要人工审批的工具
async def request_approval(content: str, reason: str) -> dict:
    """Request human approval before proceeding."""
    return {
        "status": "pending",
        "content": content,
        "reason": reason,
    }

# 创建长时运行工具
approval_tool = LongRunningFunctionTool(request_approval)

# ❌ 错误：成员不能配置 LongRunningFunctionTool
# assistant_wrong = LlmAgent(
#     name="assistant",
#     model=model,
#     tools=[approval_tool],  # ❌ 会在运行时抛出 RuntimeError
# )

# ✅ 正确：成员只配置普通工具
assistant = LlmAgent(
    name="assistant",
    model=model,
    tools=[FunctionTool(some_normal_tool)],  # ✅ 普通工具
)

# ✅ 正确：HITL 工具配置在 TeamAgent 的 tools 中（Leader 使用）
team = TeamAgent(
    name="approval_team",
    model=model,
    members=[assistant],
    instruction="""When user requests to publish content,
use request_approval tool to get human approval first.""",
    tools=[approval_tool],  # ✅ Leader 可以使用 HITL 工具
)
```

**运行时处理 HITL**：

```python
async for event in runner.run_async(...):
    if isinstance(event, LongRunningEvent):
        print(f"Waiting for human approval: {event.function_call.args}")

        # 模拟人工审批
        response_data = {"status": "approved", "approved_by": "admin"}

        # 构建恢复内容
        resume_response = FunctionResponse(
            id=event.function_response.id,
            name=event.function_response.name,
            response=response_data,
        )
        resume_content = Content(
            role="user",
            parts=[Part(function_response=resume_response)]
        )

        # 使用新的 runner 恢复执行
        async for resume_event in runner.run_async(
            user_id=USER_ID,
            session_id=session_id,
            new_message=resume_content,
        ):
            # 处理恢复后的事件
            pass
```

**完整示例：**
- [examples/team_human_in_the_loop/run_agent.py](../../examples/team_human_in_the_loop/run_agent.py) - HITL 示例

## 多种成员

TeamAgent 的成员不限于 LlmAgent，任何继承自 BaseAgent 并支持 `override_messages` 的 Agent 都可以作为成员。

**注意：目前Member成员暂时只支持：LlmAgent、ClaudeAgent、LangGraphAgent、RemoteA2aAgent，需要其他类型的Agent成员欢迎联系我们支持**

### ClaudeAgent 作为成员

```python
from trpc_agent_ecosystem.agents.claude import ClaudeAgent, setup_claude_env

# 设置 Claude 环境
setup_claude_env(proxy_host="0.0.0.0", proxy_port=8083, claude_models={"all": model})

# 创建 Claude 成员
claude_agent = ClaudeAgent(
    name="claude_expert",
    model=model,
    description="Expert powered by Claude",
    instruction="You are an expert assistant.",
    tools=[FunctionTool(some_tool)],
)
claude_agent.initialize()

# 作为团队成员
team = TeamAgent(
    name="hybrid_team",
    model=model,
    members=[claude_agent],
    instruction="Delegate expert tasks to claude_expert.",
)
```

**完整示例：**
- [examples/team_member_agent_claude/run_agent.py](../../examples/team_member_agent_claude/run_agent.py) - ClaudeAgent 成员示例

### LangGraphAgent 作为成员

```python
from trpc_agent.agents import LangGraphAgent

# 构建 LangGraph
graph = build_your_langgraph()

# 创建 LangGraph 成员
langgraph_agent = LangGraphAgent(
    name="langgraph_expert",
    description="Expert powered by LangGraph",
    graph=graph,
    instruction="You are a calculation expert.",
)

# 作为团队成员
team = TeamAgent(
    name="hybrid_team",
    model=model,
    members=[langgraph_agent],
    instruction="Delegate calculation tasks to langgraph_expert.",
)
```

**完整示例：**
- [examples/team_member_agent_langgraph/run_agent.py](../../examples/team_member_agent_langgraph/run_agent.py) - LangGraphAgent 成员示例

### 远程 A2A Agent 作为成员

```python
from trpc_agent_ecosystem.a2a.agent import TrpcRemoteA2aAgent

# 创建远程 A2A 成员
remote_agent = TrpcRemoteA2aAgent(
    name="remote_service",
    service_name="trpc.agent.team_a2a.weather",  # 或使用 resolver_result
    description="Remote weather service agent",
)
await remote_agent.initialize()

# 作为团队成员
team = TeamAgent(
    name="distributed_team",
    model=model,
    members=[remote_agent],
    instruction="Delegate weather queries to remote_service.",
)
```

**完整示例：**
- [examples/team_member_agent_remote_a2a/run_agent.py](../../examples/team_member_agent_remote_a2a/run_agent.py) - 远程 A2A 成员示例

### TeamAgent 作为成员（嵌套团队）

TeamAgent 本身也可以作为另一个 TeamAgent 的成员，实现层级化的团队结构。这种模式适用于复杂的组织架构，例如：项目经理 → 开发团队 → [后端开发, 前端开发]。

```python
from trpc_agent.agents import LlmAgent
from trpc_agent.teams import TeamAgent

# === 第二层：开发团队成员（LlmAgent）===
backend_dev = LlmAgent(
    name="backend_dev",
    model=model,
    description="Backend development expert",
    instruction="You are a backend developer. Design APIs and server-side logic.",
    tools=[FunctionTool(design_api)],
)

frontend_dev = LlmAgent(
    name="frontend_dev",
    model=model,
    description="Frontend development expert",
    instruction="You are a frontend developer. Design UI components.",
    tools=[FunctionTool(design_ui)],
)

# === 第一层：开发团队（嵌套 TeamAgent）===
dev_team = TeamAgent(
    name="dev_team",
    model=model,
    description="Development team for technical implementation",
    members=[backend_dev, frontend_dev],
    instruction="""You are the dev team leader. Coordinate:
1. Backend tasks → delegate to backend_dev
2. Frontend tasks → delegate to frontend_dev
Then integrate the technical deliverables.""",
    share_member_interactions=True,
)

# === 第一层：文档编写（LlmAgent）===
doc_writer = LlmAgent(
    name="doc_writer",
    model=model,
    description="Technical documentation writer",
    instruction="You are a technical writer. Create clear documentation.",
    tools=[FunctionTool(format_docs)],
)

# === 顶层：项目经理（包含 TeamAgent 作为成员）===
project_manager = TeamAgent(
    name="project_manager",
    model=model,
    members=[dev_team, doc_writer],  # dev_team 是一个 TeamAgent！
    instruction="""You are the project manager. For each request:
1. Delegate technical tasks to dev_team
2. Delegate documentation to doc_writer
3. Synthesize the final deliverables""",
    share_member_interactions=True,
)
```

**执行流程**：
```
用户请求 → project_manager（顶层 TeamAgent）
    → 委派给 dev_team（嵌套 TeamAgent）
        → dev_team 的 Leader 委派给 backend_dev
        → dev_team 的 Leader 委派给 frontend_dev
        → dev_team 返回整合结果
    → 委派给 doc_writer
    → project_manager 综合最终响应
```

```python
# 错误示例：嵌套团队成员使用 HITL 工具会导致错误
inner_agent = LlmAgent(
    name="inner_agent",
    tools=[LongRunningFunctionTool(approval_func)],  # ❌ 会抛出 RuntimeError
)

inner_team = TeamAgent(
    name="inner_team",
    members=[inner_agent],
)

outer_team = TeamAgent(
    name="outer_team",
    members=[inner_team],  # inner_team 作为成员
    tools=[LongRunningFunctionTool(approval_func)],  # ✅ 只有顶层 Leader 可以使用 HITL
)
```

**完整示例：**
- [examples/team_member_agent_team/run_agent.py](../../examples/team_member_agent_team/run_agent.py) - 嵌套 TeamAgent 示例

## 其他配置选项

### parallel_execution

控制多个委派是否并行执行。当 Leader 在单次回合中同时委派给多个成员时，可以选择顺序执行或并行执行：

```
顺序执行 (parallel_execution=False，默认):
  Leader -> analyst1 (1s) -> analyst2 (1s) -> analyst3 (1s)
  总时间: 3 秒

并行执行 (parallel_execution=True):
  Leader -> [analyst1 | analyst2 | analyst3] (同时运行)
  总时间: ~1 秒 (取决于最长的单个执行时间)
```

使用示例：

```python
# 创建多个分析师成员
market_analyst = LlmAgent(
    name="market_analyst",
    model=model,
    description="Market trends analysis expert",
    instruction="Analyze market trends for the given topic.",
    tools=[FunctionTool(analyze_market_trends)],
)

competitor_analyst = LlmAgent(
    name="competitor_analyst",
    model=model,
    description="Competitor analysis expert",
    instruction="Analyze competitors for the given topic.",
    tools=[FunctionTool(analyze_competitor)],
)

risk_analyst = LlmAgent(
    name="risk_analyst",
    model=model,
    description="Risk assessment expert",
    instruction="Assess risks for the given topic.",
    tools=[FunctionTool(analyze_risks)],
)

# 创建启用并行执行的团队
team = TeamAgent(
    name="analysis_team",
    model=model,
    members=[market_analyst, competitor_analyst, risk_analyst],
    instruction="""You are a strategic analysis team leader.
When asked for comprehensive analysis, delegate to ALL THREE analysts
SIMULTANEOUSLY in a single response to enable parallel execution.
After receiving all results, synthesize them into a strategic recommendation.""",
    parallel_execution=True,  # 启用并行执行
    share_member_interactions=True,
)
```
**适用场景**：
- 多个成员的任务相互独立，不依赖彼此的输出
- 需要从多个专家获取并行分析结果
- 希望减少总执行时间

**完整示例：**
- [examples/team_parallel_execution/run_agent.py](../../examples/team_parallel_execution/run_agent.py) - 并行执行示例

### max_iterations

防止无限委派循环：

```python
team = TeamAgent(
    name="team",
    model=model,
    members=[agent1, agent2],
    max_iterations=20,  # 最大委派迭代次数（默认 20）
)
```

## 实现其他团队模式

trpc-agent 的 TeamAgent 实现了 Agno 的 Coordinate 模式。对于 Agno 框架中的其他团队模式，可以通过组合 trpc-agent 的 Multi Agents 组件来实现。

下面描述的不同模式的详细介绍，见 [Agno Team协作模式](https://docs.agno.com/basics/teams/delegation)。

### Members Respond Directly（成员直接响应）

在 Agno 中，设置 `respond_directly=True` 可以让成员的响应直接返回给用户，而不经过 Leader 综合。

在 trpc-agent 中，可以使用 ChainAgent 实现类似效果：

```python
from trpc_agent.agents import LlmAgent, ChainAgent

# 意图识别 Agent - 分析用户请求并构建任务描述
intent_agent = LlmAgent(
    name="intent_agent",
    model=model,
    instruction="""You are an intent analyzer. Analyze user input and:
1. Identify the user's intent (technical issue or sales inquiry)
2. Extract key information from the request
3. Formulate a clear task description for the next agent

Output format:
Intent: [technical/sales]
Task: [clear task description]""",
    output_key="analyzed_task",
)

# 路由 Agent - 根据意图委派给对应子 Agent
router = LlmAgent(
    name="router",
    model=model,
    instruction="""You are a router. Based on the analyzed task:

{analyzed_task}

Route to the appropriate agent:
- If Intent is technical → transfer to technical_support
- If Intent is sales → transfer to sales_consultant

Just route with the task, don't answer yourself.""",
    sub_agents=[technical_support, sales_consultant],  # 子 Agent 会直接响应
)

# 组合：意图识别 → 路由（子 Agent 直接响应）
respond_directly_pipeline = ChainAgent(
    name="respond_directly_pipeline",
    sub_agents=[intent_agent, router],
)
```

**核心思路**：
1. 使用 `intent_agent` 分析用户请求并构建任务描述
2. 使用 `router` 根据意图将任务委派给对应的子 Agent
3. 子 Agent 的响应即为最终响应，不经过额外综合

### Send Input Directly to Members（直接传递原始输入）

在 Agno 中，设置 `determine_input_for_members=False` 可以将用户原始输入直接传递给成员，而不由 Leader 改写。

在 trpc-agent 中，可以这样实现：

```python
from trpc_agent.agents import LlmAgent, ChainAgent

# 路由 Agent - 只负责选择目标，不改写输入
router = LlmAgent(
    name="router",
    model=model,
    instruction="""You are a router. Just decide which agent to use:
- English questions → transfer to english_agent
- Japanese questions → transfer to japanese_agent
Do NOT modify or rephrase the user's original question.""",
    sub_agents=[english_agent, japanese_agent],
)

# 汇总 Agent（如果需要）
summarizer = LlmAgent(
    name="summarizer",
    model=model,
    instruction="""Summarize the response from: {previous_response}""",
    output_key="final_response",
)

# 组合流程
pipeline = ChainAgent(
    name="raw_input_pipeline",
    sub_agents=[router, summarizer],
)
```

**核心思路**：在路由 Agent 的 instruction 中明确指示不要改写用户输入，直接使用 `transfer_to_agent` 转发。

### Passthrough Teams（透传用户请求）

在 Agno 中，同时设置 `respond_directly=True` 和 `determine_input_for_members=False` 透传用户请求 —— Leader 只做路由，不处理输入也不综合输出。

在 trpc-agent 中：

```python
from trpc_agent.agents import LlmAgent

# 纯路由 Agent - 透传模式
passthrough_router = LlmAgent(
    name="passthrough_router",
    model=model,
    instruction="""You are a pure router. Based on the question type:
- Big questions → transfer to big_question_agent
- Small questions → transfer to small_question_agent
Do NOT answer, just route. Do NOT modify the question.""",
    sub_agents=[big_question_agent, small_question_agent],
)

# 直接使用 passthrough_router 作为入口
runner = Runner(app_name="app", agent=passthrough_router, ...)
```

**核心思路**：配置 `sub_agents` 的 LlmAgent 本身就支持透传模式，只要在 instruction 中明确指示只做路由即可。

### Delegate to All Members（并发委派给所有成员）

在 Agno 中，设置 `delegate_to_all_members=True` 可以将任务同时委派给所有成员。

在 trpc-agent 中，使用 ChainAgent + ParallelAgent 组合：

```python
from trpc_agent.agents import LlmAgent, ParallelAgent, ChainAgent

# 多个专家 Agent
reddit_researcher = LlmAgent(
    name="reddit_researcher",
    model=model,
    instruction="Research the topic on Reddit.",
    output_key="reddit_result",
)

hackernews_researcher = LlmAgent(
    name="hackernews_researcher",
    model=model,
    instruction="Research the topic on HackerNews.",
    output_key="hackernews_result",
)

academic_researcher = LlmAgent(
    name="academic_researcher",
    model=model,
    instruction="Research academic papers on this topic.",
    output_key="academic_result",
)

# 并行执行所有研究员
parallel_research = ParallelAgent(
    name="parallel_research",
    sub_agents=[reddit_researcher, hackernews_researcher, academic_researcher],
)

# 汇总 Agent
summarizer = LlmAgent(
    name="summarizer",
    model=model,
    instruction="""Synthesize research results from multiple sources:

Reddit findings: {reddit_result}
HackerNews findings: {hackernews_result}
Academic findings: {academic_result}

Create a comprehensive summary.""",
    output_key="final_summary",
)

# 组合：并行研究 → 汇总
research_team = ChainAgent(
    name="research_team",
    sub_agents=[parallel_research, summarizer],
)
```

**核心思路**：
1. 使用 `ParallelAgent` 并行执行所有成员
2. 每个成员通过 `output_key` 保存结果
3. 使用后续 Agent 通过模板变量 `{output_key}` 引用并汇总结果

### 模式对比总结

| Agno 模式 | trpc-agent 实现方式 |
|-----------|---------------------|
| Coordinate（默认） | `TeamAgent` |
| `respond_directly=True` | `LlmAgent` + `sub_agents`（子 Agent 直接响应） |
| `determine_input_for_members=False` | `LlmAgent` + `sub_agents` + instruction 指示不改写 |
| Passthrough | `LlmAgent` + `sub_agents`（纯路由 instruction） |
| `delegate_to_all_members=True` | `ChainAgent` + `ParallelAgent` + 汇总 Agent |

更多 Multi Agents 编排模式详见：[Multi Agents 文档](./multi_agents.md)
