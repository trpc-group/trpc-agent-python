# LLM Agent

LlmAgent encapsulates a general-purpose AI Agent implementation that uses an LLM as its brain, interacts with external systems through tool calls, and can automate complex task processing.

Unlike Agents that follow a fixed workflow, LlmAgent dynamically understands instructions and context through the LLM, autonomously deciding execution steps, tool calls, or whether to delegate to other Agents. For example, in a RAG scenario, the typical flow is to first retrieve documents and then generate a response based on them, whereas LlmAgent may recognize that the user's question is unrelated to the knowledge base and directly return a reply like "question is not relevant" without going through the RAG pipeline.

To create an LlmAgent, you need to configure the Agent's information and the tools it uses.

## Configuring Agent Basic Information

As shown below, in trpc_agent, an Agent is identified by the following properties:
- `name` (required): The name of the Agent, used to uniquely identify an Agent;
- `description` (optional): The description of the Agent, used in multi-Agent scenarios to provide its identity information to other Agents;
- `model` (required): The brain of the Agent; different scenarios (conversation/code generation/complex problem solving, etc.) require different types of models;

```python
LlmAgent(
    name="weather_agent",
    description="A helpful assistant for query weather",
    model="deepseek-chat",
    instruction="...", # Will be introduced in the next section
)
```

Before running the examples, you need to set the following environment variables (or configure them via a `.env` file):

```bash
export TRPC_AGENT_API_KEY="your-api-key"
export TRPC_AGENT_BASE_URL="your-base-url"
export TRPC_AGENT_MODEL_NAME="your-model-name"
```

For more model configurations supported by tRPC-Agent and how to instantiate and pass parameters for different models, please refer to the [Model Invocation](./model.md) documentation.

## Configuring Agent Instructions (instruction)

The `instruction` parameter is the most critical configuration for shaping the behavior of an `LlmAgent`. It is a string (or a function that returns a string) used to tell the Agent:

* Its core task or objective
* Its personality or role definition (e.g., "You are a friendly assistant", "You are a professional technical consultant")
* Behavioral constraints (e.g., "Only answer questions about X", "Never reveal Y")
* How and when to use its tools. You should explain the purpose of each tool and under what circumstances they should be called
* Expected output format (e.g., "Reply in JSON format", "Provide a bulleted list")

**Tips for effective instructions:**

* **Be clear and specific**: Avoid ambiguity. Clearly state the expected actions and outcomes
* **Use Markdown**: Improve readability of complex instructions with headings, lists, etc.
* **Provide examples (Few-Shot)**: For complex tasks or specific output formats, include examples directly in the instructions
* **Guide tool usage**: Don't just list tools; explain _when_ and _why_ the Agent should use them

**State variables (placeholder variables)**:

State variables `{var}` can be used in `instruction` to inject session state

* The instruction string is a template where you can use `{var}` syntax to insert dynamic values, injecting session state
* `{var}` inserts the value of the state variable named var from the session state; if the state variable does not exist, trpc_agent will ignore it
* `{var?}` is an optional placeholder; if not present, it is replaced with an empty string

```python
# Example: Adding instructions
LlmAgent(
        name="weather_agent",
        description="A professional weather query assistant that can provide real-time weather and forecast information.",
        model="deepseek-chat",
        # Using state variables for template substitution - demonstrating {var} syntax
        instruction="""
        You are a professional weather query assistant, providing services for {user_name}.

        **Current User Information:**
        - Username: {user_name}
        - City: {user_city}

        **Your Tasks:**
        - Understand the user's weather query requirements
        - Use appropriate tools to obtain weather information
        - Provide clear, useful weather information and suggestions

        **Available Tools:**
        1. `get_weather`: Get current weather information
        2. `get_weather_forecast`: Get multi-day weather forecast

        **Tool Usage Guide:**
        - When the user asks about current weather, use `get_weather`
        - When the user asks about weather for the coming days, use `get_weather_forecast`
        - If the query is ambiguous, you may use both tools simultaneously

        **Response Format:**
        - Provide accurate weather information
        - Give reasonable travel or clothing suggestions based on weather conditions
        - Maintain a friendly, professional tone
        - If the user does not specify a city, prioritize querying weather for {user_city}

        **Restrictions:**
        - Only answer weather-related questions
        - If asked about other topics, politely redirect to weather-related topics
        """,
)
    # tools will be added in the next section
```

LlmAgent can also be configured with output_key to save the Agent's output to a state variable for use in templates (typically used in cross-Agent interaction scenarios), as shown below:

```python
LlmAgent(
    name="weather_agent",
    description="A helpful assistant for query weather",
    model="deepseek-chat",
    instruction="...",
    output_key="weather_info",
)
```

## Configuring Agent Tools (tools)

Tools are how the Agent interacts with the external world. They can be API calls, database queries, file operations, or any operation that can be represented as a Python function. Currently, multiple tool types are supported:

- Function: Local function calls, supporting function parameters (string, integer, float, list, dict, boolean, pydantic.BaseModel)
- AgentTool: Allows wrapping an Agent as a Tool, enabling the output of one Agent to be used as the input of another Agent
- McpTool: A mechanism for integrating external MCP server tools. Through the MCP protocol, an Agent can invoke tools provided by other processes

For more tools, see: [tools](./tool.md)

```python
from trpc_agent_sdk.tools import FunctionTool

# Define the weather retrieval tool function
def get_weather_report(city: str) -> dict:
    """Get weather information for the specified city"""
    # Simulate weather API call
    weather_data = {
        "北京": {
            "temperature": "25°C",
            "condition": "Sunny",
            "humidity": "60%"
        },
        "上海": {
            "temperature": "28°C",
            "condition": "Cloudy",
            "humidity": "70%"
        },
        "广州": {
            "temperature": "32°C",
            "condition": "Thunderstorm",
            "humidity": "85%"
        },
    }
    return weather_data.get(city, {"temperature": "Unknown", "condition": "Data not available", "humidity": "Unknown"})


def get_weather_forecast(city: str, days: int = 3) -> list:
    """Get multi-day weather forecast for the specified city"""
    # Simulate forecast data
    return [
        {
            "date": "2024-01-01",
            "temperature": "25°C",
            "condition": "Sunny"
        },
        {
            "date": "2024-01-02",
            "temperature": "23°C",
            "condition": "Cloudy"
        },
        {
            "date": "2024-01-03",
            "temperature": "20°C",
            "condition": "Light rain"
        },
    ][:days]


def create_agent():
    """Create a weather query Agent to demonstrate various LLM Agent capabilities."""

    # Create tools
    weather_tool = FunctionTool(get_weather_report)
    forecast_tool = FunctionTool(get_weather_forecast)

    return LlmAgent(
        name="weather_agent",
        description="A professional weather query assistant that can provide real-time weather and forecast information.",
        model="deepseek-chat",
        instruction=INSTRUCTION,  # INSTRUCTION is the same as in the previous section
        tools=[weather_tool, forecast_tool],
        # Configure generation parameters
        generate_content_config=GenerateContentConfig(
            temperature=0.3,  # Reduce randomness for more deterministic responses
            top_p=0.9,
            max_output_tokens=1500,
        ),
        # Enable Planner to enhance reasoning capabilities (commented out by default). Uncomment the following line to give the model reasoning capabilities, allowing it to reason before generating responses
        # planner=PlanReActPlanner(),
    )

```

**Complete example:**
- [examples/llmagent/run_agent.py](../../../examples/llmagent/run_agent.py) - Basic weather query Agent example

## Session Management

The current LLM Agent can manage the visibility of messages generated by other Agents and historical session messages based on different scenarios when needed. This can be configured through related options to control what content is passed to the model during interaction. Below are some session management strategies:

### Using Preset Session Management Strategies

LlmAgent provides multiple parameters to control the visibility of session history, helping you optimize Agent context management across different scenarios:

- `max_history_messages` and `message_timeline_filter_mode` are used to control the Agent's visibility of the complete session history
- `message_branch_filter_mode` is used in multi-Agent scenarios to control one Agent's visibility of messages from other Agents

#### max_history_messages

The `max_history_messages` parameter limits the number of historical messages passed to the model, helping control token usage in long conversation scenarios:

```python
from trpc_agent_sdk.agents import LlmAgent

agent = LlmAgent(
    name="history_demo",
    description="Agent demonstrating history control",
    ...,
    max_history_messages=max_history_messages, # Only keep the most recent max_history_messages rounds of conversation
)
```

**Parameter description:**
- `max_history_messages=0` (default): No limit on the number of historical messages, includes all filtered messages
- `max_history_messages=N` (N > 0): Only includes the most recent N rounds of messages (applied after other filters)

**Use cases:**
- Controlling token usage in long conversation scenarios
- Scenarios where the Agent needs to focus only on recent conversation content
- Preventing performance issues caused by overly long context

**Notes:**
- This strategy is applied **after** `message_timeline_filter_mode` and `message_branch_filter_mode` filtering
- Only keeps the most recent N messages

#### message_timeline_filter_mode

The `message_timeline_filter_mode` parameter controls the visibility of historical messages across multiple conversation rounds:

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.agents import TimelineFilterMode

agent = LlmAgent(
    name="timeline_demo",
    description="Agent demonstrating timeline filtering",
    ...,
    message_timeline_filter_mode=timeline_mode,
)
```

**Available values:**
- `TimelineFilterMode.ALL` (default): Includes messages from all conversation rounds
- `TimelineFilterMode.INVOCATION`: Only includes messages generated during the current invocation (`runner.run_async()`)

**Use cases:**
- `ALL`: When the Agent needs to remember the complete conversation history
- `INVOCATION`: When the Agent needs to only process the current request, ignoring historical context

#### message_branch_filter_mode

In multi-Agent scenarios, the `message_branch_filter_mode` parameter controls the current Agent's visibility of messages from other Agents. Each Agent has a unique branch identifier during execution (e.g., `CustomerService.TechnicalSupport.DatabaseExpert`). Through branch filtering, you can precisely control the visible scope of messages. For example, consider the following four Agents:

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.agents import BranchFilterMode

# Database expert Agent - handles database-related issues
database_expert = LlmAgent(
    name="DatabaseExpert",
    description="Database expert, diagnoses and resolves database issues",
    instruction="You are a database expert, specializing in database performance and troubleshooting",
    message_branch_filter_mode=BranchFilterMode.PREFIX,  # Only sees related hierarchy
)

# Technical support Agent - handles technical issues, can call database expert
technical_support = LlmAgent(
    name="TechnicalSupport",
    instruction="You are a technical support specialist, handling technical issues",
    message_branch_filter_mode=BranchFilterMode.PREFIX,  # Only sees related hierarchy
    sub_agents=[database_expert],
)

# Billing support Agent - fully isolated, only sees its own messages
billing_support = LlmAgent(
    name="BillingSupport",
    instruction="You are a billing support specialist, handling billing issues",
    message_branch_filter_mode=BranchFilterMode.EXACT,  # Fully isolated
)

# Customer service Agent - does not need to be aware of other Agents' history, only cares about where the current request is dispatched
customer_service = LlmAgent(
    name="CustomerService",
    instruction="You are a customer service coordinator, routing user requests to the appropriate department",
    message_branch_filter_mode=BranchFilterMode.EXACT,  # Fully isolated
    sub_agents=[technical_support, billing_support],
)
```

**Available values:**

1. **`BranchFilterMode.ALL` (default)**: Includes messages from all Agents
   - Use case: When the Agent needs to interact with the model and synchronize all valid content messages generated by all Agents
   - Example: Scenarios requiring cross-department information sharing

2. **`BranchFilterMode.PREFIX`**: Prefix matching, includes messages from related hierarchy levels
   - Use case: When you want to pass messages generated by the current Agent and related upstream/downstream Agents
   - Example: The technical support Agent (branch: `CustomerService.TechnicalSupport`) can see:
     - Messages from parent Agent `CustomerService`
     - Its own `TechnicalSupport` messages
     - Messages from child Agent `DatabaseExpert`
     - But **cannot** see messages from sibling Agent `BillingSupport`

3. **`BranchFilterMode.EXACT`**: Exact matching, only includes messages from the current Agent
   - Use case: When the Agent needs to interact with the model but only uses its own generated messages, achieving full isolation
   - Example: The customer service coordinator only needs to see forwarded messages, not messages from other Agents

**Complete examples:**
- [examples/llmagent_with_max_history_messages/run_agent.py](../../../examples/llmagent_with_max_history_messages/run_agent.py) - max_history_messages example
- [examples/llmagent_with_timeline_filtering/run_agent.py](../../../examples/llmagent_with_timeline_filtering/run_agent.py) - message_timeline_filter_mode example  
- [examples/llmagent_with_branch_filtering/run_agent.py](../../../examples/llmagent_with_branch_filtering/run_agent.py) - message_branch_filter_mode example

### Setting Historical Session Content

Users may want to set historical session content into the agent service, as follows:

Construct user history records:
```python
from trpc_agent_sdk.sessions import HistoryRecord

def make_user_history_record() -> HistoryRecord:
    """Construct user history records, simulating the user's previous conversation history"""
    record: dict[str, str] = {
        "What's your name?": "My name is Alice",
        "what is the weather like in paris?": "The weather in Paris is sunny ...",
        "Do you remember my name?": "It seems I don't have your name stored ...",
    }

    history_record = HistoryRecord()
    for query, answer in record.items():
        history_record.add_record(query, answer)
    return history_record
```

Write prompts to instruct the Agent to prioritize finding answers from historical sessions:
```python
INSTRUCTION = """You are a Q&A assistant.
**Your Tasks:**
- Understand the question and provide a friendly answer
- If relevant data can be found in the historical session, prioritize searching from the historical session to reduce LLM tool calls; if not found in the historical session, then query using tools
"""
```

Inject historical records along with the user's query at runtime:
```python
from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.types import Content, Part

for query in demo_queries:
    # Get the history record object and build matching context content based on the current query
    history_record = make_user_history_record()
    history_content = history_record.build_content(query)
    user_content = Content(parts=[Part.from_text(text=query)])

    # Enable session history saving so that multi-turn conversations can accumulate context
    run_config = RunConfig(save_history_enabled=True)
    # new_message takes a [history_content, user_content] list,
    # injecting both historical records and the user's current query into the Agent's input
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=[history_content, user_content],
        run_config=run_config,
    ):
        ...
```

**Complete example:**
- [examples/llmagent_with_user_history/run_agent.py](../../../examples/llmagent_with_user_history/run_agent.py) - Setting historical session content example

## Advanced Configuration and Control

### GenerateContentConfig

Used to adjust LLM generation behavior, such as temperature, top-p, and other parameters:

```python
from trpc_agent_sdk.types import GenerateContentConfig

weather_agent = LlmAgent(
    name="weather_agent",
    model="deepseek-chat",
    instruction="...",
    tools=[weather_tool],
    generate_content_config=GenerateContentConfig(
        temperature=0.1,  # Reduce randomness for more deterministic responses
        top_p=0.95,
        max_output_tokens=1000,
    )
)
```

### ToolPrompt

Sometimes, the LLM model service does not support FunctionCall capabilities, such as in fine-tuned model scenarios. To enable LLMs that do not support FunctionCall to have this capability, the framework supports injecting tool definitions into the system_prompt via `ToolPrompt`, and then parsing specific text from the LLM output to support this capability.

The usage is straightforward. As shown below, you only need to add the `add_tools_to_prompt` option to `OpenAIModel` to enable this feature.

```python
OpenAIModel(
    model_name="deepseek-chat",
    base_url="https://api.deepseek.com/v1",
    api_key=os.environ.get("API_KEY", ""),
    add_tools_to_prompt=True,
    # The framework provides two ways to inject tool_prompt: "xml" and "json". If tool_prompt is not specified, "xml" is used by default
    # tool_prompt="xml",
),
```

Note that the framework provides `tool_prompt` to let users choose the format for converting tool definitions to text. It provides xml and json conversion formats by default, and you can switch between them by passing different strings.

In addition to accepting strings, `tool_prompt` can also accept a class that inherits from `ToolPrompt`, providing extensibility for custom tool conversion text. As shown below, this will use the framework-provided `XmlToolPrompt`:

```python
from trpc_agent_sdk.models.tool_prompt import XmlToolPrompt
OpenAIModel(
    model_name="deepseek-chat",
    base_url="https://api.deepseek.com/v1",
    api_key=os.environ.get("API_KEY", ""),
    add_tools_to_prompt=True,
    # Note: pass the type rather than an instance, because streaming parsing is chunk-based and stateful
    tool_prompt=XmlToolPrompt,
),
```

Users can implement a custom `CustomToolPrompt` for tool-to-text conversion. The purpose of each interface is shown below.

For implementation details, refer to [XmlToolPrompt](../../../trpc_agent_sdk/models/tool_prompt/_xml.py).

```python
from trpc_agent_sdk.models.tool_prompt import ToolPrompt

class CustomToolPrompt(ToolPrompt):
     @override
    def build_prompt(self, tools: List[Tool]) -> str:
        """Build a prompt string from a list of tools.

        Args:
            tools: List of Tool objects to convert to prompt text

        Returns:
            String representation of tools for inclusion in system prompt
        """
        pass

    @override
    def parse_function(self, content: str) -> List[FunctionCall]:
        """Parse function calls from complete content.

        Args:
            content: Complete content string containing function calls

        Returns:
            List of FunctionCall objects parsed from content

        Raises:
            ValueError: If content cannot be parsed as function calls
        """
        pass
```

**Complete example:**
- [examples/llmagent_with_tool_prompt/run_agent.py](../../../examples/llmagent_with_tool_prompt/run_agent.py) - ToolPrompt usage example

### PlanReActPlanner

Planner customizes the Agent's planning process. It essentially intercepts the LLM's input and output content. In the input, the Planner can inject planning-related information; in the output, the Planner can process the planning results, such as converting tool call text in the LLM output into trpc_agent's tool structures, enabling tool calls even on models that do not support them.

The framework provides PlanReActPlanner, which injects Reasoning instructions into the LLM input, enabling models that do not support Reasoning to also have this capability.

```python
from trpc_agent_sdk.planners import PlanReActPlanner

weather_agent = LlmAgent(
    name="weather_agent",
    model="deepseek-chat",
    instruction="...",
    planner=PlanReActPlanner(),  # Enable planning capability
)
```

### Enabling Thinking Mode

Many models currently support thinking mode. The framework controls thinking behavior through `BuiltInPlanner` and `ThinkingConfig`. Thinking mode allows the model to perform internal reasoning before generating the final response, improving answer quality.

Using thinking mode requires configuring the following components:

- `BuiltInPlanner`: Built-in planner that supports thinking functionality
- `ThinkingConfig`: Thinking configuration that controls the parameters of thinking behavior
  - `include_thoughts`: Whether to include the thinking process in the output, defaults to False
  - `thinking_budget`: Token budget for the thinking process, must be less than `max_output_tokens`

```python
from trpc_agent_sdk.agents.llm_agent import LlmAgent
from trpc_agent_sdk.models import LLMModel, OpenAIModel
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.planners import BuiltInPlanner
from trpc_agent_sdk.types import ThinkingConfig


def _create_model() -> LLMModel:
    """Create the model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(
        model_name=model_name,
        api_key=api_key,
        base_url=url,
        # There are two scenarios where enabling add_tools_to_prompt can improve Agent generation:
        # 1. When the thinking model does not support tool calls,
        #    you can enable the ToolPrompt framework to parse tool calls from LLM-generated text.
        # 2. When the thinking model calls tools during reasoning,
        #    if the LLM model service cannot return tool calls in JSON format, you can also enable ToolPrompt.
        #    This prompts the LLM model to output special tool call text in the body,
        #    increasing the probability of successful tool calls.
        # You can uncomment the line below to use ToolPrompt.
        # add_tools_to_prompt=True,
        )
    return model


def create_agent():
    """Create a weather query Agent demonstrating thinking mode usage."""

    return LlmAgent(
        name="weather_agent",
        description="A professional weather query assistant that can provide real-time weather and forecast information.",
        model=_create_model(),
        instruction=INSTRUCTION,
        # Note: thinking_budget must be less than max_output_tokens
        generate_content_config=GenerateContentConfig(max_output_tokens=10240, ),
        # The model must be a thinking model for this Planner to take effect; this configuration will have no effect for non-thinking models.
        planner=BuiltInPlanner(thinking_config=ThinkingConfig(
            include_thoughts=True,
            thinking_budget=2048,
        ), ),
    )

root_agent = create_agent()
```

Notes:

- `thinking_budget`: Must be less than `max_output_tokens` in `generate_content_config`
- `include_thoughts`: When set to True, users can see the model's thinking process; when set to False, only the final result is displayed
- Only models that support thinking can use this feature. Currently supported models include: deepseek-reasoner, glm-4.5-fp8, etc.
- For models that inherently have thinking capabilities (such as qwen3-next-80b-a3b-thinking), this configuration can also be used to control thinking behavior

**Complete example:**
- [examples/llmagent_with_thinking/run_agent.py](../../../examples/llmagent_with_thinking/run_agent.py) - Agent example with thinking mode enabled

### Model Creation Callback Function

In some scenarios, you may need to dynamically create model instances at runtime rather than fixing the model when initializing the Agent. For example:
- Dynamically adjusting model parameters based on runtime configuration
- Using different API keys or base URLs for different requests

The framework supports passing an async function as a model creation callback to `LlmAgent`. This callback function receives data from `RunConfig.custom_data` and returns a model instance.

***In addition to LlmAgent, both ClaudeAgent and TeamAgent support this usage.***

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel, LLMModel
from trpc_agent_sdk.configs import RunConfig

async def create_model(custom_data: dict) -> LLMModel:
    """Model creation callback function
    
    Args:
        custom_data: Data passed from RunConfig.custom_data
        
    Returns:
        LLMModel instance
    """
    print(f"📦 Model creation function received custom_data: {custom_data}")
    
    return OpenAIModel(
        model_name=model_name,
        base_url="https://api.openai.com/v1",
        api_key=os.environ.get("OPENAI_API_KEY", ""),
    )

# Pass the model creation callback when creating the Agent
weather_agent = LlmAgent(
    ...,
    model=create_model,  # Pass the model creation callback
)

# Create Runner
runner = Runner(
    app_name="weather_app",
    agent=weather_agent,
    session_service=session_service
)

# Pass custom_data through RunConfig
run_config = RunConfig(custom_data={"user_tier": "premium"})

async for event in runner.run_async(
    ...
    run_config=run_config
):
    # Process Agent output...
    pass
```

**Complete example:**
- [examples/llmagent_with_model_create_fn/run_agent.py](../../../examples/llmagent_with_model_create_fn/run_agent.py) - Model creation callback usage example

### Structured Input and Output

#### output_schema Usage

LlmAgent supports configuring structured output (output_schema). By configuring `output_schema`, you can specify the Agent's output format. It is generally necessary to specify the output structure format in the instruction.

The implementation mechanism of output_schema has two different methods depending on whether tools are used:

1. When tools are not configured for LlmAgent, it uses the LLM's [Structured Output](https://platform.openai.com/docs/guides/structured-outputs) capability (requires model service support). When the LLM supports response_format as json_schema (e.g., GPT series), there is no need to write the output format in the instruction, as the framework will auto-populate it. However, when the LLM only supports response_format as json_object (e.g., DeepSeek), users need to specify the JSON output format in the instruction.
2. When tools are configured, the LLM's Structured Output capability is not used (Structured Output cannot be used with Tools). The framework will inject a set_model_response tool into the Agent to trigger the LLM to call this tool to set the JSON output.

```python
from pydantic import BaseModel
from typing import List

class UserProfileOutput(BaseModel):
    """Output schema for user profile analysis."""
    user_name: str
    age_group: str  # "young", "adult", "senior"
    personality_traits: List[str]
    recommended_activities: List[str]
    profile_score: int  # 1-10
    summary: str

profile_agent = LlmAgent(
    name="user_profile_agent",
    model="deepseek-chat",
    instruction="...",
    output_schema=UserProfileOutput,
    output_key="user_profile",
)

# After runner execution, obtain the Agent's structured data through state
async for event in runner.run_async(...):
    pass

session = await session_service.get_session(xxx)
user_profile_json = session.state[profile_agent.output_key]
user_profile = UserProfileOutput.model_validate_json(user_profile_json)
print(user_profile)
```

#### input_schema Usage

LlmAgent also supports structured input (input_schema), which is generally used in conjunction with [AgentTool](./tool.md). AgentTool automatically validates whether the Agent's input/output conforms to the schema, as shown below:

```python
from pydantic import BaseModel
from typing import List, Optional
from trpc_agent_sdk.tools import AgentTool

class UserProfileInput(BaseModel):
    """Input schema for user profile creation."""

    name: str
    age: int
    email: str
    interests: List[str]
    location: Optional[str] = None

profile_agent = LlmAgent(
    ...,
    input_schema=UserProfileInput,
    output_schema=UserProfileOutput,
)

profile_tool = AgentTool(
    agent=profile_agent,
    skip_summarization=True,
)

main_agent = LlmAgent(
    name="main_processor",
    description="Main processing Agent that can call the user profile analysis tool",
    model="deepseek-chat",
    instruction="...",
    tools=[profile_tool],
)
```

**Complete example:**
- [examples/llmagent_with_schema/run_agent.py](../../../examples/llmagent_with_schema/run_agent.py) - Structured input/output Agent example

### Setting Parallel Tool Calls

When the Agent calls multiple tools that involve significant network latency, concurrent invocation can be used:

```python
def create_agent():
    """Create an Agent configured with hobby-related tools"""
    # ...
    return LlmAgent(
        name="hobby_toolset_agent",
        description="An assistant demonstrating hobby ToolSet usage",
        model=model,
        tools=[hobby_toolset],
        parallel_tool_calls=True,
        instruction="""
You are a virtual person who loves life. Select appropriate tools based on user interests to retrieve hobby information and provide friendly responses.
**Your Tasks:**
- If the conversation contains content related to running or sports, you must call the sports tool. If no sports parameter is provided, default to running
- If the conversation contains content related to TV, you must call the watch_tv tool. If no TV parameter is provided, default to cctv
- If the conversation contains content related to music, you must call the listen_music tool. If no music parameter is provided, default to QQ Music
""",
    )
```

Enable concurrent tool calls by setting the `parallel_tool_calls` field to True in `LlmAgent`.

**Complete example:**
- [examples/llmagent_with_parallal_tools/run_agent.py](../../../examples/llmagent_with_parallal_tools/run_agent.py) - Parallel tool calls example

### Disabling Framework Auto-injected Prompts

In some scenarios, you may want to fully control the prompt content passed to the LLM without the framework automatically injecting additional information. trpc-agent provides two configuration options to disable the framework's auto-injection behavior:

#### add_name_to_instruction

By default, the framework automatically injects the Agent's name information in multiple places:

1. **Injecting name in instruction**: Format is `"You are an agent who's name is [agent_name]."`
2. **In multi-Agent collaboration scenarios**: When one Agent's output is passed to another Agent, a `"[agent_name]: content"` prefix is added

You can disable these behaviors by setting `add_name_to_instruction` to `False`:

```python
COORDINATOR_INSTRUCTION = """You are a customer service coordinator.
Route customer requests to the appropriate department:
- Weather questions -> WeatherAssistant
- Translation requests -> TranslationAssistant
Be concise and professional."""

coordinator = LlmAgent(
    name="Coordinator",
    description="Customer service coordinator",
    ..., 
    instruction=COORDINATOR_INSTRUCTION,
    add_name_to_instruction=False,  # Disable auto-injection, "You are an agent who's name is [Coordinator]." will not be added to the instruction
)
```

#### default_transfer_message

In multi-Agent scenarios, when sub_agents are configured for an Agent, the framework automatically injects prompts related to child Agents. By setting `default_transfer_message`, you can override the default prompt injected by the framework:

```python
CUSTOM_TRANSFER_MESSAGE = """When you need help from other agents:
- Call the transfer_to_agent tool
- Choose the most suitable agent based on the user's question
Available agents:
- WeatherAssistant: handles weather queries
- TranslationAssistant: handles translation requests
"""

coordinator = LlmAgent(
    name="Coordinator",
    ...,
    description="Customer service coordinator",
    instruction=COORDINATOR_INSTRUCTION,
    default_transfer_message=CUSTOM_TRANSFER_MESSAGE,  # Use custom transfer prompt
)
```

**Note: To successfully delegate to child Agents, make sure to mention the `transfer_to_agent` tool in the prompt. The Agent can only call this tool (which the framework automatically injects when sub_agents are configured).**

This parameter has the following configurations:
- None (default): The framework will enable auto-injection
- Empty string: Will disable framework auto-injection
- Custom: Will use the user-defined string to replace the framework's auto-injected information

Complete example: [examples/llmagent_with_custom_prompt/run_agent.py](../../../examples/llmagent_with_custom_prompt/run_agent.py).

### Starting Next Conversation Round from the Last Responding Agent

By default, each round of a multi-turn conversation starts from the entry Agent. However, some scenarios require starting from the last responding Agent. For example, after delegating to an Agent, you may want to continue the conversation with that Agent for several rounds. The framework provides a `RunConfig` option to support this capability:

```python
from trpc_agent_sdk.configs import RunConfig

run_config = RunConfig(start_from_last_agent=True)
async for event in runner.run_async(...,run_config=run_config):
    ...
```

Complete example: [examples/multi_agent_start_from_last/run_agent.py](../../../examples/multi_agent_start_from_last/run_agent.py).

## Core Concepts

### InvocationContext

`InvocationContext` represents the context of a single Agent invocation, containing the services, session, user input, run configuration, and state information required for execution.

Typically, you do not need to manually create an `InvocationContext`. It is recommended to use `Runner.run_async()` directly, letting the framework automatically handle the preparation of `session`, `invocation_id`, `branch`, `run_config`, and other context. Only when you need full control over the execution flow should you construct an `InvocationContext` directly and call `agent.run_async(ctx)`.

Common use cases:

- Need full control over the invocation context
- Need to manually construct or reuse a `session`
- Testing, custom framework integration, low-level debugging scenarios

Commonly used fields in `InvocationContext`:

- `session_service`: Responsible for managing session read/write, persistence, and context assembly associated with the current invocation
- `artifact_service`: Used for saving and reading attachments, files, and other artifacts; unavailable when not configured
- `memory_service`: Used for storing and retrieving long-term memory and historical information; unavailable when not configured
- `invocation_id`: A unique identifier assigned to each invocation, facilitating tracing and troubleshooting
- `branch`: Used for isolating visible history in multi-Agent or sub-Agent scenarios, preventing parallel branches from polluting each other's context
- `agent`: Represents the Agent instance that is currently executing logic
- `agent_context`: Carries user interaction control-related context, such as interaction strategies or runtime control information
- `user_content`: Represents the user input content that triggered this invocation
- `session`: Carries the current session itself and its state data
- `end_invocation`: Can be set to `True` during callbacks or tool execution to terminate the current invocation early
- `run_config`: Stores the run configuration used for this execution, such as model, streaming output, or other runtime parameters

#### Invocation State

`InvocationContext` provides two commonly used state access methods:

- `ctx.state`: Writable state with a dictionary-style interface that automatically records incremental changes from the current invocation
- `ctx.session_state`: Read-only view for safely reading the current session state

Additional notes:

- `ctx.state`: Internally reuses the current `session.state`, but simultaneously records modifications to `event_actions.state_delta`, enabling the framework to detect and commit incremental changes
- `ctx.session_state`: Returns a read-only mapping view, suitable for use when only reading state is needed, avoiding accidental modifications to shared state

### Event

`Event` is the unified event object produced during Agent execution, and is the content unit continuously yielded by `Runner.run_async()` and `agent.run_async()`. `Event` extends `LlmResponse`, supplementing the response content with invocation chain and event metadata.

Commonly used fields include:

- `content`: The actual content payload of the event, which can be text or structured parts including tool calls, tool results, etc.
- `partial`: Commonly seen during streaming output; when `True`, it usually indicates the current event is not yet a complete result
- `error_code` / `error_message`: Used to identify whether the event is in an error state, along with the accompanying error description
- `invocation_id`: Used to associate the event back to its invocation chain; multiple events produced during a single invocation typically share the same ID
- `author`: Identifies who wrote the event, commonly used to distinguish between user messages, Agent output, and other participants
- `actions`: Carries action information associated with this event, such as state deltas, artifact changes, or other execution side effects
- `branch`: Used for multi-Agent branch isolation, helping the framework determine history visibility between different sub-chains
- `id` / `timestamp`: Used for uniquely identifying the event and recording when the event was produced, facilitating sorting, tracing, and debugging
- `visible`: When set to `False`, the event can still exist in internal flows, but `Runner` may choose not to expose it to external callers

`Event` also provides convenient methods, such as:

- `is_final_response()`: Not only checks for the absence of tool calls and tool returns, but also considers `partial`, long-running tool calls, and other conditions to comprehensively determine whether the current event can be considered a final response
- `get_function_calls()`: Extracts all `function_call` from `content.parts` for unified tool request handling
- `get_function_responses()`: Extracts all `function_response` from `content.parts` for reading tool execution results
- `get_text()`: Concatenates all text parts in the event; returns an empty string if there is no text content

### AgentABC

`Agent` is the abstract base class for all Agents, defining basic Agent properties, sub-Agent management capabilities, and the async execution entry point.

Core capabilities include:

- `name` / `description`: The Agent's name and capability description
- `sub_agents`: List of child Agents
- `find_agent()` / `find_sub_agent()`: Find Agents by name within the Agent tree
- `run_async(parent_context)`: The Agent's underlying async execution entry point

For business code, it is still generally recommended to use `Runner.run_async()` as the unified entry point; `AgentABC.run_async()` is more suitable for advanced encapsulation, custom Agent implementations, or testing scenarios.
## Other Agent Types

Now that you understand the LLM Agent provided in trpc_agent, click the links below to learn about other Agent types and how to use them:

- **[LangGraph Agent](./langgraph_agent.md)**: Learn how to use Graph to customize controllable workflows for Agents
- **[Multi Agents](./multi_agents.md)**: Master the usage and best practices of Chain, Parallel, Cycle, and Sub Agents  
- **[Custom Agent](./custom_agent.md)**: Learn how to implement fully custom Agent/Multi-Agent logic

Choose the Agent type that best fits your needs and start building powerful AI applications!
