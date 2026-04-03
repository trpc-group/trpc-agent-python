# Human-In-The-Loop

Agent处理过程中，有部分场景需要引入人工参与判断或者调整，以提高任务完成的准确率，比如下面这些场景：
- 风险操作审批：常用在Agent生成SQL或者Shell脚本时，是否执行往往需要人工审批，以Agent生成命令行为例，如果人工同意，那么会拉起terminal执行，再将执行结果传给Agent，如果不同意，则可能意味着命令行生成得有问题，需要Agent再尝试生成其他命令。
- 执行计划批准：对于一个复杂的任务，Agent会先生成一个Plan，然后交给用户确认计划是否ok，如果用户同意，则按计划依次执行，如果用户不同意，可以输入一些调整的prompt，以再重新生成符合用户要求的Plan。

## 实现机制

当前业界实现有两种：一种是提供一个UserAgent负责和用户的沟通（比如：autogen/agentscope），通过多Agent编排为Agent应用，这种方法简单易用，但多引入一个Agent会使应用变得复杂，且降低和用户交互的灵活度（因为用户交互的接口是固定的），无法覆盖所有场景；还有一种方法是将Tool作为人工参与的结合点（比如：langgraph/agno等），将人工操作作为Tool的过程，把人工产生的结果作为Tool的调用结果，这种方法很灵活，但引入了实现的复杂度。

框架当前支持Tool作为人工参与结合点的方法，在 `LlmAgent` 上提供 `LongRunningFunctionTool` 和 `LongRunningEvent` 来实现这个机制。如下图所示，具体的，当用户使用 `LongRunningFunctionTool` 创建一个工具后，Agent在调用这个工具传递的参数，可以被视作Agent产生的需要人工确认的操作，用户可以在Tool的实现里，将这些的操作组织成dict结果作为Tool的返回。当 `LongRunningFunctionTool` 被执行之后，框架将会产生 `LongRunningEvent` 事件，用户在识别到这个事件时，可以执行相应的人工操作，然后将执行的操作提交给Agent继续执行。

<img src="../assets/imgs/human_in_the_loop.png" width="600" />


### LongRunningEvent
`LongRunningEvent` 是一个特殊的事件类型，表示 Agent 执行被暂停，等待人工参与。
- `function_call`: 触发操作的Tool调用；
- `function_response`: Tool的初始响应（通常包含等待状态信息），可以基于此对象（主要是id和name），将人工操作的结果提交给Agent，见下面的代码示例；

## LlmAgent用法

### 1. 创建LongRunningFunctionTool

首先定义一个需要人工审批的Tool：

```python
async def human_approval_required(task_description: str, details: dict) -> dict:
    """A long-running function that requires human approval.

    Args:
        task_description: Description of the task requiring approval
        details: Additional details about the task

    Returns:
        A dictionary indicating the task is pending human approval
    """
    return {
        "status": "pending_approval",
        "message": f"Task '{task_description}' requires human approval",
        "details": details,
        "approval_id": str(uuid.uuid4()),
        "timestamp": time.time(),
    }


from trpc_agent_sdk.tools import LongRunningFunctionTool
approval_tool = LongRunningFunctionTool(human_approval_required)
```

### 2. 配置Agent

使用 `LongRunningFunctionTool` 包装工具并配置Agent：

```python
import os
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import LongRunningFunctionTool

def create_agent():
    model = OpenAIModel(
        model_name=os.getenv("TRPC_AGENT_MODEL_NAME", ""),
        api_key=os.getenv("TRPC_AGENT_API_KEY", ""),
        base_url=os.getenv("TRPC_AGENT_BASE_URL", ""),
    )
    approval_tool = LongRunningFunctionTool(human_approval_required)

    agent = LlmAgent(
        name="human_in_loop_agent",
        description="Agent demonstrating long-running tools with human-in-the-loop",
        model=model,
        instruction="""You are an assistant that can handle long-running operations requiring human approval.
When you encounter tasks that need approval, use the appropriate tool and wait for human intervention.""",
        tools=[approval_tool],
    )
    return agent
```

### 3. 捕获LongRunningEvent

```python
@dataclass
class InvocationParams:
    """Parameters for running an invocation"""
    user_id: str
    session_id: str
    agent: LlmAgent
    session_service: InMemorySessionService
    app_name: str

async def run_invocation(
    params: InvocationParams,
    content: Content,
) -> Optional[LongRunningEvent]:
    """Run an invocation with a fresh runner instance."""
    runner = Runner(
        app_name=params.app_name,
        agent=params.agent,
        session_service=params.session_service,
    )

    captured_long_running_event = None

    try:
        async for event in runner.run_async(
            user_id=params.user_id,
            session_id=params.session_id,
            new_message=content,
        ):
            if isinstance(event, LongRunningEvent):
                # 捕获长运行事件
                captured_long_running_event = event
                print(f"\n🔄 [Long-running operation detected]")
                print(f"   Function: {event.function_call.name}")
                print(f"   Response: {event.function_response.response}")
            elif event.content and event.content.parts and event.author != "user":
                # 处理其他事件...
                pass
    finally:
        await runner.close()

    return captured_long_running_event
```

### 4. 执行人工操作

注意到，只需要 `FunctionResponse` 的`id`、`name`、`response`，即可创建用于恢复Agent执行的Content，在Agent提供服务的场景，只需要返回这些信息给前端，前端在人工操作之后，下次Agent调用时候，带入这些信息即可。

```python
async def run_agent():
    """Run the agent with support for long-running events"""

    # 创建Agent和Session Service
    agent = create_agent()
    session_service = InMemorySessionService()

    params = InvocationParams(
        user_id="demo_user",
        session_id=str(uuid.uuid4()),
        agent=agent,
        session_service=session_service,
        app_name="agent_demo",
    )

    # 触发长运行操作的查询
    query = "I need approval to delete the production database. The details are: environment=prod, database=user_data, reason=migration"
    user_content = Content(parts=[Part.from_text(text=query)])

    # 第一次运行 - 触发人工审批
    long_running_event = await run_invocation(params, user_content)

    # 模拟人工干预
    if long_running_event:
        print("\n👤 Human intervention simulation...")
        await asyncio.sleep(2)  # 模拟人工思考时间

        # 获取Tool返回的初始响应
        response_data = long_running_event.function_response.response
        if response_data["status"] != "pending_approval":
            print("   ❌ Invalid response status")
            return

        # 模拟人工提供审批输入
        response_data["status"] = "approved"
        response_data["message"] = "APPROVED: The database deletion is approved for migration purposes."
        response_data["approved_by"] = "admin"
        response_data["timestamp"] = time.time()
        # 你也能创建新的工具返回结果，而不是复用function_response.response
        # response_data = {"user_is_approved": True}

        # 创建用于恢复Agent执行的消息，如果是调用Agent服务的场景
        # 只需要返回function_response的id和name给前端，下次调用请求里包含这些信息，用于创建resume_content即可
        resume_function_response = FunctionResponse(
            id=long_running_event.function_response.id,
            name=long_running_event.function_response.name,
            response=response_data,
        )
        resume_content = Content(role="user", parts=[Part(function_response=resume_function_response)])

        # 继续执行Agent
        await run_invocation(params, resume_content)
```

## LangGraphAgent用法

LangGraphAgent适配了LangGraph的interrupt与框架的LongRunningEvent交互机制，但与LlmAgent不同的是，他能在Node内部进行恢复原来的会话，而LlmAgent的Tool内部是无法继续执行的。

为了使用LangGraphAgent的interrupt能力，请务必开启 `checkpoint`，因为该能力会暂停图的执行，需要存储图的状态信息，以恢复执行。

注意，LangGraph在恢复执行时，会再次从Node的开头，执行到interrupt的位置，也就是说，该段逻辑会被执行两次，存在耗时操作请注意优化。

### 1. 构建包含确认是否接收工具输出的Graph

**请注意，一定要开启checkpoint**

```python
import os
from typing import Annotated, Literal, TypedDict

from langchain.chat_models import init_chat_model
from langchain_core.tools import tool
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import tools_condition, ToolNode
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import InMemorySaver

from trpc_agent_sdk.agents import langgraph_llm_node, langgraph_tool_node


@tool
@langgraph_tool_node
def execute_database_operation(operation: str, database: str, details: dict) -> str:
    """Execute a database operation that requires approval.

    Args:
        operation: The type of operation ('delete', 'update', 'create')
        database: The database name
        details: Additional operation details
    """
    return f"Database operation '{operation}' on '{database}' executed successfully with details: {details}"


class State(TypedDict):
    messages: Annotated[list, add_messages]
    task_description: str
    approval_status: str


def build_graph():
    """Build a LangGraph with human-in-the-loop approval using interrupt."""

    model = init_chat_model(
        os.getenv("TRPC_AGENT_MODEL_NAME", ""),
        api_key=os.getenv("TRPC_AGENT_API_KEY", ""),
        api_base=os.getenv("TRPC_AGENT_BASE_URL", ""),
    )
    tools = [execute_database_operation]
    llm_with_tools = model.bind_tools(tools)

    @langgraph_llm_node
    def chatbot(state: State):
        """Chatbot node that can use tools"""
        return {"messages": [llm_with_tools.invoke(state["messages"])]}

    # Human approval node using LangGraph interrupt
    def human_approval(state: State) -> Command[Literal["approved_path", "rejected_path"]]:
        """Human approval node that interrupts execution for human input."""
        task_info = {
            "_node_name": "human_approval",
            "question": "Do you approve this database operation?",
        }

        # Interrupt execution and wait for human input
        decision = interrupt(task_info)
        approval_status = decision.get("status", "rejected")

        if approval_status in ["approved", "approve", "yes", "true"]:
            return Command(goto="approved_path", update={"approval_status": "approved"})
        else:
            return Command(goto="rejected_path", update={"approval_status": "rejected"})

    # Approved and rejected path nodes
    def approved_node(state: State) -> State:
        """Handle approved operations"""
        return {"messages": [{"role": "assistant", "content": "Operation has been approved and will be executed."}]}

    def rejected_node(state: State) -> State:
        """Handle rejected operations"""
        return {"messages": [{"role": "assistant", "content": "Operation has been rejected and cancelled."}]}

    # Build the graph
    graph_builder = StateGraph(State)
    graph_builder.add_node("chatbot", chatbot)
    graph_builder.add_node("human_approval", human_approval)
    graph_builder.add_node("approved_path", approved_node)
    graph_builder.add_node("rejected_path", rejected_node)

    tool_node = ToolNode(tools=tools)
    graph_builder.add_node("tools", tool_node)

    graph_builder.add_edge(START, "chatbot")
    graph_builder.add_conditional_edges("chatbot", tools_condition)
    graph_builder.add_edge("tools", "human_approval")
    graph_builder.add_edge("approved_path", END)
    graph_builder.add_edge("rejected_path", END)

    # MUST Use checkpointer for interrupt support
    checkpointer = InMemorySaver()
    return graph_builder.compile(checkpointer=checkpointer)
```

### 2. 创建LangGraphAgent

```python
from trpc_agent_sdk.agents import LangGraphAgent

def create_agent():
    """Create a LangGraph Agent with human-in-the-loop support"""
    graph = build_graph()

    return LangGraphAgent(
        name="human_in_loop_langgraph_agent",
        description="A LangGraph agent that requires human approval for database operations",
        graph=graph,
        instruction="""You are a database management assistant that requires human approval for all operations.

When a user requests a database operation:
1. Use the execute_database_operation tool to prepare the operation
2. The system will automatically request human approval
3. Only proceed if the human approves the operation

Always be clear about what operation you're about to perform and why it needs approval.""",
    )
```

### 3. 捕获和处理LongRunningEvent

LangGraphAgent的Human-In-The-Loop处理方式与LlmAgent相同，都是通过捕获`LongRunningEvent`事件：

```python
from dataclasses import dataclass
from typing import Optional

from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.agents import LangGraphAgent
from trpc_agent_sdk.events import LongRunningEvent
from trpc_agent_sdk.types import Content, Part, FunctionResponse


@dataclass
class InvocationParams:
    """Parameters for running an invocation"""
    user_id: str
    session_id: str
    agent: LangGraphAgent
    session_service: InMemorySessionService
    app_name: str


async def run_invocation(
    params: InvocationParams,
    content: Content,
) -> Optional[LongRunningEvent]:
    """Run an invocation with a fresh runner instance."""
    runner = Runner(
        app_name=params.app_name,
        agent=params.agent,
        session_service=params.session_service,
    )

    captured_long_running_event = None

    try:
        async for event in runner.run_async(
            user_id=params.user_id,
            session_id=params.session_id,
            new_message=content,
        ):
            if isinstance(event, LongRunningEvent):
                # 捕获长运行事件
                captured_long_running_event = event
                print(f"\n🔄 [Long-running operation detected]")
                print(f"   Function: {event.function_call.name}")
                print(f"   Response: {event.function_response.response}")
            elif event.content and event.content.parts and event.author != "user":
                # 处理其他事件...
                pass
    finally:
        await runner.close()

    return captured_long_running_event
```

### 4. 执行人工操作

人工干预的处理方式与LlmAgent完全相同：

```python
import asyncio
import uuid

async def run_human_in_loop_agent():
    """Run the agent with support for long-running events"""

    # 创建Agent和Session Service
    agent = create_agent()
    session_service = InMemorySessionService()

    params = InvocationParams(
        user_id="demo_user",
        session_id=str(uuid.uuid4()),
        agent=agent,
        session_service=session_service,
        app_name="langgraph_human_in_loop_demo",
    )

    # 触发长运行操作的查询
    query = "I need to delete the production database 'user_data' for migration purposes. The details are: environment=prod, backup_created=true, reason=migration_to_new_system"
    user_content = Content(parts=[Part.from_text(text=query)])

    # 第一次运行 - 触发人工审批
    long_running_event = await run_invocation(params, user_content)

    # 模拟人工干预
    if long_running_event:
        print("\n👤 Human intervention simulation...")
        await asyncio.sleep(2)  # 模拟人工思考时间

        # 模拟人工决策
        human_decision = "approved"  # or "rejected"
        resume_data = {"status": human_decision}

        # 创建用于恢复Agent执行的消息
        resume_function_response = FunctionResponse(
            id=long_running_event.function_response.id,
            name=long_running_event.function_response.name,
            response=resume_data,
        )
        resume_content = Content(role="user", parts=[Part(function_response=resume_function_response)])

        # 继续执行Agent
        await run_invocation(params, resume_content)
```

## 完整代码示例

完整的示例代码请参考：
- LlmAgent：[examples/llmagent_with_human_in_the_loop/README.md](../../../examples/llmagent_with_human_in_the_loop/README.md)
- LangGraphAgent：[examples/langgraphagent_with_human_in_the_loop/README.md](../../../examples/langgraphagent_with_human_in_the_loop/README.md)
