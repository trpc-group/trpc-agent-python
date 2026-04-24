# Tools

Tool is the core mechanism for extending Agent capabilities in trpc_agent. With tools, Agents can invoke custom functions, integrate with third-party services, perform data processing, and more, breaking beyond the boundaries of pure text reasoning to deeply interact with external systems.

### Core Features

- **Multiple tool types**: Function Tools, MCP standard tools, Agent Tools, File Tools, and more
- **Streaming responses**: Real-time streaming (Streaming Tools) and standard response modes
- **Parallel execution**: Tool calls can run in parallel for better performance (`parallel_tool_calls=True`)
- **MCP protocol**: STDIO, SSE, and Streamable HTTP transports
- **Session management**: Automatic session health checks and reconnection for MCP toolsets

## How Agents Use Tools

Agents dynamically use tools through the following steps:
1. **Reasoning**: The LLM analyzes instructions and conversation history
2. **Selection**: Selects the appropriate tool based on available tools and their descriptions
3. **Invocation**: Generates the required parameters and triggers tool execution
4. **Observation**: Receives the results returned by the tool
5. **Integration**: Incorporates the tool output into subsequent reasoning


## Tool Types

| Type | Use Case | Development Approach | Typical Applications |
|------|----------|----------|----------|
| [Function Tools](#function-tools) | Custom business logic, data processing, API calls | Write Python async functions directly | Weather queries, database operations, file processing, calculation tools |
| [MCP Tools](#mcp-tools) | Third-party tool integration, cross-process tool invocation, microservice architecture | Connect to existing MCP servers or create new MCP services | External API services, database tools, file system operations |
| [Tool Set](#toolset) | Business scenarios that need a cohesive set of related tools (same category) | Combine a category of tools into one ToolSet | Access all tools exposed by an MCP server |
| [Agent Tools](#agent-tools) | Wrap an Agent as a tool for other Agents to invoke | Wrap Agent using AgentTool | Translation tools, content processing |
| [File Tools](#file-tools) | File operations and text processing | Use FileToolSet or individual tools | Read/write files, search, command execution |
| [LangChain Tools](#langchain-tools) | Reuse tools from the LangChain ecosystem | Wrap as async functions and package as FunctionTool | Web search (Tavily), etc. |
| [Streaming Tools](#streaming-tools) | Real-time preview of long text generation | Use StreamingFunctionTool | Code generation, document writing |
| [Agent Code Executor](./code_executor.md) | Automatic code generation and execution scenarios, data processing scenarios | Configure CodeExecutor | Automatic API invocation, tabular data processing |
---

## Function Tools

Function Tool is the most fundamental and commonly used tool type in the trpc_agent framework, allowing developers to quickly convert Python functions into tools that Agents can invoke. When the built-in tools provided by the framework cannot meet specific requirements, developers can create customized functionality through Function Tools.

trpc_agent provides multiple ways to create Function Tools, adapting to different development scenarios and complexity requirements.

### Using Tools

#### 1. Direct Function Wrapping

The simplest approach is to directly wrap a single Python function using the `FunctionTool` class:

```python
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.agents import LlmAgent

async def get_weather(city: str) -> dict:
    """Get weather information for a specified city

    Args:
        city: City name, e.g., "Beijing", "Shanghai"

    Returns:
        A dictionary containing weather information, including temperature, condition, and humidity
    """
    weather_data = {
        "Beijing": {
            "temperature": "15°C",
            "condition": "Sunny",
            "humidity": "45%",
        },
        "Shanghai": {
            "temperature": "18°C",
            "condition": "Cloudy",
            "humidity": "65%",
        },
        "Shenzhen": {
            "temperature": "25°C",
            "condition": "Light Rain",
            "humidity": "80%",
        },
    }

    if city in weather_data:
        return {
            "status": "success",
            "city": city,
            **weather_data[city],
            "last_updated": "2024-01-01T12:00:00Z",
        }
    return {
        "status": "error",
        "error_message": f"Weather data for {city} is not available",
        "supported_cities": list(weather_data.keys()),
    }

# Create the tool
weather_tool = FunctionTool(get_weather)

# Use in an Agent
agent = LlmAgent(
    name="function_tool_demo_agent",
    model="deepseek-chat",
    description="An assistant demonstrating FunctionTool usage",
    instruction="You are a weather query assistant...",
    tools=[weather_tool],
)
```

#### 2. Registration via Decorator

The `@register_tool` decorator registers a function to the global tool registry, and the tool can be retrieved using the `get_tool` function:

```python
from trpc_agent_sdk.tools import register_tool, get_tool
from trpc_agent_sdk.context import InvocationContext

@register_tool("get_session_info")
async def get_session_info(tool_context: InvocationContext) -> dict:
    """Get current session information

    The tool_context parameter is automatically injected by the framework and does not need to be provided at invocation time.

    Returns:
        Basic information about the current session
    """
    session = tool_context.session
    return {
        "status": "success",
        "session_id": session.id,
        "user_id": session.user_id,
        "app_name": session.app_name,
    }

# Retrieve the tool from the registry
session_tool = get_tool("get_session_info")
```

### Parameter Handling

#### Parameter Types

Function Tool supports **JSON-serializable types** as parameters. The framework automatically generates a JSON Schema based on the type annotations in the function signature, enabling the LLM to understand the parameter structure. The supported types are as follows:

| Python Type | JSON Schema Type | Description |
|---|---|---|
| `str` | `string` | String |
| `int` | `integer` | Integer |
| `float` | `number` | Floating-point number |
| `bool` | `boolean` | Boolean |
| `list` | `array` | List |
| `dict` | `object` | Dictionary |
| `pydantic.BaseModel` | `object` (nested structure) | Supports nested models; the framework recursively parses fields and their `description` |

> **Note**: It is recommended to avoid setting default values for parameters, as LLMs currently do not understand default parameters, which may cause the parameter to be consistently ignored or filled with incorrect values.

**Basic type examples:**

```python
async def calculate(operation: str, a: float, b: float) -> float:
    """Perform mathematical calculations

    Args:
        operation: Operation type (add, subtract, multiply, divide)
        a: First number
        b: Second number

    Returns:
        Calculation result
    """
    operations = {
        "add": lambda x, y: x + y,
        "subtract": lambda x, y: x - y,
        "multiply": lambda x, y: x * y,
        "divide": lambda x, y: x / y if y != 0 else float("inf"),
    }

    if operation not in operations:
        raise ValueError(f"Unsupported operation: {operation}")

    return operations[operation](a, b)


# Pydantic model types (with nesting support)

class City(BaseModel):
    """City information"""
    city: str = Field(..., description="City name")


class Address(BaseModel):
    """Address information for postal code lookup"""
    city: City = Field(..., description="City information")
    province: str = Field(..., description="Province name")


class PostalCodeInfo(BaseModel):
    """Postal code lookup result"""
    city: str = Field(..., description="City name")
    postal_code: str = Field(..., description="Postal code")


def get_postal_code(addr: Address) -> PostalCodeInfo:
    """Get the postal code for a specified address

    Args:
        addr: An Address object containing city and province

    Returns:
        A PostalCodeInfo object containing the postal code
    """
    cities = {
        "Guangdong": {
            "Shenzhen": "518000",
            "Guangzhou": "518001",
            "Zhuhai": "518002",
        },
        "Jiangsu": {
            "Nanjing": "320000",
            "Suzhou": "320001",
        },
    }
    postal_code = cities.get(addr.province, {}).get(addr.city.city, "Unknown")
    return PostalCodeInfo(city=addr.city.city, postal_code=postal_code)
```

#### Framework Context Parameters

Function Tool supports injecting the `InvocationContext` context parameter for accessing session state, user information, and more:

```python
from trpc_agent_sdk.context import InvocationContext

@register_tool("get_session_info")
async def get_session_info(tool_context: InvocationContext) -> dict:
    """Get current session information

    The tool_context parameter is automatically injected by the framework and does not need to be provided at invocation time.

    Returns:
        Basic information about the current session
    """
    session = tool_context.session
    return {
        "status": "success",
        "session_id": session.id,
        "user_id": session.user_id,
        "app_name": session.app_name,
    }
```

### Return Value Handling

#### Recommended Return Types

The preferred return type for Function Tool is **dict (dictionary)**, which provides structured information to the LLM. If the function returns another type, the framework automatically wraps it into a dictionary with the key name "result".

```python
# Recommended: Return a dictionary
async def good_example(query: str) -> dict:
    """Recommended return approach"""
    return {
        "status": "success",
        "result": f"Processed query: {query}",
        "timestamp": "2024-01-01T12:00:00Z"
    }

# Acceptable: Return other types (will be automatically wrapped)
async def ok_example(query: str) -> str:
    """Will be wrapped as {"result": "return value"}"""
    return f"Processed query: {query}"
```

#### Status Indication

Including a "status" field in the return value helps the LLM understand the operation result:

```python
async def process_document(content: str) -> dict:
    """Process document content"""
    try:
        # Processing logic
        processed = content.upper()
        return {
            "status": "success",
            "processed_content": processed,
            "word_count": len(content.split())
        }
    except Exception as e:
        return {
            "status": "error",
            "error_message": f"Processing failed: {str(e)}"
        }
```

### Docstring and Comments

The function's docstring is sent to the LLM as the tool description, so writing clear and detailed docstrings is crucial. Here is a good example:

```python
async def analyze_sentiment(text: str, language: str = "zh") -> dict:
    """Analyze the sentiment of a text

    This tool can analyze the sentiment of Chinese or English text, returning sentiment classification and confidence.

    Args:
        text: The text content to analyze, supports Chinese and English
        language: Text language, supports "zh" (Chinese) or "en" (English)

    Returns:
        A dictionary containing sentiment analysis results, including:
        - sentiment: Sentiment classification (positive/negative/neutral)
        - confidence: Confidence score (0.0-1.0)
        - details: Detailed analysis information

    Example:
        Analysis result example:
        {
            "sentiment": "positive",
            "confidence": 0.85,
            "details": "The text expresses positive sentiment"
        }
    """
    # Implement analysis logic
    return {
        "sentiment": "positive",
        "confidence": 0.85,
        "details": f"Analyzed {language} text: {text[:50]}..."
    }
```

### Function Tools Best Practices

#### 1. Tool Design Principles

- **Single Responsibility**: Each tool should do one thing only, keeping functionality focused
- **Clear Naming**: Use descriptive function names, e.g., `get_weather` instead of `weather`
- **Detailed Documentation**: Provide complete docstrings, including parameter descriptions and return value examples

#### 2. Parameter Design

- **Explicit Types**: Add type annotations for all parameters; the framework generates JSON Schema from these for LLM comprehension
- **Semantic Naming**: Use complete, readable parameter names (e.g., `max_results` instead of `num`) to help the LLM fill them correctly
- **Avoid Default Values**: LLMs currently do not understand default parameters, which may cause parameters to be ignored or filled incorrectly
- **Sufficient Descriptions**: Provide descriptions and value range examples for each parameter in the docstring's Args section

```python
# Good parameter design example
async def search_products(query: str, category: str, max_results: int) -> dict:
    """Search for products

    Args:
        query: Search keywords, e.g., "Bluetooth headphones"
        category: Product category, e.g., "electronics", "books", etc.
        max_results: Maximum number of results to return, recommended between 1-20
    """
    pass

# Design to avoid
def search(q, cat=None, num=10):  # Unclear parameter names, has default values
    pass
```

#### 3. Error Handling

- **Do Not Throw Uncaught Exceptions**: Unhandled exceptions will interrupt the Agent execution flow; catch them within the function and return structured error information
- **Return Clear Error Status**: Use the `status` field to distinguish between success and failure, making it easier for the LLM to determine the next action
- **Provide Actionable Error Messages**: Error descriptions should be specific, helping the LLM understand the failure reason and make reasonable subsequent decisions

```python
async def divide_numbers(a: float, b: float) -> dict:
    """Calculate the division of two numbers"""
    # Handle division by zero edge case first, return a clear error code for LLM comprehension
    if b == 0:
        return {
            "status": "error",
            "error_message": "Divisor cannot be zero",
            "error_code": "DIVISION_BY_ZERO"
        }

    try:
        result = a / b
        return {
            "status": "success",
            "result": result,
            "operation": f"{a} ÷ {b} = {result}"
        }
    except Exception as e:
        # Catch other potential exceptions (e.g., overflow) and return uniformly as error status
        return {
            "status": "error",
            "error_message": f"Calculation error: {str(e)}"
        }
```


### Function Tools Complete Example

For a complete Function Tool usage example, see: [examples/function_tools/run_agent.py](../../../examples/function_tools/run_agent.py)



## MCP Tools

**MCP Tools** (Model Context Protocol Tools) are how trpc_agent integrates tools from external MCP servers. Through the MCP protocol, Agents can invoke tools provided by other processes.

In trpc_agent, the primary integration pattern is: **Agent as MCP client**, connecting to and using tools provided by external MCP servers via `MCPToolset`.

### Using MCPToolset

`MCPToolset` is the core class in trpc_agent for integrating MCP tools. It can connect to an MCP server, automatically discover available tools, and convert them into tools that Agents can use. Its usage is straightforward, as shown below:

```python
import os
import sys

from trpc_agent_sdk.tools import MCPToolset
from trpc_agent_sdk.tools import McpStdioServerParameters
from trpc_agent_sdk.tools import StdioConnectionParams
from trpc_agent_sdk.agents import LlmAgent


class StdioMCPToolset(MCPToolset):
    """Stdio-based MCP toolset that automatically starts an MCP server subprocess."""

    def __init__(self):
        super().__init__()
        env = os.environ.copy()
        svr_file = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "mcp_server.py")
        )
        stdio_server_params = McpStdioServerParameters(
            command=f"python{sys.version_info.major}.{sys.version_info.minor}",
            args=[svr_file],
            env=env,
        )
        self._connection_params = StdioConnectionParams(
            server_params=stdio_server_params,
            timeout=5,
        )
        # Uncomment to expose only specified tools instead of all tools:
        # self._tool_filter = ["get_weather", "calculate"]


mcp_toolset = StdioMCPToolset()

agent = LlmAgent(
    name="mcp_assistant",
    description="An assistant that uses MCP tools for weather and calculation",
    model=_create_model(),
    instruction=INSTRUCTION,
    tools=[mcp_toolset],
)
```

### Creating an MCP Server

The trpc_agent framework itself is only responsible for the MCP client side (connecting and invoking tools via `MCPToolset`). The MCP server is an independent process that exposes tool capabilities. The following example shows how to create an MCP server quickly with `FastMCP` from the third-party `mcp` library, so Agents in trpc_agent can connect to it.

```python
from mcp.server import FastMCP

app = FastMCP("simple-tools")


@app.tool()
async def get_weather(location: str) -> str:
    """Get weather information for a specified location

    Args:
        location: Location name

    Returns:
        Weather information string
    """
    weather_info = {
        "Beijing": "Sunny, 15°C, humidity 45%",
        "Shanghai": "Cloudy, 18°C, humidity 65%",
        "Shenzhen": "Light rain, 25°C, humidity 80%",
    }
    return weather_info.get(location, f"Weather data for {location} is not available")


@app.tool()
async def calculate(operation: str, a: float, b: float) -> float:
    """Perform basic mathematical operations

    Args:
        operation: Operation type (add, subtract, multiply, divide)
        a: First number
        b: Second number

    Returns:
        Calculation result
    """
    operations = {
        "add": lambda x, y: x + y,
        "subtract": lambda x, y: x - y,
        "multiply": lambda x, y: x * y,
        "divide": lambda x, y: x / y if y != 0 else float("inf"),
    }
    if operation not in operations:
        raise ValueError(f"Unsupported operation: {operation}")
    return operations[operation](a, b)


if __name__ == "__main__":
    # Uncomment one of the following lines to select the transport mode:
    app.run(transport="stdio")
    # app.run(transport="sse")
    # app.run(transport="streamable-http")
```

### Connection Parameter Types Explained

#### StdioConnectionParams for stdio Type

Used to connect to an MCP server running in a local process:

```python
import os
import sys

from trpc_agent_sdk.tools import MCPToolset
from trpc_agent_sdk.tools import McpStdioServerParameters
from trpc_agent_sdk.tools import StdioConnectionParams


class StdioMCPToolset(MCPToolset):
    """Stdio-based MCP toolset that automatically starts an MCP server subprocess."""

    def __init__(self):
        super().__init__()
        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = (
            f"/usr/local/Python{sys.version_info.major}{sys.version_info.minor}/lib/:"
            + env.get("LD_LIBRARY_PATH", "")
        )
        svr_file = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "mcp_server.py")
        )
        # Configure stdio transport: start the MCP server as a subprocess
        stdio_server_params = McpStdioServerParameters(
            command=f"python{sys.version_info.major}.{sys.version_info.minor}",  # Launch command
            args=[svr_file],  # Command arguments
            env=env,  # Environment variables (optional)
        )
        self._connection_params = StdioConnectionParams(
            server_params=stdio_server_params,
            timeout=5,  # Optional timeout, default is 5s
        )
        # Uncomment to expose only specified tools instead of all tools:
        # self._tool_filter = ["get_weather", "calculate"]
```

Notes:
- Here, parameters of type `McpStdioServerParameters` are passed, and the framework internally converts them to the `StdioConnectionParams` type. If the user directly uses the `StdioConnectionParams` type, the following approach should be used instead:
```python
        stdio_server_params = McpStdioServerParameters(
            command=f"python{sys.version_info.major}.{sys.version_info.minor}",
            args=[svr_file],
            env=env,
        )
        self._connection_params = StdioConnectionParams(
            server_params=stdio_server_params,
            timeout=5,  # Optional timeout, default is 5s
        )
```
- This uses `stdio` mode, which automatically starts the `mcp_server.py` program without requiring the user to start it manually

#### SseConnectionParams for sse Type

Used to connect to a remote HTTP MCP server:

```python
from trpc_agent_sdk.tools import MCPToolset
from trpc_agent_sdk.tools import SseConnectionParams


class SseMCPToolset(MCPToolset):
    """SSE-based MCP toolset that connects to a remote MCP server via Server-Sent Events."""

    def __init__(self):
        super().__init__()
        self._connection_params = SseConnectionParams(
            # Required, replace the url with the actual address
            url="http://localhost:8000/sse",
            # Optional HTTP headers
            headers={"Authorization": "Bearer token"},
            # Optional timeout in seconds
            timeout=5,
            # Optional SSE read timeout in seconds
            sse_read_timeout=60 * 5,
        )
```
Notes:
- This uses the `sse` protocol; if testing, you need to manually start the `mcp_server.py` program
- When starting, you need to modify as follows:
```python
if __name__ == "__main__":
    # app.run(transport="stdio")
    app.run(transport="sse")
    # app.run(transport="streamable-http")
```

#### StreamableHTTPConnectionParams for streamable-http Type

Used to connect to a remote HTTP MCP server:

```python
from trpc_agent_sdk.tools import MCPToolset
from trpc_agent_sdk.tools import StreamableHTTPConnectionParams


class StreamableHttpMCPToolset(MCPToolset):
    """Streamable-HTTP-based MCP toolset that supports HTTP bidirectional streaming communication."""

    def __init__(self):
        super().__init__()
        self._connection_params = StreamableHTTPConnectionParams(
            # Required, replace the url with the actual address
            url="http://localhost:8000/mcp",
            # Optional HTTP headers
            headers={"Authorization": "Bearer token"},
            # Optional timeout in seconds
            timeout=5,
            # Optional SSE read timeout in seconds
            sse_read_timeout=60 * 5,
            # Optional flag to close the client session, default is True
            terminate_on_close=True,
        )
```
Notes:
- This uses the `streamable-http` protocol; if testing, you need to manually start the `mcp_server.py` program
- When starting, you need to modify as follows:
```python
if __name__ == "__main__":
    # app.run(transport="stdio")
    # app.run(transport="sse")
    app.run(transport="streamable-http")
```

### Framework Integration

Use `MCPToolset` the same way you use a **ToolSet**:

```python
from trpc_agent_sdk.agents import LlmAgent
from .tools import StdioMCPToolset


def create_agent() -> LlmAgent:
    """Create an Agent that uses MCP tools"""
    mcp_toolset = StdioMCPToolset()
    # mcp_toolset = SseMCPToolset()
    # mcp_toolset = StreamableHttpMCPToolset()

    agent = LlmAgent(
        name="mcp_assistant",
        description="An assistant that uses MCP tools for weather and calculation",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[mcp_toolset],
    )
    return agent


root_agent = create_agent()
```

### MCP Tools Complete Example

For a complete MCP Tools usage example, see: [examples/mcp_tools/run_agent.py](../../../examples/mcp_tools/run_agent.py)

### MCP Tools FAQ

#### Encountering `Attempted to exit a cancel scope that isn't the current tasks's current cancel scope`

This error occurs because the official MCP library relies on AnyIO: when entering and exiting a cancel scope happen in different task contexts, this error is raised. If you see it while running an Agent, do the following:

```python

async def main():
    # ...
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)
    async for event in runner.run_async(user_id=user_id, session_id=current_session_id, new_message=user_content):
        # ...
    await runner.close()

```

If the error persists, execute the following at the program entry point:

```python
from trpc_agent_sdk.tools import patch_mcp_cancel_scope_exit_issue

patch_mcp_cancel_scope_exit_issue()

# Application entry point

```

## ToolSet

Tool Set is a collection of tools in the trpc_agent framework.

### Using ToolSet

When you need to organize multiple related tools, you can use **ToolSet**.

#### 1. Creating a ToolSet

To create a custom ToolSet, inherit from `BaseToolSet` and implement the required methods:

```python
from typing import List, Optional

from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import BaseTool, BaseToolSet, FunctionTool


class WeatherToolSet(BaseToolSet):
    """Weather toolset containing all weather-related tools"""

    def __init__(self):
        super().__init__()
        self.name = "weather_toolset"
        self.tools = []

    @override
    def initialize(self) -> None:
        """Initialize the toolset and create all weather-related tools"""
        super().initialize()
        self.tools = [
            FunctionTool(self.get_current_weather),
            FunctionTool(self.get_weather_forecast),
        ]

    @override
    async def get_tools(self, invocation_context: Optional[InvocationContext] = None) -> List[BaseTool]:
        """Return available tools based on context"""
        # Return only basic tools when there is no context
        if not invocation_context:
            return self.tools[:1]

        # Get user type from session state to dynamically filter tools
        user_type = invocation_context.session.state.get("user_type", "basic")

        if user_type == "vip":
            return self.tools  # VIP users can use all tools
        else:
            return self.tools[:1]  # Regular users can only use basic functionality

    @override
    async def close(self) -> None:
        """Clean up resources"""
        # Close database connections, clean up caches, etc.
        pass

    # Tool methods
    async def get_current_weather(self, city: str) -> dict:
        """Get current weather

        Args:
            city: City name, e.g., "Beijing", "Shanghai"

        Returns:
            Current weather information
        """
        # Simulated weather data
        weather_data = {
            "Beijing": {
                "temperature": "15°C",
                "condition": "Sunny",
                "humidity": "45%",
            },
            "Shanghai": {
                "temperature": "18°C",
                "condition": "Cloudy",
                "humidity": "65%",
            },
            "Shenzhen": {
                "temperature": "25°C",
                "condition": "Light Rain",
                "humidity": "80%",
            },
        }

        if city in weather_data:
            return {
                "status": "success",
                "city": city,
                **weather_data[city],
                "timestamp": "2024-01-01T12:00:00Z",
            }
        else:
            return {
                "status": "error",
                "error_message": f"Weather data for {city} is not available",
                "supported_cities": list(weather_data.keys()),
            }

    async def get_weather_forecast(self, city: str, days: int = 3) -> dict:
        """Get weather forecast

        Args:
            city: City name
            days: Number of forecast days, default is 3

        Returns:
            Weather forecast information
        """
        return {
            "status": "success",
            "city": city,
            "forecast_days": days,
            "forecast": [
                {
                    "date": f"2024-01-{i + 1:02d}",
                    "temperature": f"{20 + i}°C",
                    "condition": "Sunny",
                }
                for i in range(days)
            ],
        }
```

#### 2. Using a ToolSet

A ToolSet can be directly added to the Agent's tool list:

```python
from trpc_agent_sdk.agents import LlmAgent

from .tools import WeatherToolSet


def create_agent() -> LlmAgent:
    """Create an Agent with a weather toolset"""
    # Create a ToolSet instance and initialize it
    weather_toolset = WeatherToolSet()
    weather_toolset.initialize()

    # Use in an Agent; the tools list can contain both Tools and ToolSets
    agent = LlmAgent(
        name="weather_toolset_agent",
        description="A weather assistant demonstrating ToolSet usage",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[weather_toolset],
    )
    return agent


root_agent = create_agent()
```

#### 3. ToolSet Best Practices

- **Functional Grouping**: Organize tools with related functionality in the same ToolSet
- **Access Control**: Use the `get_tools` method to implement user-based tool access control
- **Resource Management**: Properly clean up resources in the `close` method
- **Initialization**: Complete tool creation and configuration in the `initialize` method
- **Shutdown**: Execute the `close` method for graceful shutdown after the Runner completes execution


### ToolSet Complete Example

For a complete ToolSet usage example, see: [examples/toolsets/run_agent.py](../../../examples/toolsets/run_agent.py)

---

## Agent Tools

trpc_agent provides **AgentTool**, which allows wrapping an Agent as a Tool, enabling the output of one Agent to be used as the input of another Agent.


### Using AgentTool

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.tools import AgentTool

# Create a specialized translation Agent
translator = LlmAgent(
    name="translator",
    model=model,
    description="A professional text translation tool",
    instruction=TRANSLATOR_INSTRUCTION,
)

# Wrap the Agent as a Tool
translator_tool = AgentTool(agent=translator)

# Use in the main Agent
main_agent = LlmAgent(
    name="content_processor",
    description="A content processing assistant that can invoke translation tools",
    model=model,
    instruction=MAIN_INSTRUCTION,
    tools=[translator_tool],
)
```


### AgentTool Parameters

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `agent` | AgentABC | Required | The Agent to be wrapped |
| `skip_summarization` | bool | False | Whether to skip summarization |
| `filters_name` | list[str] | None | Associated filter names |

### Agent Tools Complete Example

For a complete AgentTool usage example, see: [examples/agent_tools/run_agent.py](../../../examples/agent_tools/run_agent.py)

---

## File Tools

File Tools is a set of file operation and text processing tools provided by the trpc_agent framework. These tools provide Agents with basic capabilities for reading, writing, editing, searching, and command execution, suitable for various file operation scenarios.

### Tool Overview

File Tools contains the following 6 tools:

1. **Read** - Read file contents
2. **Write** - Write or append file contents
3. **Edit** - Replace text blocks in files
4. **Grep** - Search file contents using regular expressions
5. **Bash** - Execute shell commands
6. **Glob** - Find files using glob patterns

### Using FileToolSet

The simplest approach is to use `FileToolSet`, which automatically includes all file operation tools:

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.tools.file_tools import FileToolSet
from trpc_agent_sdk.models import OpenAIModel

# Create the toolset with an optional working directory
file_tools = FileToolSet(cwd="/path/to/workspace")

# Use in an Agent
agent = LlmAgent(
    name="file_assistant",
    model=OpenAIModel(model_name="deepseek-v3-local-II"),
    instruction="You are a file operation assistant that helps users read, write, and edit files.",
    tools=[file_tools],  # Add the file toolset
)
```

#### Working Directory

All tools share the same working directory (`cwd`), and relative paths are resolved based on this directory:

```python
# Specify the working directory
file_tools = FileToolSet(cwd="/home/user/project")

# Use relative paths when invoking tools
# Read(path="config.ini") will read /home/user/project/config.ini
```

If `cwd` is not specified, the tools will use the current working directory.

### Using Individual Tools

If you need to use a specific tool individually, you can import and add them one by one:

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.tools.file_tools import ReadTool, WriteTool, EditTool, GrepTool, BashTool, GlobTool

# Create the working directory
work_dir = "/path/to/workspace"

# Create tool instances, each tool can be configured independently
read_tool = ReadTool(cwd=work_dir)      # Read file contents
write_tool = WriteTool(cwd=work_dir)     # Write or append to files
edit_tool = EditTool(cwd=work_dir)       # Replace text blocks in files
grep_tool = GrepTool(cwd=work_dir)       # Search for patterns using regex
bash_tool = BashTool(cwd=work_dir)       # Execute shell commands
glob_tool = GlobTool(cwd=work_dir)       # Find files matching glob patterns

# Add tools individually to the Agent
agent = LlmAgent(
    name="file_assistant",
    description="File operations assistant with file operation tools",  # Agent description
    model=_create_model(),
    instruction=INSTRUCTION,
    tools=[read_tool, write_tool, edit_tool, grep_tool, bash_tool, glob_tool],
)
```

This approach allows flexible selection of the needed tools and enables configuring different working directories for different tools.

### Tool Details

#### 1. Read Tool

Reads file contents, supporting reading the entire file or a specified line range.

**Features:**
- Read the entire file
- Read a specified line range (start_line to end_line)
- Automatic file encoding detection
- Large file support (with line count limits)

**Usage Example:**
```python
# The Agent invokes automatically; parameter examples:
# Read(path="config.ini")  # Read the entire file
# Read(path="app.py", start_line=10, end_line=20)  # Read lines 10-20
```

**Return Format:**
```python
{
    "success": True,
    "content": "File contents...",
    "total_lines": 100,
    "read_range": "1-100"  # or "10-20"
}
```

#### 2. Write Tool

Writes or appends content to a file.

**Features:**
- Write new files
- Overwrite existing files
- Append content to the end of a file
- Automatically create directories

**Usage Example:**
```python
# The Agent invokes automatically; parameter examples:
# Write(path="output.txt", content="Hello, World!\n")  # Write a new file
# Write(path="log.txt", content="New log entry\n", append=True)  # Append content
```

**Return Format:**
```python
{
    "success": True,
    "message": "SUCCESS: file output.txt written to successfully (13 bytes)",
    "path": "/path/to/output.txt"
}
```

#### 3. Edit Tool

Replaces text blocks in files, supporting exact matching and tolerance matching.

**Features:**
- Exact text block replacement
- Multi-line text replacement support
- Whitespace tolerance (spaces/tabs)
- Similarity hints (when exact match is not found)

**Usage Example:**
```python
# The Agent invokes automatically; parameter examples:
# Edit(
#     path="config.ini",
#     old_string="host=localhost",
#     new_string="host=production-server"
# )
```

**Return Format:**
```python
{
    "success": True,
    "message": "SUCCESS: file config.ini modified successfully",
    "line_range": "5-5",
    "changed_line_ranges": [(5, 5)]
}
```

#### 4. Grep Tool

Searches file contents using regular expressions.

**Features:**
- Single file or directory search
- Regular expression support
- Case-sensitive/insensitive options
- Result count limit

**Usage Example:**
```python
# The Agent invokes automatically; parameter examples:
# Grep(pattern="def.*function", path="src/", case_sensitive=False)
# Grep(pattern="TODO|FIXME", path=".", max_results=50)
```

**Return Format:**
```python
{
    "success": True,
    "matches": [
        {
            "file": "src/main.py",
            "line": 42,
            "content": "def my_function():"
        }
    ],
    "total_matches": 1
}
```

#### 5. Bash Tool

Executes shell commands.

**Features:**
- Execute arbitrary shell commands
- Support for pipes and redirections
- Timeout control (default 300 seconds)
- Security restrictions (commands outside the working directory must be on the whitelist)

**Usage Example:**
```python
# The Agent invokes automatically; parameter examples:
# Bash(command="ls -la", cwd="src/")
# Bash(command="git status", timeout=60)
# Bash(command="find . -name '*.py' | head -10")
```

**Security Restrictions:**
- No restrictions for commands within the working directory
- Commands outside the working directory are restricted to the whitelist: `ls`, `pwd`, `cat`, `grep`, `find`, `head`, `tail`, `wc`, `echo`

**Return Format:**
```python
{
    "success": True,
    "stdout": "Command output...",
    "stderr": "",
    "return_code": 0,
    "command": "ls -la",
    "cwd": "/path/to/workspace"
}
```

#### 6. Glob Tool

Finds files using glob patterns.

**Features:**
- Standard glob pattern support
- Recursive search (`**`)
- Brace expansion (`*.{py,js,go}`)
- Result count limit (default 1000)

**Usage Example:**
```python
# The Agent invokes automatically; parameter examples:
# Glob(pattern="*.txt")  # Find all .txt files
# Glob(pattern="**/*.py")  # Recursively find all Python files
# Glob(pattern="**/*.{py,js}")  # Find Python and JavaScript files
```

**Return Format:**
```python
{
    "success": True,
    "matches": ["/path/to/file1.txt", "/path/to/file2.txt"],
    "count": 2,
    "truncated": False,
    "pattern": "*.txt"
}
```

### File Tools Complete Example

For a complete File Tools usage example, see: [examples/file_tools/run_agent.py](../../../examples/file_tools/run_agent.py)

### File Tools Best Practices

#### 1. Working Directory Management

It is recommended to specify an independent working directory for each project or task:

```python
# Use different directories for different projects
project_a_tools = FileToolSet(cwd="/path/to/project_a")
project_b_tools = FileToolSet(cwd="/path/to/project_b")
```

#### 2. Security Considerations

- **Bash Tool** has security restrictions outside the working directory, allowing only whitelisted commands
- Avoid letting the Agent execute dangerous commands (e.g., `rm -rf /`)
- Consider stricter security policies in production environments

#### 3. File Size Limits

- **Read Tool** has file size and line count limits (default maximum 10MB, 10000 lines)
- For large files, consider using **Grep Tool** for searching instead of reading the entire file

#### 4. Error Handling

All tools return results containing a `success` field:

```python
# On success
{
    "success": True,
    ...
}

# On failure
{
    "success": False,
    "error": "Error message..."
}
```

The Agent can determine whether the operation succeeded based on the `success` field and take appropriate action.

For a complete File Tools usage example, see: [examples/file_tools/agent/agent.py](../../../examples/file_tools/agent/agent.py)

The example code demonstrates how to:
- Import and create tool instances individually
- Configure working directories for each tool
- Add tools individually to the Agent
- Run file operation tasks

---

## LangChain Tools

LangChain Tools allow you to reuse tools from the LangChain community or official ecosystem in trpc_agent. This article uses Tavily search as an example to demonstrate how to integrate `langchain_tavily.TavilySearch` as a tool into `LlmAgent`.

trpc_agent offers the same developer experience as **Function Tools**: wrap functionality as an async function → wrap with `FunctionTool` → inject into `LlmAgent`.

### Using Tools

#### 1. Minimal Tavily Tool Integration

```python
# examples/langchain_tools/agent/tools.py
from typing import Any

from langchain_tavily import TavilySearch


async def tavily_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """Web search based on Tavily

    Args:
        query: Search query
        max_results: Maximum number of results to return

    Returns:
        Structured search results containing a hit list and count
    """
    try:
        # Instantiate TavilySearch with maximum number of results
        tool = TavilySearch(max_results=max_results)
        # Async invocation for non-blocking search
        res = await tool.ainvoke(query)

        # Handle different return formats across versions: dict with "results" key / direct list / other
        if isinstance(res, dict) and "results" in res:
            items = res["results"]
        elif isinstance(res, list):
            items = res
        else:
            items = []

        # Return unified structured results
        return {
            "status": "success",
            "query": query,
            "result_count": len(items),
            "results": items,
        }
    except Exception as e:  # pylint: disable=broad-except
        # Catch all exceptions and return error info instead of raising, to avoid interrupting the conversation flow
        return {"status": "error", "error_message": str(e)}
```

```python
# examples/langchain_tools/agent/agent.py
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import tavily_search


def _create_model() -> LLMModel:
    """Create a model instance"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent() -> LlmAgent:
    """Create an Agent integrated with the LangChain Tavily search tool"""
    agent = LlmAgent(
        name="langchain_tavily_agent",
        description="An assistant integrated with LangChain Tavily search tool",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[FunctionTool(tavily_search)],
    )
    return agent


# Export root_agent at module level for direct reference by Runner
root_agent = create_agent()
```

#### 2. Invocation Mode Description

- The official Tavily package is `langchain_tavily`: recommended class `TavilySearch`
- Supports both `invoke` and `ainvoke`:
  - Synchronous: `invoke` (blocks the current thread)
  - Asynchronous: `ainvoke` (recommended, non-blocking)

### Parameter Handling

#### Parameter Types

As with Function Tools, JSON-serializable parameters are recommended. In the example, `query: str` and `max_results: int` are both simple types, making it easy for the LLM to fill them correctly.

```python
async def tavily_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """Web search based on Tavily"""
    tool = TavilySearch(max_results=max_results)
    res = await tool.ainvoke(query)
    # Handle different return formats across versions
    if isinstance(res, dict) and "results" in res:
        items = res["results"]
    elif isinstance(res, list):
        items = res
    else:
        items = []
    return {
        "status": "success",
        "query": query,
        "result_count": len(items),
        "results": items,
    }
```

#### Framework Context Parameters

If you need to access session state or context, add `tool_context: InvocationContext` as needed (can coexist with business parameters; automatically injected by the framework):

```python
from trpc_agent_sdk.context import InvocationContext

async def tavily_search(query: str, tool_context: InvocationContext, max_results: int = 5) -> dict[str, Any]:
    # Can use tool_context.session / tool_context.state, etc.
    tool = TavilySearch(max_results=max_results)
    res = await tool.ainvoke(query)
    # Handle different return formats across versions
    if isinstance(res, dict) and "results" in res:
        items = res["results"]
    elif isinstance(res, list):
        items = res
    else:
        items = []
    return {
        "status": "success",
        "query": query,
        "result_count": len(items),
        "results": items,
    }
```

### Return Value Handling

#### Recommended Return Types

Return a dictionary (dict) to provide structured information for the LLM. The example uniformly returns:

```python
return {
    "status": "success",
    "query": query,
    "result_count": len(items),
    "results": items,
}
```

#### Status Indication

When exceptions occur, returning `status=error` and `error_message` helps the LLM adopt fallback strategies:

```python
try:
    tool = TavilySearch(max_results=max_results)
    res = await tool.ainvoke(query)
    # ... normal processing logic ...
    return {"status": "success", "query": query, "result_count": len(items), "results": items}
except Exception as e:  # pylint: disable=broad-except
    return {"status": "error", "error_message": str(e)}
```

### Docstring and Comments

The tool function's docstring is exposed to the model as the tool description. It is recommended to include: purpose, parameters, and return examples to help the model better align expectations.

```python
async def tavily_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """Search the web using Tavily and return results.

    Requires the TAVILY_API_KEY environment variable to be set.

    Args:
        query: The search query text.
        max_results: Maximum number of results to return.

    Returns:
        A dict containing search status and results.
    """
    ...
```

### LangChain Tools Best Practices

#### 1. Tool Design Principles

- **Single Responsibility**: Each tool should focus on a single capability (e.g., search, translation) to avoid functional coupling
- **Clear Naming**: Function names should directly express intent (e.g., `tavily_search`) so the model can understand at a glance
- **Complete Documentation**: Clearly describe the purpose, parameter meanings, return structure, and edge cases in the docstring

#### 2. Parameter Design

- Keep parameters flat; avoid deeply nested complex structures
- Use semantically clear parameter names (e.g., `query`, `max_results`) to reduce the probability of LLM misfilling
- Provide reasonable default values for optional parameters to reduce required fields during invocation

#### 3. Error Handling

- Always catch exceptions that third-party calls may throw (network timeouts, authentication failures, etc.)
- Return error information in a structured format (e.g., `{"status": "error", "error_message": ...}`) instead of raising exceptions directly, ensuring the conversation flow is not interrupted

### LangChain Tools Complete Example

For a complete usage example, see: [examples/langchain_tools/run_agent.py](../../../examples/langchain_tools/run_agent.py)

For more LangChain Tool usage, refer to: [LangChain Tool](https://python.langchain.com/docs/integrations/tools/)

---

## Streaming Tools

Streaming tools allow you to **see the tool parameters being generated by the AI in real time**, without waiting for the entire generation to complete. This is particularly useful for long text scenarios such as code generation and document writing.

---

### Why Streaming Tools?

Traditional tool invocation flow:
```
User request → AI reasoning → [Long wait] → Complete parameters returned → Execution
```

Streaming tool invocation flow:
```
User request → AI reasoning → Generate and return progressively → Real-time preview → Execution
```

**Typical Scenarios**:
- 📝 **Code Generation**: Real-time preview of code being generated
- 📄 **Document Writing**: Watch content being generated progressively
- 🔍 **Long Text Processing**: Reduce waiting anxiety, detect issues early

---

### Underlying Principles: Model Streaming Output Format

Before diving into usage, understanding the underlying principles helps with better comprehension and debugging.

#### OpenAI API Streaming Output

When the LLM invokes a tool, OpenAI returns parameters progressively through multiple chunks:

```
Request: "Please create a test.txt file with the content Hello World"

↓ Model starts streaming tool call output ↓

Chunk 1: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_abc123","function":{"name":"write_file","arguments":"{"}}]}}]}
Chunk 2: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\"path\":"}}]}}]}
Chunk 3: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\"test.txt\""}}]}}]}
Chunk 4: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":",\"content\":"}}]}}]}
Chunk 5: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\"Hello"}}]}}]}
Chunk 6: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":" World\""}}]}}]}
Chunk 7: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"}"}}]}}]}
Chunk 8: {"choices":[{"finish_reason":"tool_calls"}]}
```

**Key Field Descriptions**:

| Field | Description | Example |
|------|------|------|
| `delta.tool_calls[].index` | Tool call index (supports parallel multi-tool calls) | `0` |
| `delta.tool_calls[].id` | Tool call unique ID (first chunk only) | `"call_abc123"` |
| `delta.tool_calls[].function.name` | Tool name (first chunk only) | `"write_file"` |
| `delta.tool_calls[].function.arguments` | **Incremental argument string** | `"\"path\":"` |

**Parameter Accumulation Process**:

```
Chunk 1: arguments = "{"
Chunk 2: arguments = "{" + "\"path\":" = "{\"path\":"
Chunk 3: arguments = "{\"path\":" + "\"test.txt\"" = "{\"path\":\"test.txt\""
...
Final:   arguments = "{\"path\":\"test.txt\",\"content\":\"Hello World\"}"
```

#### Anthropic Claude Streaming Output

Claude uses an event-driven streaming format:

```
Event 1: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_01","name":"write_file","input":{}}}
Event 2: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\""}}
Event 3: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"path\":"}}
Event 4: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"\"test.txt\""}}
...
Event 8: {"type":"content_block_stop","index":0}
Event 9: {"type":"message_delta","delta":{"stop_reason":"tool_use"}}
```

#### Framework Unified Processing

The trpc_agent framework converts outputs from different models into a unified internal format:

```
┌─────────────────────────────────────────────────────────────────┐
│  OpenAI: delta.tool_calls[].function.arguments                  │
│  Anthropic: content_block_delta + input_json_delta              │
└───────────────────────────┬─────────────────────────────────────┘
                            ↓ Framework unified conversion
┌─────────────────────────────────────────────────────────────────┐
│  function_call.args = {                                         │
│      "tool_streaming_args": "incremental content for this chunk"│
│  }                                                              │
└─────────────────────────────────────────────────────────────────┘
```

This way, when consuming events, you don't need to care whether the underlying model is OpenAI or Claude — simply use `tool_streaming_args` to get the incremental content.

---

### Quick Start

#### Wrapping with `StreamingFunctionTool`

```python
from trpc_agent_sdk.tools import StreamingFunctionTool

"""Agent tool module.

Provides two tool types:
  1. StreamingFunctionTool: Supports streaming parameter transmission, suitable for large text content.
  2. FunctionTool: Standard synchronous tool, suitable for simple queries.
"""


def write_file(path: str, content: str) -> dict:
    """Write content to a file (streaming).

    Args:
        path: The file path to write to.
        content: The content to write.

    Returns:
        A dictionary containing success status, path, and content size.
    """
    print(f"\n📄 Writing to {path}...")
    print(f"Content: {content[:100]}...")
    return {"success": True, "path": path, "size": len(content)}

# Create a streaming tool
streaming_tool = StreamingFunctionTool(write_file)
```

#### Integrating into an Agent

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel, OpenAIModel
from trpc_agent_sdk.tools import FunctionTool, StreamingFunctionTool

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import get_file_info, write_file


def _create_model() -> LLMModel:
    """Create a model instance"""
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def create_agent() -> LlmAgent:
    """Create an Agent with streaming and standard tools.

    Tool list:
      - write_file: StreamingFunctionTool, streaming file write.
      - get_file_info: FunctionTool, query file information.
    """
    return LlmAgent(
        name="streaming_tool_demo_agent",
        description="An assistant demonstrating streaming tool usage",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[
            StreamingFunctionTool(write_file),  # Streaming tool
            FunctionTool(get_file_info),        # Standard tool
        ],
    )


root_agent = create_agent()
```

#### Running and Handling Events

```python
import asyncio
import uuid

from dotenv import load_dotenv

from trpc_agent_sdk.models import constants as const
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part

# Load environment variables
load_dotenv()


async def run_streaming_tool_agent():
    """Run the streaming tool demo Agent"""

    app_name = "streaming_tool_demo"

    # Import the Agent
    from agent.agent import root_agent

    # Create session service and runner
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"

    # Demo query list
    demo_queries = [
        "Please create a Python script hello.py that implements a simple calculator",
    ]

    for query in demo_queries:
        current_session_id = str(uuid.uuid4())

        # Create a session
        await session_service.create_session(
            app_name=app_name,
            user_id=user_id,
            session_id=current_session_id,
        )

        print(f"🆔 Session ID: {current_session_id[:8]}...")
        print(f"📝 User: {query}")
        print("🤖 Assistant: ", end="", flush=True)

        user_content = Content(parts=[Part.from_text(text=query)])

        # For accumulating streaming content
        accumulated_content = ""

        async for event in runner.run_async(
            user_id=user_id,
            session_id=current_session_id,
            new_message=user_content,
        ):
            if not event.content or not event.content.parts:
                continue

            # 🔥 Streaming tool call event - parameters are being generated
            if event.is_streaming_tool_call():
                for part in event.content.parts:
                    if part.function_call:
                        # Get incremental content
                        delta = part.function_call.args.get(const.TOOL_STREAMING_ARGS, "")
                        accumulated_content += delta
                        print(f"⏳ Generated {len(accumulated_content)} chars...", end="\r")
                continue

            # Streaming text output
            if event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                continue

            # ✅ Complete tool call - parameter generation complete
            for part in event.content.parts:
                if part.thought:
                    continue
                if part.function_call:
                    print(f"\n✅ Code generation complete!")
                    accumulated_content = ""  # Reset accumulation
                elif part.function_response:
                    print(f"📊 [Tool Result: {part.function_response.response}]")
                elif part.text:
                    print(f"\n💬 {part.text}")

        print("\n" + "-" * 40)

    # Close the runner
    await runner.close()


if __name__ == "__main__":
    asyncio.run(run_streaming_tool_agent())
```

---

### Multiple Ways to Create Streaming Tools

trpc_agent provides several ways to create streaming tools for different scenarios:

#### 1. Wrapping a Synchronous Function

```python
from trpc_agent_sdk.tools import StreamingFunctionTool


def write_file(path: str, content: str) -> dict:
    """Write content to a file (streaming)."""
    print(f"\n📄 Writing to {path}...")
    print(f"Content: {content[:100]}...")
    return {"success": True, "path": path, "size": len(content)}

# Create a streaming tool
streaming_tool = StreamingFunctionTool(write_file)
```

#### 2. Wrapping an Asynchronous Function

```python
from trpc_agent_sdk.tools import StreamingFunctionTool


async def async_write_file(path: str, content: str) -> dict:
    """Asynchronously write to a file."""
    print(f"\n📄 Writing to {path}...")
    print(f"Content: {content[:100]}...")
    return {"success": True, "path": path, "size": len(content)}

streaming_tool = StreamingFunctionTool(async_write_file)
```

#### 3. Converting from FunctionTool

An existing `FunctionTool` can be directly converted to a streaming tool:

```python
from trpc_agent_sdk.tools import FunctionTool, StreamingFunctionTool

# Existing FunctionTool
regular_tool = FunctionTool(write_file)

# Convert to a streaming tool
streaming_tool = StreamingFunctionTool(regular_tool)
```

#### 4. Custom BaseTool

Inherit from `BaseTool` and override the `is_streaming` property:

```python
from trpc_agent_sdk.tools import BaseTool
from typing_extensions import override

class CustomStreamingWriteTool(BaseTool):
    """Custom streaming tool."""

    def __init__(self):
        super().__init__(
            name="custom_write",
            description="Custom streaming write tool",
        )

    @property
    @override
    def is_streaming(self) -> bool:
        """Enable streaming parameters."""
        return True

    @override
    async def _run_async_impl(self, *, tool_context, args):
        # Tool implementation logic
        return {"success": True}
```

#### 5. Using Streaming Tools in a ToolSet

Streaming tools within a `ToolSet` are automatically detected by the framework:

```python
from trpc_agent_sdk.tools import BaseToolSet, StreamingFunctionTool, FunctionTool


class FileToolSet(BaseToolSet):
    """File operation toolset."""

    def __init__(self):
        super().__init__(name="file_tools")
        self._tools = [
            StreamingFunctionTool(self._write_file),  # Streaming tool
            FunctionTool(self._get_file_info),         # Standard tool
        ]

    def _write_file(self, path: str, content: str) -> dict:
        """Write content to a file (streaming)."""
        print(f"\n📄 Writing to {path}...")
        print(f"Content: {content[:100]}...")
        return {"success": True, "path": path, "size": len(content)}

    def _get_file_info(self, path: str) -> dict:
        """Get file information (non-streaming)."""
        return {"path": path, "exists": True}

    async def get_tools(self, invocation_context=None):
        return self._tools
```

---

### Streaming Tools Complete Example

For a complete streaming tools usage example, see: [examples/streaming_tools/run_agent.py](../../../examples/streaming_tools/run_agent.py)

---

### Streaming Tools API Reference

#### @register_tool Decorator

A decorator for registering tool functions, with streaming configuration support.

```python
@register_tool(
    name: str = '',           # Tool name (defaults to the function name)
    description: str = '',    # Tool description (defaults to the function docstring)
    filters_name: list[str] = None,  # Filter names
)
```

**Example**:

```python
@register_tool("get_weather")
def get_weather(city: str) -> dict:
    """Get weather information."""
    return {"temp": 20}
```

> **Note**: `@register_tool` registers a standard tool. To create a streaming tool, use `StreamingFunctionTool`.

#### StreamingFunctionTool

A function tool class that supports streaming parameters.

**Constructor**:

```python
StreamingFunctionTool(
    func: Union[Callable, FunctionTool],  # The function or FunctionTool to wrap
    filters_name: list[str] = None,       # Filter names (optional)
    filters: list[BaseFilter] = None,     # Filter instances (optional)
)
```

**Parameter Description**:

| Parameter | Type | Description |
|------|------|------|
| `func` | `Callable \| FunctionTool` | The function to wrap; supports synchronous/asynchronous functions or an existing FunctionTool |
| `filters_name` | `list[str]` | Optional list of filter names |
| `filters` | `list[BaseFilter]` | Optional list of filter instances |

#### is_streaming Property

All tools have an `is_streaming` property to indicate whether streaming parameters are supported:

```python
from trpc_agent_sdk.tools import StreamingFunctionTool, FunctionTool
from .tools import write_file, get_file_info

# StreamingFunctionTool's is_streaming always returns True
streaming_tool = StreamingFunctionTool(write_file)
print(streaming_tool.is_streaming)  # True

# FunctionTool's is_streaming returns False
regular_tool = FunctionTool(get_file_info)
print(regular_tool.is_streaming)  # False
```

#### Event Types

Streaming tool calls produce special events that can be identified as follows:

```python
# Method 1: Use the is_streaming_tool_call() method (recommended)
if event.is_streaming_tool_call():
    # This is a streaming tool call event
    pass

# Method 2: Check the partial flag and function_call
if event.partial and event.content:
    for part in event.content.parts:
        if part.function_call:
            # This is a streaming tool call event
            pass
```

#### Incremental Content Retrieval

The parameters of streaming tool call events contain the `tool_streaming_args` field, representing the current increment:

```python
from trpc_agent_sdk.models import constants as const

if event.is_streaming_tool_call():
    for part in event.content.parts:
        if part.function_call:
            args = part.function_call.args or {}

            # Get incremental content
            delta = args.get(const.TOOL_STREAMING_ARGS, "")
            # Or use the string literal
            # delta = args.get("tool_streaming_args", "")
```

---

### Selective Streaming Tool Support

The framework supports **mixing** streaming tools and standard tools within the same Agent. Only tools marked as streaming will produce streaming events:

```python
from trpc_agent_sdk.tools import StreamingFunctionTool, FunctionTool
from .tools import write_file, get_file_info

agent = LlmAgent(
    tools=[
        StreamingFunctionTool(write_file),   # ✅ Produces streaming events
        FunctionTool(get_file_info),          # ❌ Does not produce streaming events
    ],
)
```

#### Detection Flow

The framework automatically detects the `is_streaming` property of tools at runtime:

```
Agent initialization
     ↓
ToolsProcessor.process_llm_request()
     ↓
Iterate through all tools (including tools within ToolSets)
     ↓
Check each tool's is_streaming property
     ↓
Collect names of all tools with is_streaming=True
     ↓
Enable streaming parameter transmission only for these tools
```

This means:
- Tools created with `StreamingFunctionTool` will produce streaming events
- Standard `FunctionTool` will not produce streaming events
- Streaming tools within a `ToolSet` will be correctly detected
- Custom `BaseTool` implementations need to override the `is_streaming` property

---

### Integration with ClaudeAgent

`ClaudeAgent` also supports selective streaming tools, with behavior consistent with `LlmAgent`:

```python
from trpc_agent_sdk.server.agents.claude import ClaudeAgent
from trpc_agent_sdk.tools import StreamingFunctionTool, FunctionTool
from .tools import write_file, get_file_info

agent = ClaudeAgent(
    name="claude_writer",
    model=_create_model(),
    tools=[
        StreamingFunctionTool(write_file),  # Streaming: display parameters in real time
        FunctionTool(get_file_info),         # Non-streaming: parameters returned at once
    ],
)
```

---

### Integration with A2A Protocol

A2A (Agent-to-Agent) protocol supports cross-service Agent invocation. Streaming tool call events can be transmitted in real time to remote clients through the A2A protocol.

#### A2A Event Conversion

```
trpc_agent event                     A2A protocol event
──────────────────────────────────────────────────────────────
streaming_tool_call (delta)    →   TaskStatusUpdateEvent
                                   └─ metadata: trpc_streaming_tool_call=true
                                   └─ DataPart: streaming_function_call_delta

tool_call (complete)           →   TaskStatusUpdateEvent
                                   └─ state: working
                                   └─ Message with function_call

tool_result                    →   TaskStatusUpdateEvent
                                   └─ Message with function_response
```

---

### Integration with AG-UI

If you use the AG-UI protocol, streaming tool calls are automatically converted to the corresponding events:

```
trpc_agent event                    AG-UI event
─────────────────────────────────────────────────────────
streaming_tool_call (partial)  →  TOOL_CALL_START
streaming_tool_call (delta)    →  TOOL_CALL_ARGS
tool_call (complete)           →  TOOL_CALL_END
```

Frontend JavaScript example:

```javascript
const eventSource = new EventSource('/api/agent/stream');

eventSource.onmessage = (event) => {
    const data = JSON.parse(event.data);

    switch (data.type) {
        case 'TOOL_CALL_START':
            console.log(`🔧 Tool started: ${data.toolCallId}`);
            break;

        case 'TOOL_CALL_ARGS':
            // Display incremental arguments
            console.log(`⏳ Argument delta: ${data.delta}`);
            break;

        case 'TOOL_CALL_END':
            console.log(`✅ Tool completed`);
            break;
    }
};
```

---

### Streaming Tools Best Practices

#### 1. Choose the Right Parameters for Streaming

Streaming tools are best suited for scenarios **with long text parameters**:

```python
from trpc_agent_sdk.tools import StreamingFunctionTool, FunctionTool
from .tools import write_file, get_file_info

# ✅ Suitable for streaming: the content parameter can be very long
streaming_tool = StreamingFunctionTool(write_file)

# ❌ Streaming not needed: all parameters are short
regular_tool = FunctionTool(get_file_info)
```

#### 2. Handle Streaming Events at the Runner Layer

```python
from trpc_agent_sdk.models import constants as const

# For accumulating streaming content
accumulated_content = ""

async for event in runner.run_async(
    user_id=user_id,
    session_id=current_session_id,
    new_message=user_content,
):
    if not event.content or not event.content.parts:
        continue

    if event.is_streaming_tool_call():
        for part in event.content.parts:
            if part.function_call:
                # Get incremental content
                delta = part.function_call.args.get(const.TOOL_STREAMING_ARGS, "")
                accumulated_content += delta
                # Display progress
                print(f"⏳ Generated {len(accumulated_content)} chars...", end="\r")
        continue
```

#### 3. Handling Multi-Tool Scenarios

```python
async for event in runner.run_async(
    user_id=user_id,
    session_id=current_session_id,
    new_message=user_content,
):
    if not event.content or not event.content.parts:
        continue

    if event.is_streaming_tool_call():
        for part in event.content.parts:
            if part.function_call:
                tool_name = part.function_call.name
                delta = part.function_call.args.get(const.TOOL_STREAMING_ARGS, "")

                # Handle differently based on tool name
                if tool_name == "write_file":
                    print(f"📄 Writing file: {delta[:30]}...")
                elif tool_name == "get_file_info":
                    print(f"📋 Querying file: {delta[:30]}...")
```

#### 4. Mixing Streaming and Non-Streaming Tools

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel, OpenAIModel
from trpc_agent_sdk.tools import FunctionTool, StreamingFunctionTool

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import get_file_info, write_file


def create_agent() -> LlmAgent:
    """Create an Agent with streaming and standard tools."""
    return LlmAgent(
        name="streaming_tool_demo_agent",
        description="An assistant demonstrating streaming tool usage",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[
            StreamingFunctionTool(write_file),  # Long text parameters: use streaming
            FunctionTool(get_file_info),        # Short parameters: use standard tool
        ],
        # The framework detects automatically; no manual stream_tool_call_args configuration needed
    )


root_agent = create_agent()
```

---

### Streaming Tools FAQ

#### Q: Can streaming tools and standard tools be used together?

**A**: Yes. You can use both streaming tools and standard tools within the same Agent. The framework automatically detects each tool's `is_streaming` property and enables streaming parameter transmission only for streaming tools:

```python
from trpc_agent_sdk.tools import StreamingFunctionTool, FunctionTool
from .tools import write_file, get_file_info

agent = LlmAgent(
    tools=[
        StreamingFunctionTool(write_file),  # Streaming
        FunctionTool(get_file_info),         # Non-streaming
    ],
)
```

#### Q: Which models support streaming tool calls?

**A**: Currently known supported models:
- ✅ glm4.7, glm5
- ✅ claude-opus-4.6
- ✅ kimi-k2.5
- ✅ gpt-5.2
- Other models need to be tested individually

#### Q: How to accumulate streaming content?

**A**: Accumulate when consuming events at the Runner layer:

```python
from trpc_agent_sdk.models import constants as const

# For accumulating streaming content
accumulated_content = ""

async for event in runner.run_async(
    user_id=user_id,
    session_id=current_session_id,
    new_message=user_content,
):
    if not event.content or not event.content.parts:
        continue

    if event.is_streaming_tool_call():
        for part in event.content.parts:
            if part.function_call:
                delta = part.function_call.args.get(const.TOOL_STREAMING_ARGS, "")
                accumulated_content += delta
                print(f"⏳ Generated {len(accumulated_content)} chars...", end="\r")
        continue
```

#### Q: How to determine if a tool supports streaming?

**A**: Check the tool's `is_streaming` property:

```python
from trpc_agent_sdk.tools import StreamingFunctionTool, FunctionTool
from .tools import write_file, get_file_info

streaming_tool = StreamingFunctionTool(write_file)
print(streaming_tool.is_streaming)  # True

regular_tool = FunctionTool(get_file_info)
print(regular_tool.is_streaming)  # False
```

#### Q: Are streaming tools within a ToolSet detected?

**A**: Yes. The framework recursively detects the `is_streaming` property of all tools within a ToolSet at runtime, ensuring streaming tools are correctly identified.

---

### Streaming Tools Related Resources

- [Streaming Tools Complete Example](../../../examples/streaming_tools/run_agent.py) - Streaming tools running example
- [Function Tools Documentation](#function-tools) - Usage of standard function tools

## WebFetchTool

`WebFetchTool` is the built-in **single-URL web fetching tool** in the trpc-agent-python framework. When an Agent needs to read, summarize, or cite content from a public web page, it can use this tool to issue a single HTTP GET request. The framework then converts the response into structured text consumable by the LLM: HTML is reduced to plain Markdown text, textual MIME types such as `text/*` and `application/json` are returned as-is, and binary responses are rejected with a structured error.

### Features

- **HTTP GET**: HTML is automatically converted to plain Markdown text, with non-content blocks such as `<script>`, `<style>`, and `<svg>` removed; other textual MIME types are returned as-is; binary responses (PDFs, images, archives, and so on) are rejected with the `UNSUPPORTED_CONTENT_TYPE` error
- **SSRF protection**: `block_private_network=True` (default) performs DNS resolution checks on the request target and **every redirect hop**, rejecting loopback, private, link-local (including the `169.254.169.254` cloud metadata endpoint), reserved, multicast, and unspecified addresses
- **Domain allowlist and blocklist**: `allowed_domains` and `blocked_domains` are **tool-level** settings with subdomain-aware matching (`www.` is stripped, and `python.org` also matches `docs.python.org`)
- **Dual truncation by content and bytes**: `max_content_length` (characters) and `max_response_bytes` (bytes) separately control the returned text length and the raw response bytes actually read; the LLM can further control the result at call time through the `max_length` parameter
- **Manual redirect control**: `follow_redirects` and `max_redirects` provide a predictable upper bound for redirect chains and prevent infinite redirects
- **In-process LRU cache**: when `enable_cache=True`, an LRU cache for URL -> `FetchResult` is enabled; `cache_ttl_seconds` and `cache_max_bytes` control the TTL and cache byte budget; on a cache hit, the response is marked with `cached=true`; cache keys use URL normalization (scheme case normalization, `www.` stripping, and ignoring default ports and trailing `/`)

### WebFetchTool Parameters

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `timeout` | `float` | `30.0` | HTTP timeout in seconds |
| `user_agent` | `str` | `"trpc-agent-python-webfetch/1.0"` | HTTP `User-Agent` header, used to help downstream systems identify request sources in logs |
| `proxy` | `Optional[str]` | `None` | Optional HTTP proxy URL, passed directly to `httpx` |
| `http_client` | `Optional[httpx.AsyncClient]` | `None` | Optional prebuilt `httpx.AsyncClient` for connection pool reuse (its lifecycle is managed by the caller) |
| `max_content_length` | `int` | `100_000` | Maximum number of characters returned in `content`; `0` means unlimited; can be overridden by the call-time `max_length` argument |
| `max_response_bytes` | `int` | `5 * 1024 * 1024` (5 MB) | Maximum number of raw response bytes to read; `0` means unlimited; streaming reads stop once the limit is reached |
| `allowed_domains` | `Optional[List[str]]` | `None` | Tool-level host allowlist (subdomain-aware, with `www.` stripped); cannot be overridden by the LLM |
| `blocked_domains` | `Optional[List[str]]` | `None` | Tool-level host blocklist; uses the same matching rules as the allowlist; checked **before the allowlist** |
| `block_private_network` | `bool` | `True` | SSRF protection switch; when enabled, rejects any target that resolves to private, loopback, link-local, or similar addresses |
| `follow_redirects` | `bool` | `True` | Whether to manually follow 3xx redirects |
| `max_redirects` | `int` | `5` | Maximum number of redirect hops |
| `enable_cache` | `bool` | `False` | Whether to enable the in-process LRU cache |
| `cache_ttl_seconds` | `float` | `900.0` (15 minutes) | Cache entry TTL; once expired, the next access will bypass and evict the entry |
| `cache_max_bytes` | `int` | `50 * 1024 * 1024` (50 MB) | Total cache byte capacity; entries exceeding the capacity are silently skipped |
| `filters_name` | `Optional[List[str]]` | `None` | Names of associated filters, passed through to `BaseTool` |
| `filters` | `Optional[List[BaseFilter]]` | `None` | Filter instances injected directly, passed through to `BaseTool` |

**LLM call-time parameters** (filled by the LLM at invocation time, not constructor arguments):

| Parameter | Type | Required | Description |
|------|------|------|------|
| `url` | `string` | Yes | Absolute `http(s)` URL that must include the scheme, such as `https://docs.python.org/3/whatsnew/3.13.html` |
| `max_length` | `integer` | No | Character limit for `content` in this call (overrides the tool-level `max_content_length`); `0` disables this limit |

**`FetchResult` return fields**:

| Field | Type | Description |
|------|------|------|
| `url` | `str` | Final URL after redirects |
| `status_code` | `int` | HTTP status code (`0` if the request did not complete) |
| `status_text` | `str` | HTTP reason phrase |
| `content_type` | `str` | Normalized media type without additional parameters |
| `content` | `str` | Textual response body, which may be truncated |
| `bytes` | `int` | UTF-8 byte length of `content` |
| `duration_ms` | `int` | Total request duration in milliseconds |
| `cached` | `bool` | Whether the in-process LRU cache was hit |
| `error` | `str` | Structured error code when the request fails or is rejected, such as `BLOCKED_URL`, `SSRF_BLOCKED_URL`, `HTTP_STATUS`, `UNSUPPORTED_CONTENT_TYPE`, or `HTTP_ERROR` |

### Usage

#### Create a WebFetchTool Agent

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel, OpenAIModel
from trpc_agent_sdk.tools import WebFetchTool

from .config import get_model_config
from .prompts import INSTRUCTION


def _create_model() -> LLMModel:
    """Create the LLM model."""
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def create_default_fetch_agent() -> LlmAgent:
    """Create a WebFetchTool Agent."""
    web_fetch = WebFetchTool(
        timeout=10.0, # Request timeout
        user_agent="trpc-agent-python-webfetch-example/1.0", # User-Agent header
        max_content_length=4000, # Maximum returned text length
        max_response_bytes=1 * 1024 * 1024, # Maximum raw response bytes to read
        follow_redirects=True, # Manually follow redirect chains
        max_redirects=3, # Maximum number of redirect hops
        block_private_network=True, # Enable SSRF protection
        # allowed_domains=["python.org"], # Domain allowlist
        # blocked_domains=["example.com"], # Domain blocklist
        # enable_cache=True, # Enable cache
        # cache_ttl_seconds=120.0, # Cache TTL
        # cache_max_bytes=1 * 1024 * 1024, # Cache byte budget
    )
    return LlmAgent(
        name="default_webfetch_assistant",
        description="Web-reading assistant that fetches a single URL and summarises its textual content.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[web_fetch],
    )
```

> **Note**:
> - `allowed_domains` and `blocked_domains` are **tool-level** settings and **cannot be overridden by the LLM in call arguments**; matching is **subdomain-aware** (`www.` is stripped), and **every redirect hop is revalidated** to prevent bypasses such as "an allowed first hop redirecting to a blocked host"
> - `block_private_network=True` enables SSRF protection by default; consider disabling it only when the caller already restricts targets with an external allowlist and fully trusts the input
> - `enable_cache` is disabled by default and must be explicitly enabled; cache keys apply URL normalization (scheme case normalization, `www.` stripping, ignoring default ports, and ignoring trailing `/`), so `https://example.com` and `https://www.example.com/` share the same cache entry

#### Drive the Agent and Print Tool Events

`run_agent.py` drives the Agent by executing each `(label, query)` scenario in sequence and extracting `function_call` / `function_response` from the event stream so that tool invocations can be observed directly:

```python
import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part

load_dotenv()

APP_NAME = "webfetch_agent_demo"
USER_ID = "demo_user"


async def _run_one_query(runner: Runner, *, label: str, query: str) -> None:
    """Drive a single user query through ``runner`` and pretty-print events."""
    session_id = str(uuid.uuid4())
    await runner.session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
        state={"user_name": USER_ID},
    )

    print(f"\n========== {label} ==========")
    print(f"📝 User: {query}")
    print("🤖 Assistant: ", end="", flush=True)

    user_content = Content(parts=[Part.from_text(text=query)])
    async for event in runner.run_async(
            user_id=USER_ID,
            session_id=session_id,
            new_message=user_content,
    ):
        if not event.content or not event.content.parts:
            continue

        if event.partial:
            for part in event.content.parts:
                if part.text:
                    print(part.text, end="", flush=True)
            continue

        for part in event.content.parts:
            # Skip thought parts
            if part.thought:
                continue
            # Print tool calls
            if part.function_call:
                print(f"\n🔧 [Invoke Tool: {part.function_call.name}({part.function_call.args})]")
            # Print tool responses
            elif part.function_response:
                resp = part.function_response.response
                print(f"📊 [Tool Result: {resp}]")

    print("\n" + "-" * 40)


async def _drive_agent(agent: LlmAgent, *, scenarios: list[tuple[str, str]]) -> None:
    """Drive the Agent through the scenarios."""
    runner = Runner(
        app_name=APP_NAME,
        agent=agent,
        session_service=InMemorySessionService(),
    )
    for label, query in scenarios:
        await _run_one_query(runner, label=label, query=query)


async def main() -> None:
    from agent.agent import default_fetch_agent

    await _drive_agent(default_fetch_agent, scenarios=[
        ("Default · plain fetch",
         "Fetch https://example.com and summarise the page in one short paragraph.")
    ])


if __name__ == "__main__":
    asyncio.run(main())
```

#### Example Output

**Example return value**:

When fetching succeeds, the `FetchResult` inside `function_response` looks like this:

```python
{
    "url": "https://example.com",
    "status_code": 200,
    "status_text": "OK",
    "content_type": "text/html",
    "content": "Example Domain\n\n# Example Domain\n\nThis domain is for use in ...",
    "bytes": 183,
    "duration_ms": 87,
    "cached": False,
    "error": "",
}
```

When the cache is hit, `cached=True`; when the request is blocked by domain policy or SSRF protection, `error` contains a structured error code:

```python
# Rejected by domain policy
{"url": "https://example.com", "error": "BLOCKED_URL: 'example.com' is not permitted by the tool's domain policy", ...}

# Rejected by SSRF protection (for example, when the target resolves to 127.0.0.1)
{"url": "http://localhost:8080", "error": "SSRF_BLOCKED_URL: localhost resolves to private/reserved address 127.0.0.1", ...}

# Rejected because the response is binary
{"url": "https://example.com/a.pdf", "error": "UNSUPPORTED_CONTENT_TYPE: application/pdf", ...}
```

It is recommended to specify in the Agent's `instruction` that when the tool returns an `error` field, the Agent should **repeat the error code and explain the reason** to the user rather than inventing content; when `content` is truncated or served from cache, that should also be stated explicitly in the response

### Best Practices

- **Security first**: when deploying an Agent in environments that can access cloud metadata endpoints such as AWS EC2 `169.254.169.254` or other internal resources, **keep the default `block_private_network=True`**
- **Content truncation**: to prevent long pages from exhausting the context window, set `max_content_length` to a reasonable value that matches the model window, for example 4000 to 20000 characters; if the LLM only needs a summary, it can further limit the result through `max_length`
- **Byte budget**: for large files such as oversized HTML pages or log pages, prefer relying on `max_response_bytes` to stop early at the network layer rather than downloading first and truncating later
- **Cache strategy**: enable `enable_cache=True` for hot documents, changelogs, or status pages, and size `cache_max_bytes` according to the average page size; note that an overly long TTL may return stale content, and `cached=true` can be used for downstream handling
- **Domain policy**: use `allowed_domains` when the Agent should be restricted to trusted sites; use `blocked_domains` to exclude noisy or high-risk sites; both can be combined, with the **blocklist taking precedence**
- **Using it with MCP tools**: when a dedicated MCP fetching tool is available with capabilities such as authentication, JavaScript rendering, or form submission, prefer the MCP tool; `WebFetchTool` is better suited for quickly reading public documentation-style pages that do not require login
- **Custom HTTP behavior**: when you need to integrate with a corporate proxy, mTLS, or a reused connection pool, configure `proxy` or inject `http_client`

### Complete Example

The complete WebFetchTool usage example is available at: [examples/webfetch_tool/run_agent.py](../../../examples/webfetch_tool/run_agent.py)

The example covers the following scenarios:

- Baseline: default HTTP behavior plus default SSRF behavior
- Per-call override with `max_length`: the LLM further limits the returned text length in the call arguments
- LRU cache hit: fetch the same URL twice in succession, with `cached=true` on the second response
- Allowlist rejection: a host outside the allowlist is rejected with `BLOCKED_URL`
- Blocklist rejection: a host in the blocklist is rejected with `BLOCKED_URL`

## WebSearchTool

`WebSearchTool` is the built-in **public web search tool** in the trpc-agent-python framework. When an Agent needs to answer questions about "latest developments / version numbers / events / definitions / factual topics" that fall beyond the model's knowledge cutoff, it can use this tool to call mainstream search-engine retrieval APIs, obtain structured results containing titles, URLs, and snippets, and then list all citations in a `Sources:` section as Markdown hyperlinks according to the prescribed format.

This tool uses a **pluggable provider** design and currently includes two built-in backends:

- **`duckduckgo` (default)**: DuckDuckGo Instant Answer API, which **does not require an API key**. It returns DDG-curated instant answers / abstracts / definitions and related topics, making it suitable for encyclopedia, definition, and fact-based queries. Note that it does not return full real-time web search results, but rather DDG's curated result set
- **`google`**: Google Custom Search (CSE) JSON API, which requires `api_key` and `engine_id` (that is, the CSE `cx`). It returns real public web search results and supports native CSE parameters such as `siteSearch`, `hl` (language), `safe` (SafeSearch), and `dateRestrict` (freshness)

On top of that, `WebSearchTool` also provides **domain allowlist/blocklist filtering, URL normalization and deduplication, result truncation, mandatory citation-policy injection, and HTTP connection-pool reuse**, helping you integrate network retrieval into LLM Agents in a stable and controllable way for production environments.

### Features

- **Dual provider support**: switch between `duckduckgo` (keyless, suitable for definitions/encyclopedia/facts) and `google` (requires CSE API credentials and supports real public web search) through the `provider` parameter, while exposing a consistent `FunctionDeclaration` to the LLM
- **Domain allowlist/blocklist**: the LLM may fill in `allowed_domains` or `blocked_domains` at call time (the two are mutually exclusive). The tool performs **subdomain-aware** matching (`www.` is stripped, and `python.org` also matches `docs.python.org`). For Google, a single allowed domain takes the server-side `siteSearch` fast path, while multiple domains automatically fall back to client-side filtering
- **URL normalization and deduplication**: `dedup_urls=True` (default) merges duplicate hits by a normalized scheme/host/path key, preventing the same source from appearing multiple times in the `Sources:` section. Set it to `False` to preserve the raw recall list for downstream re-rankers, diversified sampling, or offline evaluation
- **Result truncation**: `results_num`, `snippet_len`, and `title_len` control the number of returned items and the character limits for each snippet and title. All parameters are clamped to `[1, _MAX_*]` to avoid misconfiguration that would exceed the context window. The LLM can further control the returned count at call time via the `count` parameter
- **Mandatory citation policy**: during `process_request`, the tool automatically appends instructions to the LLM and **strictly requires** that: (1) the final answer must end with `Sources:` section listing the URLs returned by the tool in `[Title](URL)` format; (2) the Agent must not fabricate URLs; and (3) for "latest/recent" queries, the **current year and month** must be included in the search input to reduce stale-year hallucinations
- **Provider-native parameter passthrough**: `ddg_extra_params` and `google_extra_params` let you pin provider-specific advanced parameters at the Agent level, such as Google CSE's `safe`, `dateRestrict`, `gl`, and `cr`, so they are automatically included in every tool invocation without exposing additional fields in the `FunctionDeclaration`
- **Shared `httpx` connection pool**: by passing a prebuilt `httpx.AsyncClient` through the `http_client` constructor argument, you can reuse the connection pool across multiple Agents or multiple invocations. The caller is responsible for managing its lifecycle (the tool will not call `aclose`)
- **Structured output**: always returns a `WebSearchResult` containing `query`, `provider`, `results: List[{title, url, snippet}]`, and `summary`, making it easier both for the LLM to compose citations and for downstream re-ranking or RAG pipelines

### WebSearchTool Parameters

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `provider` | `Literal["duckduckgo", "google"]` | `"duckduckgo"` | Search backend. `google` requires both `api_key` and `engine_id`; otherwise a not-configured message is returned at call time |
| `api_key` | `Optional[str]` | `None` | Google CSE API key. If omitted, falls back to the `GOOGLE_CSE_API_KEY` environment variable |
| `engine_id` | `Optional[str]` | `None` | Google CSE engine ID (that is, `cx`). If omitted, falls back to the `GOOGLE_CSE_ENGINE_ID` environment variable |
| `base_url` | `Optional[str]` | provider default | Override the provider's API base URL (mainly for testing or proxies) |
| `user_agent` | `str` | `"trpc-agent-python-websearch/1.0"` | HTTP `User-Agent` header, used to help downstream systems distinguish request traffic in logs |
| `proxy` | `Optional[str]` | `None` | Optional HTTP proxy URL, passed directly to `httpx` |
| `lang` | `Optional[str]` | `None` | Default Google CSE language (maps to `hl`). Ignored by DDG. Can be overridden by the call-time `lang` parameter |
| `http_client` | `Optional[httpx.AsyncClient]` | `None` | Optional prebuilt `httpx.AsyncClient` for connection-pool reuse (its lifecycle is managed by the caller) |
| `results_num` | `int` | `5` | Default upper bound on returned results, clamped to `[1, 10]`; can be overridden by the call-time `count` parameter |
| `snippet_len` | `int` | `300` | Character limit for each `snippet`, clamped to `[1, 1000]` |
| `title_len` | `int` | `100` | Character limit for each `title`, clamped to `[1, 200]` |
| `timeout` | `float` | `15.0` | HTTP timeout in seconds |
| `dedup_urls` | `bool` | `True` | Whether to merge duplicate URLs by normalized key; if `False`, the original hit order is preserved |
| `ddg_extra_params` | `Optional[dict]` | `None` | Additional query parameters passed through to DDG |
| `google_extra_params` | `Optional[dict]` | `None` | Additional query parameters passed through to Google CSE, such as `{"safe": "active"}`, `{"dateRestrict": "m6"}`, or `{"gl": "us"}` |
| `filters_name` | `Optional[List[str]]` | `None` | Names of associated filters, passed through to `BaseTool` |
| `filters` | `Optional[List[BaseFilter]]` | `None` | Filter instances injected directly, passed through to `BaseTool` |

**LLM call-time parameters** (filled by the LLM at invocation time, not constructor arguments):

| Parameter | Type | Required | Description |
|------|------|------|------|
| `query` | `string` | Yes | Search keywords, at least 2 characters. For topics such as "latest" or "version number", it is recommended to explicitly include the year or version number |
| `count` | `integer` | No | Upper bound on the number of results returned in this call, clamped to `1-10`; defaults to the tool-level `results_num` |
| `allowed_domains` | `array[string]` | No | Domain allowlist (host only, subdomain-aware, with `www.` stripped automatically); mutually exclusive with `blocked_domains` |
| `blocked_domains` | `array[string]` | No | Domain blocklist, using the same matching rules as `allowed_domains`; mutually exclusive with `allowed_domains` |
| `lang` | `string` | No | Only effective for Google CSE (maps to `hl`). Ignored by DDG. Overrides the tool-level `lang` |

**`WebSearchResult` return fields**:

| Field | Type | Description |
|------|------|------|
| `query` | `str` | Query term used for this search (echoed back verbatim) |
| `provider` | `"duckduckgo" \| "google"` | Provider actually used |
| `results` | `List[SearchHit]` | Structured hit list, where each item contains `title`, `url`, and `snippet` |
| `summary` | `str` | Aggregated summary from DDG instant answers / abstracts / definitions. For Google, this field is also populated when spelling correction or API errors occur |

When call-time arguments are invalid, such as passing both `allowed_domains` and `blocked_domains`, or when `query` is too short, or when an HTTP error occurs, the tool returns a structured error object such as `{"error": "INVALID_ARGS: ..."}` or `{"error": "HTTP_ERROR: ..."}`, making it easier for the LLM to perform graceful fallback handling.

### Usage

#### Build a WebSearchTool Agent

Create a WebSearchTool Agent (using the default DuckDuckGo provider as an example, which runs **without any API key**):

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel, OpenAIModel
from trpc_agent_sdk.tools import WebSearchTool

from .config import get_model_config
from .prompts import INSTRUCTION


def _create_model() -> LLMModel:
    """Create the LLM model."""
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def create_ddg_agent() -> LlmAgent:
    """Create a WebSearchTool Agent based on DuckDuckGo."""
    web_search = WebSearchTool(
        provider="duckduckgo",     # Keyless; suitable for definitions, encyclopedia, and fact-based queries
        results_num=3,             # Return up to 3 results by default
        snippet_len=300,           # Each snippet is capped at 300 characters
        title_len=80,              # Each title is capped at 80 characters
        timeout=10.0,
        # dedup_urls=False,                    # Disable URL normalization/deduplication and keep the raw recall list
        # ddg_extra_params={"region": "us-en"},  # Pass through DDG-native parameters
    )
    return LlmAgent(
        name="ddg_research_assistant",
        description="Web research assistant powered by DuckDuckGo Instant Answers.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[web_search],
    )
```

**Switch to Google Custom Search**: when you need real public web search results, or need freshness, language, or SafeSearch controls, switch to `provider="google"`. Google requires an API key from the [Google Cloud Console](https://developers.google.com/custom-search/v1/overview) and a `cx` value from a [Programmable Search Engine](https://programmablesearchengine.google.com/):

```python
import httpx

from .config import get_google_cse_config, get_http_proxy

# Created and owned by the application. Call await shared_client.aclose() before exit.
shared_client = httpx.AsyncClient(
    timeout=15.0,
    limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
)


def create_google_agent() -> LlmAgent:
    """Create a WebSearchTool Agent based on Google Custom Search."""
    api_key, engine_id = get_google_cse_config()
    web_search = WebSearchTool(
        provider="google", # Use Google Custom Search
        api_key=api_key, # Google CSE API key
        engine_id=engine_id, # Google CSE engine ID
        user_agent="trpc-agent-python-websearch-demo/1.0 (+google-cse)", # User-Agent header
        proxy=get_http_proxy(),                # Optional outbound proxy
        lang="en", # Language setting
        http_client=shared_client,             # Reuse the connection pool; the caller must close it explicitly
        results_num=3, # Number of results
        snippet_len=240, # Character limit for each snippet
        title_len=80, # Character limit for each title
        timeout=15.0, # Timeout
        dedup_urls=True, # Enable URL normalization and deduplication
        google_extra_params={"safe": "active"},       # Enable Google SafeSearch
        # google_extra_params={"dateRestrict": "m6"}, # Keep only results indexed within the last 6 months
    )
    return LlmAgent(
        name="google_research_assistant",
        description="Web research assistant powered by Google Custom Search (SafeSearch on).",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[web_search],
    )
```

> **Note**:
> - `allowed_domains` and `blocked_domains` are filled in by the **LLM in the call arguments** rather than the constructor. The LLM may decide whether to use them based on the user prompt. The two are mutually exclusive, and passing both returns `INVALID_ARGS`
> - When an external `http_client` is provided, `WebSearchTool` will **not** call `aclose()` for you. The caller must explicitly close it in the **same event loop** to avoid `Unclosed client` warnings
> - Even when an external client is reused, the tool still force-applies the constructor's `timeout` and `user_agent` on every `GET` request, ensuring that the Agent-level constraints always remain in effect
> - Other commonly used Google CSE passthrough parameters include `gl` (geographic bias), `cr` (country restriction), `filter`, and `sort`. For DuckDuckGo, parameters such as `region` and `kl` can be passed through via `ddg_extra_params`

#### Drive the Agent and Print Tool Events

`run_agent.py` drives the Agent by executing each `(label, query)` scenario in sequence and extracting `function_call` / `function_response` from the event stream so that tool invocations can be observed directly:

```python
import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part

load_dotenv()

APP_NAME = "websearch_agent_demo"
USER_ID = "demo_user"


async def _run_one_query(runner: Runner, *, label: str, query: str) -> None:
    """Drive a single user query through ``runner`` and pretty-print events."""
    session_id = str(uuid.uuid4())
    await runner.session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
        state={"user_name": USER_ID},
    )

    print(f"\n========== {label} ==========")
    print(f"📝 User: {query}")
    print("🤖 Assistant: ", end="", flush=True)

    user_content = Content(parts=[Part.from_text(text=query)])
    async for event in runner.run_async(
            user_id=USER_ID,
            session_id=session_id,
            new_message=user_content,
    ):
        if not event.content or not event.content.parts:
            continue

        if event.partial:
            for part in event.content.parts:
                if part.text:
                    print(part.text, end="", flush=True)
            continue

        for part in event.content.parts:
            if part.thought:
                continue
            # Print tool calls
            if part.function_call:
                print(f"\n🔧 [Invoke Tool: {part.function_call.name}({part.function_call.args})]")
            # Print tool responses
            elif part.function_response:
                resp = part.function_response.response
                print(f"📊 [Tool Result: {resp}]") # Tool response

    print("\n" + "-" * 40)


async def _drive_agent(agent: LlmAgent, *, scenarios: list[tuple[str, str]]) -> None:
    """Drive the Agent through the scenarios."""
    runner = Runner(
        app_name=APP_NAME,
        agent=agent,
        session_service=InMemorySessionService(),
    )
    for label, query in scenarios:
        await _run_one_query(runner, label=label, query=query)


async def main() -> None:
    from agent.agent import ddg_agent

    await _drive_agent(ddg_agent, scenarios=[
        ("DuckDuckGo · plain lookup",
         "Look up the entity 'Python (programming language)' and summarise it in "
         "one paragraph. Use count=1.")
    ])


if __name__ == "__main__":
    asyncio.run(main())
```

After seeing the prompt above, the LLM automatically assembles a tool call similar to the following from the `FunctionDeclaration`:

```python
# function_call.args generated automatically by the LLM
{
    "query": "Python (programming language)",
    "count": 3,
    "allowed_domains": ["wikipedia.org"],
}
```

#### Example Output

**Example return value**:

When the call succeeds, the `WebSearchResult` inside `function_response` looks like this:

```python
{
    "query": "Python 3.13 release highlights",
    "provider": "google",
    "results": [
        {
            "title": "What's New In Python 3.13",
            "url": "https://docs.python.org/3/whatsnew/3.13.html",
            "snippet": "This article explains the new features in Python 3.13, compared to 3.12 ...",
        },
        {
            "title": "Python 3.13.0 Release Notes",
            "url": "https://www.python.org/downloads/release/python-3130/",
            "snippet": "Python 3.13 is the newest major release ...",
        },
    ],
    "summary": "",
}
```

For the DuckDuckGo provider, when an instant answer is hit, the `summary` field contains the aggregated summary text returned by DDG, such as a Wikipedia abstract or dictionary definition, which the LLM may cite directly. When DDG has no `Results`, the tool falls back to returning the DDG search-page URL as the only source, ensuring that the `Sources:` section is never empty.

**Error handling**: when arguments are invalid or an HTTP error occurs, the tool returns a structured error object:

```python
# Passing both the allowlist and blocklist
{"error": "INVALID_ARGS: cannot specify both allowed_domains and blocked_domains in the same request"}

# Query is too short
{"error": "INVALID_QUERY: query must be at least 2 characters"}

# Google CSE is not configured with api_key / engine_id
{"query": "...", "provider": "google", "results": [],
 "summary": "Google provider is not configured: set api_key + engine_id ..."}

# Network / HTTP error
{"error": "HTTP_ERROR: ConnectTimeout(...)", "provider": "google", "query": "..."}
```

It is recommended to specify in the Agent's `instruction` that when the tool returns an `error` field, the Agent should **repeat the reason for the error and offer a fallback option** to the user, such as retrying later, changing the query, or checking the domain allowlist, rather than fabricating content.

**Automatically injected citation policy**: `WebSearchTool.process_request` automatically calls `append_instructions` before each request and strictly requires the LLM to:

- Append a `Sources:` section at the end of the answer and list the URLs returned by the tool in `[Title](URL)` format
- **Never fabricate URLs** and cite only the URLs actually returned by the tool
- For "latest/recent/current" queries, include the **current month and year** in the search input to reduce stale-year hallucinations

You do not need to restate this logic in the user's own `instruction`; it takes effect automatically as long as `WebSearchTool` is mounted.

### Best Practices

- **Provider selection**: use the default `duckduckgo` provider when you need lightweight definition, encyclopedia, or fact-based lookups without an API key. Switch to `google` with CSE credentials when you need real public web search or controls over site, language, SafeSearch, or freshness
- **Result truncation**: to prevent long snippets from exceeding the context window, configure `snippet_len`, `title_len`, and `results_num` to match the model window. When the LLM only needs a small number of sources, it can further tighten the result set via the call-time `count` parameter
- **Domain strategy**: when the Agent should be restricted to trusted sites such as official documentation or a company website, explicitly instruct the LLM in the prompt to fill in `allowed_domains`. Use `blocked_domains` to exclude content farms or noisy domains. The two are mutually exclusive
- **Google multi-domain filtering**: Google CSE `siteSearch` accepts only a single value, so when multiple allowed or blocked domains are provided, the tool automatically falls back to client-side filtering. If you want a single domain to take the server-side fast path, constrain the prompt so that the LLM passes only one domain at a time
- **Deduplication toggle**: URL normalization and deduplication are enabled by default to avoid repeating the same source in the `Sources:` section. Set `dedup_urls=False` to preserve the raw recall list for downstream re-rankers, diversified sampling, or offline evaluation
- **Freshness control**: for Agents that answer "latest / what's new / today" queries, pin `google_extra_params={"dateRestrict": "m6"}` (last 6 months), `"m1"` (1 month), or `"d7"` (7 days) at the Agent level. This is more reliable than repeatedly emphasizing freshness in the prompt
- **Connection-pool reuse**: when multiple `WebSearchTool` instances are mounted in the same process or the tool is called at high frequency, pass a shared `httpx.AsyncClient` via `http_client` and explicitly `aclose()` it when the program exits
- **Credentials and proxies**: inject the Google CSE `api_key` and `engine_id` through environment variables (`GOOGLE_CSE_API_KEY` and `GOOGLE_CSE_ENGINE_ID`) rather than hard-coding them in source code. When a corporate outbound proxy is required, configure it through the `proxy` parameter
- **Using it with WebFetchTool**: `WebSearchTool` is used to "discover URLs", while `WebFetchTool` is used to "read full page content". When the LLM needs to closely read, summarize, or quote a specific search result, mount both tools on the Agent to form a two-stage workflow of "search -> close reading"
- **Using it with Knowledge/RAG**: when search results are used as real-time supplementary material in a RAG pipeline, refer to [examples/knowledge_with_searchtool_rag_agent](../../../examples/knowledge_with_searchtool_rag_agent)

### Complete Example

The complete WebSearchTool usage example is available at: [examples/websearch_tool/run_agent.py](../../../examples/websearch_tool/run_agent.py)

The example builds four independent Agents and covers the following scenarios:

- **DuckDuckGo baseline** (`ddg_agent`, `dedup_urls=True`): entity-name query + allowlist + blocklist
- **DuckDuckGo raw-hit mode** (`ddg_raw_agent`, `dedup_urls=False`): preserves the provider's raw recall list for downstream processing
- **Google baseline** (`google_agent`, `safe=active`): real public web search + server-side single-domain `siteSearch` + client-side multi-domain filtering + blocklist + per-call `lang` override
- **Google freshness Agent** (`google_raw_agent`, `dateRestrict=m6` + `dedup_urls=False`): keeps only results indexed within the last 6 months, suitable for "latest / what's new" queries
