# tRPC-Agent ClaudeAgent

After the release of [Claude-Code](https://www.claude.com/product/claude-code), due to its **outstanding task planning capabilities**, developers have increasingly attempted to build Agents tailored to their business needs based on this CLI tool. Anthropic also officially released [Claude-Agent-Sdk-Python](https://github.com/anthropics/claude-agent-sdk-python) to integrate with the Claude-Code-CLI tool, enabling rapid Agent development without complex Agent workflow orchestration or repeated prompt tuning. With simple tool configuration and system_instruction writing, you can achieve solid results — the Agent will continuously plan appropriate tool calls to accomplish given tasks.

tRPC-Agent integrates Claude-Agent-Sdk-Python to bridge the tRPC-Agent framework ecosystem with Claude-Code-CLI, making it easy for businesses to migrate existing Agents developed with Claude-Agent-Sdk-Python and reuse the framework's complete ecosystem (including but not limited to internal model integration, tRPC ecosystem, and Agent AI ecosystem). It also provides current framework users with an alternative approach for developing Agents.

## Use Cases

The following scenarios are well-suited for using ClaudeAgent:
1. Code-related Agents: Claude-Code is inherently designed for code generation. By introducing domain knowledge through additional tools, it can write code or reuse Claude-Code's code retrieval tools;
2. Agents requiring file system interaction: Claude-Code has built-in file system read/write operations and supports file search tools, which Agents can directly leverage;
3. Agents for complex tasks: Claude-Code's built-in multi-Agent system architecture and fine-tuned prompts enable step-by-step planning for complex tasks, making it suitable for scenarios where simple configuration can accomplish complex tasks.

Claude-Code also includes the following built-in tools. If your Agent happens to use these tools, consider trying ClaudeAgent to see if it provides improvements in your scenario:

| Tool | Description |
|------|------|
| Bash | Execute shell commands in the environment |
| Edit | Make precise edits to specific files |
| Glob | Find files based on pattern matching |
| Grep | Search for patterns in file contents |
| NotebookEdit | Modify Jupyter notebook cells |
| Read | Read file contents |
| SlashCommand | Run custom slash commands |
| Task | Run sub-Agents to handle complex multi-step tasks |
| TodoWrite | Create and manage structured task lists |
| WebFetch | Fetch content from a specified URL |
| WebSearch | Perform web searches with domain filtering |
| Write | Create or overwrite files |

**Note:**
- **Claude-Code's implementation is closed-source. If your business scenario requires fine-grained optimization or flow control over the underlying Agent, please use it with caution.**

## Design

As shown in the architecture diagram below, tRPC-Agent provides ClaudeAgent and Anthropic Proxy Server to integrate this capability. ClaudeAgent is implemented based on Claude-Agent-SDK-Python, and the Anthropic Proxy Server forwards Claude-Code requests to connect with internal models. The core components are described as follows:
- **ClaudeAgent**: Users develop Claude-Code-based Agents by configuring the **ClaudeAgent provided by the tRPC-Agent-Python framework**. ClaudeAgent can be configured with different Session modes — either letting Claude-Code manage sessions (default), or letting tRPC-Agent manage sessions (by setting ClaudeAgent's `enable_session: True` field).
- **SessionManager - Claude Session**: **Enabled by default**. Sessions are managed by Claude-Code. If your business requires multi-node deployment, please use `hash` routing, as each Session will create a new Claude-Code-Process due to Claude-SDK limitations.
- **Directly Use - tRPC Session**: **Disabled by default**. Sessions are managed by tRPC-Agent. For multi-node deployment, you only need to use the framework's RedisSession. Essentially, each call to Claude-Code is a brand new conversation, except the framework injects historical messages into the conversation. Since the conversation is not managed by Claude-Code, some internal reasoning information is missing, so multi-turn conversation performance may be inferior to Claude Session in scenarios that depend on internal reasoning information.
- **Claude Code Process**: A process is spawned by the Claude-Agent-Python-SDK, interacting with Claude-Code-CLI via stdio. Each ClaudeSession manages the interaction with one subprocess.
- **Tools**: When configuring an Agent, users can use both Claude-Code's built-in tools and custom tools. The framework automatically injects them into the CLI.
- **Model**: Like LlmAgent, users can freely define the model used by the Agent. When Claude-Code-CLI executes, it will call this model. Any model compatible with the framework can be configured.
- **Anthropic Proxy Process**: The framework automatically spawns this proxy subprocess to forward Claude-Code-CLI requests to internal model services. Businesses can freely configure models from Venus, Hunyuan, Tencent Cloud, etc. Note the `Default Claude Request` section — even if a model is configured for ClaudeAgent, not all model calls during CLI execution will use the configured model. Some internal processes and simple calls will use the default models (i.e., claude-opus, claude-haiku, claude-sonnet). The framework provides a mechanism in the Proxy process to forward these default model calls to the user-configured model.

<p align="center">
  <img src="../assets/imgs/claude_agent_architecture.png" alt="ClaudeAgent's Architecture" />
</p>

## Usage Guide

### Installation

Before using ClaudeAgent, please install the Claude-Code-CLI tool in your environment:
```bash
npm install -g @anthropic-ai/claude-code
```

Then install the tRPC-Agent extension package for ClaudeAgent:
```bash
pip install trpc-agent[agent-claude]
```

### Usage

The following example demonstrates the usage by developing a code generation Agent. For the complete example, see: [examples/claude_agent_with_code_writer/run_agent.py](../../../examples/claude_agent_with_code_writer/run_agent.py).

The project structure of this example is as follows:
```
examples/claude_agent_with_code_writer/
├── .env                  # Environment variable configuration
├── run_agent.py          # Main entry point
└── agent/
    ├── __init__.py
    ├── config.py          # Model configuration
    ├── prompts.py         # Prompt configuration
    └── agent.py           # Agent creation and environment management
```

First, retrieve model configuration from environment variables in `agent/config.py`, and define the Agent's instructions in `agent/prompts.py`:
```python
import os
# agent/config.py
def get_model_config() -> tuple[str, str, str]:
    """Get model config from environment variables"""
    api_key = os.getenv('TRPC_AGENT_API_KEY', '')  # Model API key
    url = os.getenv('TRPC_AGENT_BASE_URL', '')  # Model service URL
    model_name = os.getenv('TRPC_AGENT_MODEL_NAME', '')  # Model name
    if not api_key or not url or not model_name:
        raise ValueError('''TRPC_AGENT_API_KEY, TRPC_AGENT_BASE_URL, 
                         and TRPC_AGENT_MODEL_NAME must be set in environment variables''')
    return api_key, url, model_name
```

```python
# agent/prompts.py
INSTRUCTION = "You are a helpful assistant for writing code."
```

Then, configure ClaudeAgent in `agent/agent.py`. Since we are developing a code generation assistant, we only need to specify its role and the tools it uses. As you can see, we specify that it can operate files (Read/Write/Edit), search filenames (Glob) and file contents (Grep), and supports task management (TodoWrite). In addition to Claude-Code's built-in tools, other tools can also be configured via `tools`. If you are unsure how to configure them, refer to [tRPC-Agent FunctionTools Usage](./tool.md) and [tRPC-Agent MCPTools Usage](./tool.md):
```python
# agent/agent.py
from trpc_agent_sdk.server.agents.claude import ClaudeAgent, setup_claude_env, destroy_claude_env
from trpc_agent_sdk.models import LLMModel, OpenAIModel
from claude_agent_sdk.types import ClaudeAgentOptions

from .prompts import INSTRUCTION
from .config import get_model_config

CLAUDE_ALLOWED_TOOLS = ["Read", "Write", "Edit", "TodoWrite", "Glob", "Grep"]

def _create_model() -> LLMModel:
    """Create a model"""
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)

def create_agent() -> ClaudeAgent:
    """Create an agent"""
    return ClaudeAgent(
        name="code_writing_agent",  # Agent name
        description="A helpful Claude assistant for writing code",  # Agent description
        model=_create_model(),  # LLM model to use
        instruction=INSTRUCTION,  # Agent system instruction
        claude_agent_options=ClaudeAgentOptions(
            allowed_tools=CLAUDE_ALLOWED_TOOLS,  # Claude-Code built-in tool allowlist
        ),
        # tools=[...], # Other custom business tools can be placed here, see link for details
        # enable_session=False, # Whether to enable tRPC Session, disabled by default, see architecture design for details
    )
```

Next, provide environment initialization and cleanup methods in the same file. When the process starts, before executing the Agent, you need to initialize the Proxy subprocess and Claude's default model via `setup_claude_env`, and stop the Proxy subprocess via `destroy_claude_env`:
```python
def setup_claude(proxy_host: str = "0.0.0.0", proxy_port: int = 8082):
    """Setup Claude environment (proxy server)"""
    claude_default_model = _create_model()
    setup_claude_env(
        proxy_host=proxy_host,
        proxy_port=proxy_port,
        claude_models={"all": claude_default_model},
    )

def cleanup_claude():
    """Clean up Claude environment (stop proxy server)"""
    destroy_claude_env()
```
In `run_agent.py`, implement the main flow for running the Agent. Initialize the Runtime to execute Claude-Agent-Python-SDK via `agent.initialize()`, run the Agent through Runner, and print out the Agent's various actions. Before the program exits, stop the Claude-Code session via `agent.destroy()`:
```python
# run_agent.py
import asyncio
import uuid
import json
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part

from dotenv import load_dotenv
load_dotenv()


async def run_code_writer_agent():
    """Run the Claude code writer agent demo"""

    app_name = "claude_code_writing_app"

    from agent.agent import create_agent, setup_claude, cleanup_claude

    # Initialize Claude environment: start the Anthropic Proxy Server subprocess
    setup_claude()

    # Create Agent and initialize runtime
    agent = create_agent()
    agent.initialize()

    # Create in-memory session service and Runner
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=agent, session_service=session_service)

    user_id = "demo_user"

    demo_queries = [
        "Write a Python function that calculates the Fibonacci sequence up to n terms, save it to 'fibonacci.py'.",
    ]

    try:
        for query in demo_queries:
            current_session_id = str(uuid.uuid4())

            await session_service.create_session(
                app_name=app_name,
                user_id=user_id,
                session_id=current_session_id,
                state={"user_name": f"{user_id}"},
            )

            print(f"🆔 Session ID: {current_session_id[:8]}...")
            print(f"📝 User: {query}")

            user_content = Content(parts=[Part.from_text(text=query)])

            print("🤖 Assistant: ", end="", flush=True)
            # Asynchronously iterate over the event stream returned by the Agent
            async for event in runner.run_async(user_id=user_id, session_id=current_session_id, new_message=user_content):
                if not event.content or not event.content.parts:
                    continue

                # Streaming text fragment (partial=True), print character by character
                if event.partial:
                    for part in event.content.parts:
                        if part.text:
                            print(part.text, end="", flush=True)
                    continue

                # Complete events: tool calls, tool results, final responses, etc.
                for part in event.content.parts:
                    if part.thought:
                        continue
                    if part.function_call:
                        args_str = json.dumps(part.function_call.args, ensure_ascii=False)[:200]
                        print(f"\n🔧 [Tool Call: {part.function_call.name}({args_str})]", flush=True)
                    elif part.function_response:
                        response_str = json.dumps(part.function_response.response, ensure_ascii=False)[:200]
                        print(f"📊 [Tool Result: {part.function_response.name}({response_str})]", flush=True)

            print("\n" + "-" * 40)

    finally:
        # Resource cleanup: close Runner -> destroy Agent (stop Runtime) -> stop Proxy subprocess
        await runner.close()
        agent.destroy()
        cleanup_claude()
        print("🧹 Claude environment cleaned up")


if __name__ == "__main__":
    asyncio.run(run_code_writer_agent())
```

### Running the Agent

Before running, please set the model-related environment variables (or configure them in the `.env` file):
```bash
export TRPC_AGENT_API_KEY="your-api-key"
export TRPC_AGENT_BASE_URL="your-base-url"
export TRPC_AGENT_MODEL_NAME="your-model-name"
```

Run the Agent program. Example output is shown below. As you can see, ClaudeAgent automatically writes code and saves it to a file based on user instructions. You can replace the query text in `demo_queries` with examples suitable for your scenario. For more examples, refer to the [trpc-agent examples directory](../../../examples/):
```text
[2026-03-17 17:18:47][INFO][trpc_agent][_setup.py:222][68046] Proxy server proxy process started (PID: 68077)
[2026-03-17 17:18:49][INFO][trpc_agent][_setup.py:239][68046] Proxy server is ready at http://0.0.0.0:8082
[2026-03-17 17:18:49][INFO][trpc_agent][_runtime.py:26][68046] ClaudeAgent event loop thread started
🆔 Session ID: 3fe4f9f2...
📝 User: Write a Python function that calculates the Fibonacci sequence up to n terms, save it to 'fibonacci.py'.
🤖 Assistant: Here is the Python function that calculates the Fibonacci sequence up to n terms.
I will save it to a file named fibonacci.py:
🔧 [Tool Call: Write({"file_path": "fibonacci.py", "content": "def fibonacci(n):\n    ..."})]
📊 [Tool Result: Write({"result": "File created successfully at: fibonacci.py"})]
I've created the fibonacci.py file with the Fibonacci sequence implementation.
----------------------------------------
[2026-03-17 17:19:14][INFO][trpc_agent][_runtime.py:38][68046] ClaudeAgent event loop thread stopped
[2026-03-17 17:19:14][INFO][trpc_agent][_runtime.py:61][68046] ClaudeAgent thread terminated successfully
[2026-03-17 17:19:14][INFO][trpc_agent][_setup.py:275][68046] Terminating proxy process (PID: 68077)...
[2026-03-17 17:19:14][INFO][trpc_agent][_setup.py:287][68046] Subprocess terminated successfully.
🧹 Claude environment cleaned up
```

Note that when the program is running, an `anthropic_proxy.log` file will also be written to the working directory. This is the log file of the subprocess that forwards Claude-Code requests. You can review it if interested — it shows the model invocation behavior triggered by Claude-Code.

## Event Mapping

ClaudeAgent receives messages from Claude-Code through `claude_agent_sdk` and converts them into the framework's unified `Event` objects. The mapping between SDK message types and framework events is as follows:

| Claude SDK Message Type | Framework Event | Description |
|-----|-----|-----|
| `TextBlock` in `AssistantMessage` | Text response event | Text content of the model response |
| `ThinkingBlock` in `AssistantMessage` | Thought event | Model reasoning/thinking content |
| `ToolUseBlock` in `AssistantMessage` | Tool-call response event | Tool invocation, including tool name and parameters |
| `ToolResultBlock` in `AssistantMessage` / `UserMessage` | Tool-result response event | Tool execution result |
| `StreamEvent` (`text_delta`) | Partial text event | Streaming text fragment |
| `StreamEvent` (`input_json_delta`) | Partial tool-call event | Streaming tool parameter fragment |
| `SystemMessage` | No event emitted | Logged only |
| `ResultMessage` | No event emitted | Contains usage and duration statistics, logged only |

**Final response determination**: When an event contains no tool calls, no tool results, and is not a partial event, the framework determines it as a final response (`is_final_response()`). At this point, if `output_key` is configured, the text result will be written to the session state.

## Streaming Output

ClaudeAgent supports streaming output, controlled by `run_config.streaming` in Runner:

```python
from trpc_agent_sdk.runners import Runner, RunConfig

runner = Runner(app_name="my_app", agent=agent, session_service=session_service)

async for event in runner.run_async(
    user_id=user_id,
    session_id=session_id,
    new_message=user_content,
    run_config=RunConfig(streaming=True),  # Enable streaming output
):
    if event.partial:
        # Streaming text fragment
        for part in event.content.parts:
            if part.text:
                print(part.text, end="", flush=True)
    else:
        # Complete events (tool calls, tool results, final responses, etc.)
        ...
```

When enabled, ClaudeAgent sets `ClaudeAgentOptions.include_partial_messages = True`, and the SDK returns `StreamEvent` type streaming messages, which the framework converts into events with `partial=True`.

Streaming output supports two types of content:
- **Text stream**: `text_delta` type, each fragment is emitted as a partial text event
- **Tool parameter stream**: `input_json_delta` type, only effective for tools marked with `is_streaming=True`, parameter fragments are emitted via partial tool-call events

## Observability and Tracing

### Event Tracing

ClaudeAgent has a built-in `CustomTraceReporter` that automatically performs trace reporting when each event is emitted:

```python
trace_reporter = CustomTraceReporter(
    agent_name=self.name,              # Agent name, used to identify the trace source
    model_prefix="claude",             # Model trace prefix, distinguishes different types of model calls
    tool_description_prefix="Claude tool",  # Tool trace prefix, marks Claude built-in tools
    text_content_filter=_text_filter,  # Text filter, used for desensitization or truncation of excessively long text content
)
# Automatically traces each emitted event
trace_reporter.trace_event(ctx, event)
```

ClaudeAgent interacts with claude_agent_sdk, which returns a `ResultMessage` after each query is completed, containing statistics for this call (conversation turns, duration, cost, etc.). The framework does not convert it into an event but records it as a debug log:
```
Claude query complete: turns=5, duration=12000ms, cost=$0.05
```

### Proxy Logs

When running ClaudeAgent, the framework generates an `anthropic_proxy.log` file in the working directory, recording the logs of the Anthropic Proxy Server forwarding requests. This can be used to observe Claude-Code's model invocation behavior and troubleshoot issues in the model call chain.

## Advanced Usage
### Tool Configuration

#### Using Claude-Code Built-in Tools

Specify the built-in tools allowed for use via the `allowed_tools` field in the `claude_agent_options` parameter:

```python
from claude_agent_sdk.types import ClaudeAgentOptions

agent = ClaudeAgent(
    name="code_writer",
    description="A helpful Claude assistant for writing code",
    model=model,
    instruction="You are a helpful assistant for writing code.",
    claude_agent_options=ClaudeAgentOptions(
        # Specify allowed Claude-Code built-in tools
        allowed_tools=["Read", "Write", "Edit", "TodoWrite", "Glob", "Grep"],
    ),
)
```

#### Using Custom Tools

ClaudeAgent supports configuring Agent framework tools, including FunctionTool and MCPTool:

```python
from trpc_agent_sdk.tools import FunctionTool, MCPToolset

def get_current_date():
    """Get today's date"""
    return datetime.datetime.now().strftime("%Y-%m-%d")

# Custom MCP toolset
class GoogleSearchMCP(MCPToolset):
    def __init__(self):
        super().__init__()
        self._connection_params = StdioConnectionParams(
            server_params=StdioServerParameters(
                command="npx",
                args=["google-search-mcp"],
            ),
            timeout=30.0,
        )

agent = ClaudeAgent(
    name="travel_planner",
    description="Travel planning assistant",
    model=model,
    instruction="You are a travel planning assistant...",
    claude_agent_options=ClaudeAgentOptions(
        allowed_tools=["TodoWrite"],  # Claude built-in tools
    ),
    tools=[  # User-defined custom tools
        FunctionTool(get_current_date),
        GoogleSearchMCP(),
    ],
)
```

### Session Management

ClaudeAgent provides two session management modes, controlled by the `enable_session` parameter:

#### Mode 1: Claude Session (Default, `enable_session=False`)

Sessions are managed internally by Claude-Code. The framework uses `ctx.session.id` (tRPC-Agent's session ID) as Claude's session ID. The same session ID corresponds to the same `ClaudeSDKClient` instance and Claude-Code subprocess.

In this mode, Claude-Code retains the complete internal reasoning context, providing the best multi-turn conversation performance. Only the latest user message is sent each time, with history maintained by Claude-Code.

Suitable for single-node deployment or multi-node deployment with hash routing.

#### Mode 2: tRPC Session (`enable_session=True`)

Sessions are managed by tRPC-Agent. Each call creates a new `ClaudeSDKClient`, with the session ID fixed as `"default"`, meaning each call is a brand new Claude-Code session. The framework extracts complete conversation history from session events and sends it as a prompt with context.

In this mode, the framework's RedisSession and other distributed session storage options can be used, making it suitable for multi-node deployment. However, since Claude-Code does not retain internal reasoning information, multi-turn conversation performance may be inferior to Claude Session.

#### Session Lifecycle

When using Claude Session, the framework manages the lifecycle of `ClaudeSDKClient` instances through `SessionManager`, with behavior controlled by `SessionConfig`:
- `ttl`: Time before idle sessions are cleaned up, in seconds. Defaults to 600s (10 minutes) of inactivity before cleanup. Set to 0 to disable automatic cleanup.

Before each query, `SessionManager` automatically cleans up idle sessions that have exceeded the TTL, releasing the corresponding Claude-Code subprocess resources.

```python
from trpc_agent_sdk.server.agents.claude import SessionConfig

ClaudeAgent(
    ...,
    # enable_session=False, # Disabled by default, meaning Claude Session is used, and the SessionConfig below takes effect
    session_config=SessionConfig(
        ttl=600,
    ),
)
```

#### Resource Cleanup

When using ClaudeAgent, resources must be properly cleaned up before the program exits:
- `agent.destroy()`: Closes the SessionManager and all ClaudeSDKClient connections, stops the AsyncRuntime thread
- `destroy_claude_env()`: Stops the Anthropic Proxy Server subprocess

If cleanup is not performed properly (e.g., force quit with Ctrl+C), residual Claude-Code sessions may interfere with subsequent runs. In this case, manually execute `rm -rf ~/.claude*` to clean up.

### Using the Built-in Skill Capability of Claude Agent SDK
The Claude Agent SDK has a built-in skill capability. With simple configuration, you can quickly leverage the skill feature.

#### Creating Skills
- Create a `./claude/skills` directory in the project directory or the home directory (~)
    - Generally, if your skills are created in the home directory, they represent user-level skill capabilities (cross-project)
    - If your skills directory is created in the project directory, they represent project-level skill capabilities (project-specific)
- Create a skill directory under the skills directory, e.g., traver-helper
- Create a SKILL.md document in the skill directory. Refer to [skill format](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) for the format reference

Example skill.md
```
---
name: Travel Planning Assistant
description: Automatically generates a complete travel plan based on user's travel requirements (destination, time, budget, etc.), including transportation, accommodation, attractions, food, and itinerary. Use when users ask about travel plans, itinerary arrangements, travel guides, or mention traveling to a specific destination.
---

# Travel Planning Assistant

## Workflow

When a user makes a travel planning request, follow these steps to automatically generate a complete travel plan:
...
```

#### Configuring Options
```python
from claude_agent_sdk.types import ClaudeAgentOptions

agent = ClaudeAgent(
    name="travel_planner",
    description="Travel planning assistant",
    model=model,
    instruction="""
You are a professional AI assistant built on the Claude Agent SDK. Your core responsibility is to understand user requirements and invoke the appropriate Skill to complete complex tasks.
You should maintain a professional and objective attitude, and refuse to perform any harmful or non-compliant operations.
""",
    claude_agent_options=ClaudeAgentOptions(
        # cwd is where the project directory is
        cwd="your project path",
        # setting_sources is the way of claude agent to get the skills from the user and the project
        # user is the way of claude agent to get the skills from path: ~/.claude/skills
        # project is the way of claude agent to get the skills from path: cwd/.claude/skills
        setting_sources=["user", "project"], 
        # Skill Tool is the way of claude agent to use the skills,must be allowed
        allowed_tools=["Skill"], 
    ),
)
```

- Configure cwd. cwd is the project directory where the Claude Agent operates, which may contain project-level skill documents
- Configure setting_sources. Multiple data sources can be configured.
    - If `user` is set, it reads the directory `~/.claude/skills`
    - If `project` is set, it reads the directory `cwd_path/.claude/skills`
    - Multiple data sources can be set
- Configure tools. `Skill` must be configured as one of the Tool capabilities, because the Claude Agent SDK implements the skill capability through tool invocation.

For detailed usage instructions, see: [claude agent sdk with skills](https://platform.claude.com/docs/en/agent-sdk/skills)

#### Test Results

```
📝 User: Help me create a travel guide for Beijing

🤖 Agent: 
🔧 [Tool Call: Skill({"skill": "traver_helper"})]
📊 [Tool Result: Skill({"result": "Launching skill: traver_helper"})]

🔧 [Tool Call: mcp__travel_planner_tools__get_current_date({})]
📊 [Tool Result: mcp__travel_planner_tools__get_current_date({"result": "2025-12-22"})]
### Beijing Travel Guide (3 Days, 2 Nights)

📍 **Destination**: Beijing  
🗓️ **Recommended Travel Date**: December 24, 2025 - December 26, 2025  
⏱️ **Duration**: 3 days, 2 nights  
💰 **Budget Range**: Budget (approx. ¥1,500/person) / Comfort (approx. ¥2,500/person)  
🎯 **Itinerary Theme**: Cultural Exploration + Food Experience  

---

### **B. Transportation Plan**

#### Round-Trip Transportation
- **High-speed Rail**: Shanghai to Beijing, approx. 4.5 hours, ticket price approx. ¥553 (second class).  
- **Flight**: Shanghai to Beijing, approx. 2 hours, ticket price approx. ¥800 (economy class).  

#### Local Transportation
- **Subway**: Beijing subway has extensive coverage, single fare ¥3-7.  
- **Bus**: Fare starting from ¥2, suitable for short trips.  
- **Taxi**: Starting fare ¥13, suitable for nighttime or group travel.  

**Transportation Tips**:  
- High-speed rail is more suitable for short trips; flights are suitable for travelers with tight schedules.  
- Download the "Beijing Subway" app in advance for convenient route queries.  

---

### **C. Accommodation Recommendations**

| Hotel Name       | Location Advantage               | Price Range (per night) | Booking Tips          |
|----------------|------------------------|------------------|-------------------|
| Home Inn (Qianmen) | Close to Tiananmen, Forbidden City       | ¥300-400        | Book 1 week in advance       |
| Beijing Hotel       | City center, convenient transportation       | ¥800-1,000       | Book 2 weeks in advance       |
| Courtyard Guesthouse     | Experience old Beijing charm         | ¥500-700        | Book 1 month in advance     |

.....
```

### ClaudeAgentOptions Configuration

You can fine-tune Claude-Code's behavior through `ClaudeAgentOptions`. Below are some commonly used configurations that can be applied as needed:

```python
from claude_agent_sdk.types import ClaudeAgentOptions

claude_agent_options = ClaudeAgentOptions(
    # Tool configuration
    allowed_tools=["Read", "Write", "Edit"],  # Allowed built-in tool list; defaults to all tools if not specified
    disallowed_tools=["Bash"],  # Disabled tool list
    
    # Permission control
    permission_mode="default",  # Permission mode: default, acceptEdits, plan, bypassPermissions
    
    # Session control
    max_turns=10,  # Maximum conversation turns
    
    # Environment configuration
    cwd="/path/to/workdir",  # Working directory, defaults to the directory of the current main method
    add_dirs=["/path/to/extra/dir"],  # Additional directories allowed for access
    env={"KEY": "VALUE"},  # Environment variables
)
```

For detailed parameter descriptions, see: [ClaudeAgentOptions Configuration](https://docs.claude.com/en/api/agent-sdk/python#claudeagentoptions).

### Model Generation Parameter Configuration

ClaudeAgent supports configuring model generation parameters (such as temperature, max tokens, etc.) through the `generate_content_config` parameter. This configuration will override the parameters in requests sent by Claude-Code.

#### Configuring in ClaudeAgent

You can specify `generate_content_config` when creating a ClaudeAgent:

```python
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.server.agents.claude import ClaudeAgent

# Create generation configuration
config = GenerateContentConfig(
    temperature=0.7,        # Temperature parameter, controls randomness
    max_output_tokens=2000, # Maximum output token count
    top_p=0.9,             # Nucleus sampling parameter
    top_k=40,              # Top-k sampling parameter
)

# Specify configuration when creating the Agent
agent = ClaudeAgent(
    name="my_agent",
    model=OpenAIModel(
        model_name="deepseek-chat",
        api_key=os.environ.get("TRPC_AGENT_API_KEY", ""),
        base_url="https://api.deepseek.com/v1",
    ),
    instruction="You are a helpful assistant.",
    generate_content_config=config,  # Specify generation configuration
)
```

#### Configuring Default Model Parameters in setup_claude_env

When configuring default models in `setup_claude_env` (for handling Claude-Code's internal calls), you can also configure generation parameters for these default models.

```python
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.server.agents.claude import setup_claude_env

# Configure generate_content_config directly in OpenAIModel
# OpenAIModel has built-in support for the generate_content_config field
model = OpenAIModel(
    model_name="deepseek-chat",
    api_key=os.environ.get("TRPC_AGENT_API_KEY", ""),
    base_url="https://api.deepseek.com/v1",
    generate_content_config=GenerateContentConfig(
        temperature=0.8,
        max_output_tokens=1500,
    ),
)

# Use the configured model in setup_claude_env
# The Proxy server automatically extracts the model's generate_content_config
# This way, Claude-Code's default calls (sonnet/opus/haiku) will all use this configuration
setup_claude_env(
    proxy_host="0.0.0.0",
    proxy_port=8082,
    claude_models={"all": model}  # Configuration is automatically extracted
)
```

**Notes**:
- The `generate_content_config` field of `OpenAIModel` is **automatically extracted** in `setup_claude_env`
- The extracted configuration is stored in the Proxy server and applied to all requests mapped to that model
- When using `{"all": model}`, all three models (sonnet, opus, haiku) will use the same configuration

#### Configuration Lookup and Priority

The Proxy server looks up configuration in the following order when building requests:

**Configuration lookup order**:
1. **Configuration stored in model_configs**: Configuration automatically extracted from ClaudeAgent's `generate_content_config` or `setup_claude_env`
2. **Model instance configuration**: If not found in model_configs, the model instance's `generate_content_config` is used (fallback mechanism)
3. **Request parameters**: If the configuration is None or does not exist, the parameters from the Claude-Code request are used

**Field priority**: For the found configuration, the priority of specific fields is:
- **Configured fields take precedence**: If a field is set (non-None) in `generate_content_config`, the configured value is used
- **Request parameters as fallback**: If a field in the configuration is None, the parameter value from the Claude-Code request is used

For example:
```python
# Configuration sets temperature=0.7, but top_p is not set (None)
config = GenerateContentConfig(temperature=0.7, top_p=None)

# Claude-Code request: temperature=0.5, top_p=0.9
# Final values used:
#   temperature=0.7  (configured value is used, not overridden by Claude-Code request parameters)
#   top_p=0.9        (configuration is None, Claude-Code request parameter is used)
```

**Configuration source priority**:
- Explicit ClaudeAgent configuration > OpenAIModel configuration > Claude request parameters

#### Notes

- Commonly configurable fields include:
  - `temperature`: Temperature parameter (0.0-1.0)
  - `max_output_tokens`: Maximum output token count
  - `top_p`: Nucleus sampling parameter
  - `top_k`: Top-k sampling parameter
  - `stop_sequences`: List of stop sequences

## Complete ClaudeAgent Examples

- Simple weather query Agent example: [examples/claude_agent/run_agent.py](../../../examples/claude_agent/run_agent.py)
- Code generation Agent example: [examples/claude_agent_with_code_writer/run_agent.py](../../../examples/claude_agent_with_code_writer/run_agent.py)
- Travel planning Agent (multi-turn conversation) example: [examples/claude_agent_with_travel_planner/run_agent.py](../../../examples/claude_agent_with_travel_planner/run_agent.py)
- Travel planning Agent (Skill) example: [examples/claude_agent_with_skills/run_agent.py](../../../examples/claude_agent_with_skills/run_agent.py)
- Streaming tool Agent example: [examples/claude_agent_with_streaming_tool/run_agent.py](../../../examples/claude_agent_with_streaming_tool/run_agent.py)
- Cancel execution Agent example: [examples/claude_agent_with_cancel/run_agent.py](../../../examples/claude_agent_with_cancel/run_agent.py)

## FAQ
### Historical Sessions Interfering with Current Session
- Cause: During testing, exiting with Ctrl+C or not following the framework's resource cleanup logic to clean up resources. Since Claude-Code historical sessions were not properly closed, they interfere with the current session.
- Solution: Execute the command: `rm -rf ~/.claude*` to manually clean up sessions.
