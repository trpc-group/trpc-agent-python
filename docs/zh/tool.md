# Tools

Tool（工具）是 trpc_agent 中扩展 Agent 能力的核心机制。借助工具，Agent 可以调用自定义函数、对接第三方服务、执行数据处理等操作，从而突破纯文本推理的边界，与外部系统进行深度交互。

### 核心特性

- **多类型工具**：支持函数工具（Function Tools）、MCP 标准工具、Agent 工具、文件工具等多种工具类型
- **流式响应**：支持实时流式响应（Streaming Tools）和普通响应两种模式
- **并行执行**：工具调用支持并行执行以提升性能（`parallel_tool_calls=True`）
- **MCP 协议**：完整支持 STDIO、SSE、Streamable HTTP 三种传输方式
- **会话管理**：MCP 工具集支持自动会话健康检查与重连

## Agent 如何使用工具

Agent 通过以下步骤动态使用工具：
1. **推理**：LLM 分析指令和对话历史
2. **选择**：基于可用工具和描述选择合适的工具
3. **调用**：生成所需参数并触发工具执行
4. **观察**：接收工具返回的结果
5. **整合**：将工具输出融入后续推理过程


## 工具类型

| 类型 | 适用场景 | 开发方式 | 典型应用 |
|------|----------|----------|----------|
| [Function Tools](#function-tools) | 自定义业务逻辑、数据处理、API 调用 | 直接编写 Python 异步函数 | 天气查询、数据库操作、文件处理、计算工具 |
| [MCP Tools](#mcp-tools) | 集成第三方工具、跨进程工具调用、微服务架构 | 连接现有 MCP 服务器或创建新的 MCP 服务 | 外部 API 服务、数据库工具、文件系统操作 |
| [Tool Set](#toolset) | 需要一组同类工具处理的业务场景 | 将一类工具组合为 ToolSet | 需要访问 MCP Server 的所有 tool |
| [Agent Tools](#agent-tools) | 将 Agent 包装成工具供其他 Agent 调用 | 使用 AgentTool 包装 Agent | 翻译工具、内容处理 |
| [File Tools](#file-tools) | 文件操作和文本处理 | 使用 FileToolSet 或单独工具 | 读写文件、搜索、命令执行 |
| [LangChain Tools](#langchain-tools) | 复用 LangChain 生态工具 | 封装为异步函数并包装为 FunctionTool | 联网搜索（Tavily）等 |
| [Streaming Tools（流式工具）](#streaming-tools流式工具) | 实时预览长文本生成 | 使用 StreamingFunctionTool | 代码生成、文档写作 |
| [Agent Code Executor](#agent-code-executor) | 自动生成并执行代码场景、数据处理场景 | 配置 CodeExecutor | API 自动调用、表格数据处理 |
---

## Function Tools

Function Tool 是 trpc_agent 框架中最基础且常用的工具类型，它允许开发者将 Python 函数快速转换为 Agent 可以调用的工具。当框架提供的内置工具无法满足特定需求时，开发者可以通过 Function Tool 创建定制化功能。

trpc_agent 提供了多种创建 Function Tool 的方式，适应不同的开发场景和复杂度需求。

### 使用 Tool

#### 1. 直接包装函数

最简单的方式是直接使用`FunctionTool`类包装单个Python函数：

```python
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.agents import LlmAgent

async def get_weather(city: str) -> dict:
    """获取指定城市的天气信息

    Args:
        city: 城市名称，如 "Beijing"、"Shanghai"

    Returns:
        包含天气信息的字典，包括温度、天气状况和湿度
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

# 创建工具
weather_tool = FunctionTool(get_weather)

# 在Agent中使用
agent = LlmAgent(
    name="function_tool_demo_agent",
    model="deepseek-chat",
    description="An assistant demonstrating FunctionTool usage",
    instruction="你是一个天气查询助手...",
    tools=[weather_tool],
)
```

#### 2. 使用装饰器注册

通过`@register_tool`装饰器可以将函数注册到全局工具注册表，通过`get_tool`函数获取工具：

```python
from trpc_agent_sdk.tools import register_tool, get_tool
from trpc_agent_sdk.context import InvocationContext

@register_tool("get_session_info")
async def get_session_info(tool_context: InvocationContext) -> dict:
    """获取当前会话信息

    tool_context 参数由框架自动注入，调用时无需提供。

    Returns:
        当前会话的基本信息
    """
    session = tool_context.session
    return {
        "status": "success",
        "session_id": session.id,
        "user_id": session.user_id,
        "app_name": session.app_name,
    }

# 从注册表获取工具
session_tool = get_tool("get_session_info")
```

### 参数处理

#### 参数类型

Function Tool 支持**JSON 可序列化类型**作为参数，框架会根据函数签名中的类型注解自动生成 JSON Schema，供 LLM 理解参数结构。支持的类型如下：

| Python 类型 | JSON Schema 类型 | 说明 |
|---|---|---|
| `str` | `string` | 字符串 |
| `int` | `integer` | 整数 |
| `float` | `number` | 浮点数 |
| `bool` | `boolean` | 布尔值 |
| `list` | `array` | 列表 |
| `dict` | `object` | 字典 |
| `pydantic.BaseModel` | `object`（嵌套结构） | 支持嵌套模型，框架会递归解析字段及其 `description` |

> **注意**：建议避免为参数设置默认值，因为 LLM 目前不支持理解默认参数，可能导致该参数始终被忽略或填充不正确的值。

**基本类型示例：**

```python
async def calculate(operation: str, a: float, b: float) -> float:
    """执行数学计算

    Args:
        operation: 运算类型 (add, subtract, multiply, divide)
        a: 第一个数字
        b: 第二个数字

    Returns:
        计算结果
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


# Pydantic 模型类型（支持嵌套）

class City(BaseModel):
    """城市信息"""
    city: str = Field(..., description="城市名称")


class Address(BaseModel):
    """邮编查询的地址信息"""
    city: City = Field(..., description="城市信息")
    province: str = Field(..., description="省份名称")


class PostalCodeInfo(BaseModel):
    """邮编查询结果"""
    city: str = Field(..., description="城市名称")
    postal_code: str = Field(..., description="邮政编码")


def get_postal_code(addr: Address) -> PostalCodeInfo:
    """获取指定地址的邮政编码

    Args:
        addr: 包含城市和省份的 Address 对象

    Returns:
        包含邮政编码的 PostalCodeInfo 对象
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

#### 框架上下文参数

Function Tool 支持注入 `InvocationContext`上下文参数，用于访问会话状态、用户信息等：

```python
from trpc_agent_sdk.context import InvocationContext

@register_tool("get_session_info")
async def get_session_info(tool_context: InvocationContext) -> dict:
    """获取当前会话信息

    tool_context 参数由框架自动注入，调用时无需提供。

    Returns:
        当前会话的基本信息
    """
    session = tool_context.session
    return {
        "status": "success",
        "session_id": session.id,
        "user_id": session.user_id,
        "app_name": session.app_name,
    }
```

### 返回值处理

#### 推荐的返回类型

Function Tool 的首选返回类型是**字典（dict）**，这样可以提供结构化的信息给LLM。如果函数返回其他类型，框架会自动包装成字典，键名为"result"。

```python
# 推荐：返回字典
async def good_example(query: str) -> dict:
    """推荐的返回方式"""
    return {
        "status": "success",
        "result": f"处理了查询: {query}",
        "timestamp": "2024-01-01T12:00:00Z"
    }

# 可行：返回其他类型（会被自动包装）
async def ok_example(query: str) -> str:
    """会被包装成 {"result": "返回值"}"""
    return f"处理了查询: {query}"
```

#### 状态指示

在返回值中包含"status"字段可以帮助LLM理解操作结果：

```python
async def process_document(content: str) -> dict:
    """处理文档内容"""
    try:
        # 处理逻辑
        processed = content.upper()
        return {
            "status": "success",
            "processed_content": processed,
            "word_count": len(content.split())
        }
    except Exception as e:
        return {
            "status": "error",
            "error_message": f"处理失败: {str(e)}"
        }
```

### Docstring 和注释

函数的 docstring 会作为工具描述发送给 LLM，因此编写清晰、详细的 docstring 至关重要。以下是一个良好的示例：

```python
async def analyze_sentiment(text: str, language: str = "zh") -> dict:
    """分析文本情感倾向

    这个工具可以分析中文或英文文本的情感倾向，返回情感分类和置信度。

    Args:
        text: 要分析的文本内容，支持中英文
        language: 文本语言，支持 "zh"（中文）或 "en"（英文）

    Returns:
        包含情感分析结果的字典，包括：
        - sentiment: 情感分类（positive/negative/neutral）
        - confidence: 置信度（0.0-1.0）
        - details: 详细分析信息

    Example:
        分析结果示例：
        {
            "sentiment": "positive",
            "confidence": 0.85,
            "details": "文本表达了积极的情感"
        }
    """
    # 实现分析逻辑
    return {
        "sentiment": "positive",
        "confidence": 0.85,
        "details": f"分析了{language}文本: {text[:50]}..."
    }
```

### Function Tools 最佳实践

#### 1. 工具设计原则

- **单一职责**：每个工具只做一件事，保持功能聚焦
- **清晰命名**：使用描述性的函数名，如`get_weather`而不是`weather`
- **详细文档**：提供完整的docstring，包括参数说明和返回值示例

#### 2. 参数设计

- **类型明确**：为所有参数添加类型注解，框架据此生成 JSON Schema 供 LLM 理解
- **命名语义化**：使用完整、可读的参数名（如 `max_results` 而非 `num`），帮助 LLM 正确填充
- **避免默认值**：LLM 目前不支持理解默认参数，可能导致参数被忽略或填充错误
- **描述充分**：在 docstring 的 Args 中为每个参数提供说明和取值范围示例

```python
# 好的参数设计示例
async def search_products(query: str, category: str, max_results: int) -> dict:
    """搜索产品

    Args:
        query: 搜索关键词，如"蓝牙耳机"
        category: 产品分类，如"electronics"、"books"等
        max_results: 最大返回数量，建议1-20之间
    """
    pass

# 避免的设计
def search(q, cat=None, num=10):  # 参数名不清晰，有默认值
    pass
```

#### 3. 错误处理

- **不要抛出未捕获的异常**：未处理的异常会中断 Agent 执行流程，应在函数内部捕获并以结构化的错误信息返回
- **返回明确的错误状态**：通过 `status` 字段区分成功与失败，便于 LLM 判断下一步动作
- **提供可操作的错误信息**：错误描述应具体，帮助 LLM 理解失败原因并做出合理的后续决策

```python
async def divide_numbers(a: float, b: float) -> dict:
    """计算两个数的除法"""
    # 优先处理除零边界情况，返回明确的错误码便于 LLM 理解
    if b == 0:
        return {
            "status": "error",
            "error_message": "除数不能为零",
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
        # 捕获其他潜在异常（如溢出），统一以 error 状态返回
        return {
            "status": "error",
            "error_message": f"计算错误: {str(e)}"
        }
```


### Function Tools 完整示例

完整的 Function Tool 使用示例见：[examples/function_tools/run_agent.py](../../examples/function_tools/run_agent.py)



## MCP Tools

MCP Tools（Model Context Protocol Tools）是 trpc_agent 框架中用于集成外部 MCP 服务器工具的机制。通过MCP协议，Agent 可以调用其他进程提供的工具。

在trpc_agent中，主要的集成模式是：**Agent作为MCP客户端**，通过 `MCPToolset` 连接并使用外部MCP服务器提供的工具。

### 使用 MCPToolset

`MCPToolset`是 trpc_agent 中用于集成 MCP 工具的核心类。它可以连接到MCP服务器，自动发现可用工具，并将它们转换为 Agent 可以使用的工具，他的用法很简单，如下所示：

```python
import os
import sys

from trpc_agent_sdk.tools import MCPToolset
from trpc_agent_sdk.tools import McpStdioServerParameters
from trpc_agent_sdk.tools import StdioConnectionParams
from trpc_agent_sdk.agents import LlmAgent


class StdioMCPToolset(MCPToolset):
    """基于 stdio 的 MCP 工具集，自动启动 MCP 服务器子进程。"""

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
        # 取消注释以仅暴露指定工具，而非全部工具：
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

### 创建 MCP 服务器

trpc_agent 框架本身只负责 MCP 客户端侧（通过 `MCPToolset` 连接和调用工具）。MCP 服务器是独立的进程，负责对外提供工具能力。以下示例展示如何使用第三方 `mcp` 库的 `FastMCP` 快速创建一个 MCP 服务器，供 trpc_agent 的 Agent 连接使用。

```python
from mcp.server import FastMCP

app = FastMCP("simple-tools")


@app.tool()
async def get_weather(location: str) -> str:
    """获取指定地点的天气信息

    Args:
        location: 地点名称

    Returns:
        天气信息字符串
    """
    weather_info = {
        "Beijing": "Sunny, 15°C, humidity 45%",
        "Shanghai": "Cloudy, 18°C, humidity 65%",
        "Shenzhen": "Light rain, 25°C, humidity 80%",
    }
    return weather_info.get(location, f"Weather data for {location} is not available")


@app.tool()
async def calculate(operation: str, a: float, b: float) -> float:
    """执行基本数学运算

    Args:
        operation: 运算类型 (add, subtract, multiply, divide)
        a: 第一个数字
        b: 第二个数字

    Returns:
        计算结果
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
    # 取消注释以下其中一行以选择传输模式：
    app.run(transport="stdio")
    # app.run(transport="sse")
    # app.run(transport="streamable-http")
```

### 连接参数类型详解

#### stdio 类型的 StdioConnectionParams

用于连接本地进程的MCP服务器：

```python
import os
import sys

from trpc_agent_sdk.tools import MCPToolset
from trpc_agent_sdk.tools import McpStdioServerParameters
from trpc_agent_sdk.tools import StdioConnectionParams


class StdioMCPToolset(MCPToolset):
    """基于 stdio 的 MCP 工具集，自动启动 MCP 服务器子进程。"""

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
        # 配置 stdio 传输：以子进程方式启动 MCP 服务器
        stdio_server_params = McpStdioServerParameters(
            command=f"python{sys.version_info.major}.{sys.version_info.minor}",  # 启动命令
            args=[svr_file],  # 命令参数
            env=env,  # 环境变量（可选）
        )
        self._connection_params = StdioConnectionParams(
            server_params=stdio_server_params,
            timeout=5,  # 可选的超时时间，默认是 5s
        )
        # 取消注释以仅暴露指定工具，而非全部工具：
        # self._tool_filter = ["get_weather", "calculate"]
```

注意事项：
- 这里是传递是 `McpStdioServerParameters` 类型的参数，框架内部会将其转为 `StdioConnectionParams` 类型，如果用户直接使用 `StdioConnectionParams` 类型则需要换成如下方式
```python
        stdio_server_params = McpStdioServerParameters(
            command=f"python{sys.version_info.major}.{sys.version_info.minor}",
            args=[svr_file],
            env=env,
        )
        self._connection_params = StdioConnectionParams(
            server_params=stdio_server_params,
            timeout=5,  # 可选的超时时间，默认是 5s
        )
```
- 这里使用的是 `stdio` 模式，会自动启动 `mcp_server.py` 程序，无需用户自己启动

#### sse 类型 的 SseConnectionParams

用于连接远程 HTTP MCP 服务器：

```python
from trpc_agent_sdk.tools import MCPToolset
from trpc_agent_sdk.tools import SseConnectionParams


class SseMCPToolset(MCPToolset):
    """基于 SSE 的 MCP 工具集，通过 Server-Sent Events 连接远程 MCP 服务器。"""

    def __init__(self):
        super().__init__()
        self._connection_params = SseConnectionParams(
            # 必填，用户根据实际地址替换url
            url="http://localhost:8000/sse",
            # 可选的 HTTP 头
            headers={"Authorization": "Bearer token"},
            # 可选的超时时间，单位秒
            timeout=5,
            # 可选的 SSE 读取超时时间，单位秒
            sse_read_timeout=60 * 5,
        )
```
注意事项：
- 这里使用的是 `sse` 协议，如果测试需要主动启动 `mcp_server.py` 程序
- 启动的时候需要修改为：
```python
if __name__ == "__main__":
    # app.run(transport="stdio")
    app.run(transport="sse")
    # app.run(transport="streamable-http")
```

#### sse 类型 的 StreamableHTTPConnectionParams

用于连接远程 HTTP MCP 服务器：

```python
from trpc_agent_sdk.tools import MCPToolset
from trpc_agent_sdk.tools import StreamableHTTPConnectionParams


class StreamableHttpMCPToolset(MCPToolset):
    """基于 Streamable-HTTP 的 MCP 工具集，支持 HTTP 双向流式通信。"""

    def __init__(self):
        super().__init__()
        self._connection_params = StreamableHTTPConnectionParams(
            # 必填，用户根据实际地址替换url
            url="http://localhost:8000/mcp",
            # 可选的 HTTP 头
            headers={"Authorization": "Bearer token"},
            # 可选的超时时间，单位秒
            timeout=5,
            # 可选的 SSE 读取超时时间，单位秒
            sse_read_timeout=60 * 5,
            # 可选的关闭客户端会话，默认是 True
            terminate_on_close=True,
        )
```
注意事项：
- 这里使用的是 `streamable-http` 协议，如果测试需要主动启动 `mcp_server.py` 程序
- 启动的时候需要修改为：
```python
if __name__ == "__main__":
    # app.run(transport="stdio")
    # app.run(transport="sse")
    app.run(transport="streamable-http")
```

### 框架集成

你只需要像使用 Toolset 一样使用 MCPToolset 即可：

```python
from trpc_agent_sdk.agents import LlmAgent
from .tools import StdioMCPToolset


def create_agent() -> LlmAgent:
    """创建一个使用 MCP 工具的 Agent"""
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

### MCP Tools 完整示例

完整的MCP Tools使用示例见：[examples/mcp_tools/run_agent.py](../../examples/mcp_tools/run_agent.py)

### MCP Tools FAQ

#### 出现 `Attempted to exit a cancel scope that isn't the current tasks's current cancel scope`

这种错误是因为 mcp 官方库使用 AnyIO 库，当进入和退出发生在不同的任务（Task）上下文中。则会报这个错误，如果是运行 agent，报这个错误请执行：

```python

async def main():
    # ...
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)
    async for event in runner.run_async(user_id=user_id, session_id=current_session_id, new_message=user_content):
        # ...
    await runner.close()

```

如果还继续出错，请在程序执行入口执行：

```python
from trpc_agent_sdk.tools import patch_mcp_cancel_scope_exit_issue

patch_mcp_cancel_scope_exit_issue()

# your main function

```

## ToolSet

Tool Set 是 trpc_agent 框架中一组工具的集合

### 使用 ToolSet

当需要组织多个相关的工具时，可以使用**ToolSet（工具集）**。

#### 1. 创建ToolSet

创建自定义ToolSet需要继承`BaseToolSet`并实现必要的方法：

```python
from typing import List, Optional

from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import BaseTool, BaseToolSet, FunctionTool


class WeatherToolSet(BaseToolSet):
    """天气工具集，包含天气查询相关的所有工具"""

    def __init__(self):
        super().__init__()
        self.name = "weather_toolset"
        self.tools = []

    @override
    def initialize(self) -> None:
        """初始化工具集，创建所有天气相关工具"""
        super().initialize()
        self.tools = [
            FunctionTool(self.get_current_weather),
            FunctionTool(self.get_weather_forecast),
        ]

    @override
    async def get_tools(self, invocation_context: Optional[InvocationContext] = None) -> List[BaseTool]:
        """根据上下文返回可用工具"""
        # 无上下文时仅返回基础工具
        if not invocation_context:
            return self.tools[:1]

        # 从会话状态中获取用户类型，动态筛选工具
        user_type = invocation_context.session.state.get("user_type", "basic")

        if user_type == "vip":
            return self.tools  # VIP用户可以使用所有工具
        else:
            return self.tools[:1]  # 普通用户只能使用基础功能

    @override
    async def close(self) -> None:
        """清理资源"""
        # 关闭数据库连接、清理缓存等
        pass

    # 工具方法
    async def get_current_weather(self, city: str) -> dict:
        """获取当前天气

        Args:
            city: 城市名称，例如 "Beijing", "Shanghai"

        Returns:
            当前天气信息
        """
        # 模拟天气数据
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
        """获取天气预报

        Args:
            city: 城市名称
            days: 预报天数，默认3天

        Returns:
            天气预报信息
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

#### 2. 使用 ToolSet

ToolSet可以直接添加到Agent的工具列表中：

```python
from trpc_agent_sdk.agents import LlmAgent

from .tools import WeatherToolSet


def create_agent() -> LlmAgent:
    """创建带有天气工具集的Agent"""
    # 创建ToolSet实例并初始化
    weather_toolset = WeatherToolSet()
    weather_toolset.initialize()

    # 在Agent中使用，tools列表中既可以添加Tool，也可以添加ToolSet
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

#### 3. ToolSet 最佳实践

- **功能分组**：将相关功能的工具组织在同一个ToolSet中
- **权限控制**：利用`get_tools`方法实现基于用户的工具访问控制
- **资源管理**：在`close`方法中正确清理资源
- **初始化**：在`initialize`方法中完成工具的创建和配置
- **关闭**：在 Runner 运行完成后需要执行`close`方法优雅退出


### ToolSet 完整示例

完整的ToolSet使用示例见：[examples/toolsets/run_agent.py](../../examples/toolsets/run_agent.py)

---

## Agent Tools

trpc_agent 提供了 **AgentTool**，允许将 Agent 包装成 Tool，实现将一个 Agent 的输出，作为另一个 Agent 的输入。


### 使用 AgentTool

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.tools import AgentTool

# 创建专业的翻译Agent
translator = LlmAgent(
    name="translator",
    model=model,
    description="A professional text translation tool",
    instruction=TRANSLATOR_INSTRUCTION,
)

# 将Agent包装成Tool
translator_tool = AgentTool(agent=translator)

# 在主Agent中使用
main_agent = LlmAgent(
    name="content_processor",
    description="A content processing assistant that can invoke translation tools",
    model=model,
    instruction=MAIN_INSTRUCTION,
    tools=[translator_tool],
)
```


### AgentTool 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `agent` | AgentABC | 必需 | 需要被包装的 Agent |
| `skip_summarization` | bool | False | 是否需要跳过总结 |
| `filters_name` | list[str] | None | 关联的 filter 名称 |

### Agent Tools 完整示例

完整的 AgentTool 使用示例见：[examples/agent_tools/run_agent.py](../../examples/agent_tools/run_agent.py)

---

## File Tools

File Tools 是 trpc_agent 框架中提供的一组文件操作和文本处理工具集。这些工具为 Agent 提供了基础的读写、编辑、搜索、命令执行等能力，适用于各种文件操作场景。

### 工具概览

File Tools 包含以下 6 个工具：

1. **Read** - 读取文件内容
2. **Write** - 写入或追加文件内容
3. **Edit** - 替换文件中的文本块
4. **Grep** - 使用正则表达式搜索文件内容
5. **Bash** - 执行 shell 命令
6. **Glob** - 使用 glob 模式查找文件

### 使用 FileToolSet

最简单的方式是使用 `FileToolSet`，它会自动包含所有文件操作工具：

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.tools.file_tools import FileToolSet
from trpc_agent_sdk.models import OpenAIModel

# 创建工具集，指定工作目录（可选）
file_tools = FileToolSet(cwd="/path/to/workspace")

# 在 Agent 中使用
agent = LlmAgent(
    name="file_assistant",
    model=OpenAIModel(model_name="deepseek-v3-local-II"),
    instruction="你是一个文件操作助手，可以帮助用户读写、编辑文件。",
    tools=[file_tools],  # 添加文件工具集
)
```

#### 工作目录

所有工具共享同一个工作目录（`cwd`），相对路径会基于此目录解析：

```python
# 指定工作目录
file_tools = FileToolSet(cwd="/home/user/project")

# 工具调用时使用相对路径
# Read(path="config.ini") 会读取 /home/user/project/config.ini
```

如果不指定 `cwd`，工具会使用当前工作目录。

### 单独使用工具

如果需要单独使用某个工具，可以直接导入并逐个添加：

```python
from trpc_agent_sdk.agents.llm_agent import LlmAgent
from trpc_agent_sdk.tools.file_tools import ReadTool, WriteTool, EditTool, GrepTool, BashTool, GlobTool

# 创建工作目录
work_dir = "/path/to/workspace"

# 创建工具实例，每个工具都可以独立配置
read_tool = ReadTool(cwd=work_dir)      # Read file contents
write_tool = WriteTool(cwd=work_dir)     # Write or append to files
edit_tool = EditTool(cwd=work_dir)       # Replace text blocks in files
grep_tool = GrepTool(cwd=work_dir)       # Search for patterns using regex
bash_tool = BashTool(cwd=work_dir)       # Execute shell commands
glob_tool = GlobTool(cwd=work_dir)       # Find files matching glob patterns

# 在 Agent 中逐个添加工具
agent = LlmAgent(
    name="file_assistant",
    description="File operations assistant with file operation tools",  # Agent 描述信息
    model=_create_model(),
    instruction=INSTRUCTION,
    tools=[read_tool, write_tool, edit_tool, grep_tool, bash_tool, glob_tool],
)
```

这种方式可以灵活选择需要的工具，也可以为不同工具配置不同的工作目录。

### 工具详解

#### 1. Read Tool

读取文件内容，支持读取整个文件或指定行范围。

**功能特性：**
- 读取整个文件
- 读取指定行范围（start_line 到 end_line）
- 自动检测文件编码
- 支持大文件（有行数限制）

**使用示例：**
```python
# Agent 会自动调用，参数示例：
# Read(path="config.ini")  # 读取整个文件
# Read(path="app.py", start_line=10, end_line=20)  # 读取第10-20行
```

**返回格式：**
```python
{
    "success": True,
    "content": "文件内容...",
    "total_lines": 100,
    "read_range": "1-100"  # 或 "10-20"
}
```

#### 2. Write Tool

写入或追加内容到文件。

**功能特性：**
- 写入新文件
- 覆盖现有文件
- 追加内容到文件末尾
- 自动创建目录

**使用示例：**
```python
# Agent 会自动调用，参数示例：
# Write(path="output.txt", content="Hello, World!\n")  # 写入新文件
# Write(path="log.txt", content="New log entry\n", append=True)  # 追加内容
```

**返回格式：**
```python
{
    "success": True,
    "message": "SUCCESS: file output.txt written to successfully (13 bytes)",
    "path": "/path/to/output.txt"
}
```

#### 3. Edit Tool

替换文件中的文本块，支持精确匹配和容差匹配。

**功能特性：**
- 精确文本块替换
- 支持多行文本替换
- 空白字符容差（空格/制表符）
- 相似度提示（当找不到精确匹配时）

**使用示例：**
```python
# Agent 会自动调用，参数示例：
# Edit(
#     path="config.ini",
#     old_string="host=localhost",
#     new_string="host=production-server"
# )
```

**返回格式：**
```python
{
    "success": True,
    "message": "SUCCESS: file config.ini modified successfully",
    "line_range": "5-5",
    "changed_line_ranges": [(5, 5)]
}
```

#### 4. Grep Tool

使用正则表达式搜索文件内容。

**功能特性：**
- 单文件或目录搜索
- 正则表达式支持
- 大小写敏感/不敏感选项
- 结果数量限制

**使用示例：**
```python
# Agent 会自动调用，参数示例：
# Grep(pattern="def.*function", path="src/", case_sensitive=False)
# Grep(pattern="TODO|FIXME", path=".", max_results=50)
```

**返回格式：**
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

执行 shell 命令。

**功能特性：**
- 执行任意 shell 命令
- 支持管道和重定向
- 超时控制（默认 300 秒）
- 安全限制（工作目录外命令需在白名单中）

**使用示例：**
```python
# Agent 会自动调用，参数示例：
# Bash(command="ls -la", cwd="src/")
# Bash(command="git status", timeout=60)
# Bash(command="find . -name '*.py' | head -10")
```

**安全限制：**
- 工作目录内的命令无限制
- 工作目录外的命令限制在白名单：`ls`, `pwd`, `cat`, `grep`, `find`, `head`, `tail`, `wc`, `echo`

**返回格式：**
```python
{
    "success": True,
    "stdout": "命令输出...",
    "stderr": "",
    "return_code": 0,
    "command": "ls -la",
    "cwd": "/path/to/workspace"
}
```

#### 6. Glob Tool

使用 glob 模式查找文件。

**功能特性：**
- 支持标准 glob 模式
- 递归搜索（`**`）
- 大括号展开（`*.{py,js,go}`）
- 结果数量限制（默认 1000）

**使用示例：**
```python
# Agent 会自动调用，参数示例：
# Glob(pattern="*.txt")  # 查找所有 .txt 文件
# Glob(pattern="**/*.py")  # 递归查找所有 Python 文件
# Glob(pattern="**/*.{py,js}")  # 查找 Python 和 JavaScript 文件
```

**返回格式：**
```python
{
    "success": True,
    "matches": ["/path/to/file1.txt", "/path/to/file2.txt"],
    "count": 2,
    "truncated": False,
    "pattern": "*.txt"
}
```

### File Tools 完整示例

完整的 File Tools 使用示例见：[examples/file_tools/run_agent.py](../../examples/file_tools/run_agent.py)

### File Tools 最佳实践

#### 1. 工作目录管理

建议为每个项目或任务指定独立的工作目录：

```python
# 为不同项目使用不同目录
project_a_tools = FileToolSet(cwd="/path/to/project_a")
project_b_tools = FileToolSet(cwd="/path/to/project_b")
```

#### 2. 安全考虑

- **Bash Tool** 在工作目录外有安全限制，只允许白名单命令
- 避免让 Agent 执行危险命令（如 `rm -rf /`）
- 在生产环境中考虑更严格的安全策略

#### 3. 文件大小限制

- **Read Tool** 有文件大小和行数限制（默认最大 10MB，10000 行）
- 对于大文件，考虑使用 **Grep Tool** 进行搜索而不是完整读取

#### 4. 错误处理

所有工具都会返回包含 `success` 字段的结果：

```python
# 成功时
{
    "success": True,
    ...
}

# 失败时
{
    "success": False,
    "error": "错误信息..."
}
```

Agent 可以根据 `success` 字段判断操作是否成功，并采取相应行动。

完整的 File Tools 使用示例见：[examples/file_tools/agent/agent.py](../../examples/file_tools/agent/agent.py)

示例代码展示了如何：
- 逐个导入和创建工具实例
- 为每个工具配置工作目录
- 在 Agent 中逐个添加工具
- 运行文件操作任务

---

## LangChain Tools

LangChain Tool 允许你在 trpc_agent 中复用 LangChain 社区或官方生态的工具能力。本文以 Tavily 搜索为例，演示如何将 `langchain_tavily.TavilySearch` 作为工具接入到 `LlmAgent`。

trpc_agent 提供与 Function Tool 一致的开发体验：将功能封装为异步函数 → 包装为 `FunctionTool` → 注入到 `LlmAgent`。

### 使用 Tool

#### 1. Tavily 工具最小接入

```python
# examples/langchain_tools/agent/tools.py
from typing import Any

from langchain_tavily import TavilySearch


async def tavily_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """基于 Tavily 的联网搜索

    Args:
        query: 搜索查询
        max_results: 返回结果条数上限

    Returns:
        结构化的搜索结果，包含命中列表与计数
    """
    try:
        # 实例化 TavilySearch，传入最大结果数
        tool = TavilySearch(max_results=max_results)
        # 异步调用搜索，非阻塞
        res = await tool.ainvoke(query)

        # 兼容不同版本的返回格式：dict 带 "results" 键 / 直接返回 list / 其他
        if isinstance(res, dict) and "results" in res:
            items = res["results"]
        elif isinstance(res, list):
            items = res
        else:
            items = []

        # 统一返回结构化结果
        return {
            "status": "success",
            "query": query,
            "result_count": len(items),
            "results": items,
        }
    except Exception as e:  # pylint: disable=broad-except
        # 捕获所有异常，返回错误信息而非抛出，避免中断对话流程
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
    """创建模型实例"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent() -> LlmAgent:
    """创建集成 LangChain Tavily 搜索工具的 Agent"""
    agent = LlmAgent(
        name="langchain_tavily_agent",
        description="An assistant integrated with LangChain Tavily search tool",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[FunctionTool(tavily_search)],
    )
    return agent


# 模块级别导出 root_agent，供 Runner 直接引用
root_agent = create_agent()
```

#### 2. 调用模式说明

- Tavily 官方包为 `langchain_tavily`：推荐类 `TavilySearch`
- 支持 `.invoke/.ainvoke` 两种模式：
  - 同步：`invoke`（会阻塞当前线程）
  - 异步：`ainvoke`（推荐，非阻塞）

### 参数处理

#### 参数类型

与 Function Tool 一致，推荐使用 JSON 可序列化的参数。示例中 `query: str`、`max_results: int` 均为简单类型，便于 LLM 正确填充。

```python
async def tavily_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """基于 Tavily 的联网搜索"""
    tool = TavilySearch(max_results=max_results)
    res = await tool.ainvoke(query)
    # 兼容不同版本返回格式
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

#### 框架上下文参数

如需访问会话状态或上下文，按需添加 `tool_context: InvocationContext`（可与业务参数并存，框架自动注入）：

```python
from trpc_agent_sdk.context import InvocationContext

async def tavily_search(query: str, tool_context: InvocationContext, max_results: int = 5) -> dict[str, Any]:
    # 可使用 tool_context.session / tool_context.state 等
    tool = TavilySearch(max_results=max_results)
    res = await tool.ainvoke(query)
    # 兼容不同版本返回格式
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

### 返回值处理

#### 推荐的返回类型

返回字典（dict）以便 LLM 消化结构化信息。示例统一返回：

```python
return {
    "status": "success",
    "query": query,
    "result_count": len(items),
    "results": items,
}
```

#### 状态指示

当发生异常时，返回 `status=error` 与 `error_message` 有助于 LLM 采取降级策略：

```python
try:
    tool = TavilySearch(max_results=max_results)
    res = await tool.ainvoke(query)
    # ... 正常处理逻辑 ...
    return {"status": "success", "query": query, "result_count": len(items), "results": items}
except Exception as e:  # pylint: disable=broad-except
    return {"status": "error", "error_message": str(e)}
```

### Docstring 和注释

工具函数的 docstring 会作为工具描述暴露给模型。建议包含：用途、参数、返回示例，帮助模型更好地对齐预期。

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

### LangChain Tools 最佳实践

#### 1. 工具设计原则

- **单一职责**：每个工具只聚焦一项能力（如搜索、翻译），避免功能耦合
- **清晰命名**：函数名应直接表达意图（如 `tavily_search`），让模型一目了然
- **完整文档**：在 docstring 中明确说明用途、参数含义、返回结构及边界情况

#### 2. 参数设计

- 保持参数扁平，避免深层嵌套的复杂结构
- 使用语义明确的参数名（如 `query`、`max_results`），降低 LLM 误填概率
- 为可选参数提供合理的默认值，减少调用时的必填项

#### 3. 错误处理

- 务必捕获第三方调用可能抛出的异常（网络超时、鉴权失败等）
- 以结构化格式返回错误信息（如 `{"status": "error", "error_message": ...}`），而非直接抛出异常，确保对话流程不被中断

### LangChain Tools 完整示例

完整的使用示例见：[examples/langchain_tools/run_agent.py](../../examples/langchain_tools/run_agent.py)

更多 LangChain Tool 的用法，可以参考：[LangChain Tool](https://python.langchain.com/docs/integrations/tools/)

---

## Streaming Tools（流式工具）

流式工具让你能够**实时看到 AI 正在生成的工具参数**，而不用等待全部生成完毕。这对于代码生成、文档写作等长文本场景特别有用。

---

### 为什么需要流式工具？

传统工具调用流程：
```
用户请求 → AI 思考 → [漫长等待] → 完整参数返回 → 执行
```

流式工具调用流程：
```
用户请求 → AI 思考 → 边生成边返回 → 实时预览 → 执行
```

**典型场景**：
- 📝 **代码生成**：实时预览正在生成的代码
- 📄 **文档写作**：看到内容逐步生成
- 🔍 **长文本处理**：减少等待焦虑，提前发现问题

---

### 底层原理：模型的流式输出格式

在深入使用之前，了解底层原理有助于更好地理解和调试。

#### OpenAI API 流式输出

当 LLM 调用工具时，OpenAI 通过多个 chunk 逐步返回参数：

```
请求: "请帮我创建一个 test.txt 文件，内容是 Hello World"

↓ 模型开始流式输出工具调用 ↓

Chunk 1: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_abc123","function":{"name":"write_file","arguments":"{"}}]}}]}
Chunk 2: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\"path\":"}}]}}]}
Chunk 3: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\"test.txt\""}}]}}]}
Chunk 4: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":",\"content\":"}}]}}]}
Chunk 5: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\"Hello"}}]}}]}
Chunk 6: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":" World\""}}]}}]}
Chunk 7: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"}"}}]}}]}
Chunk 8: {"choices":[{"finish_reason":"tool_calls"}]}
```

**关键字段解析**：

| 字段 | 说明 | 示例 |
|------|------|------|
| `delta.tool_calls[].index` | 工具调用索引（支持多工具并行） | `0` |
| `delta.tool_calls[].id` | 工具调用唯一 ID（仅首个 chunk） | `"call_abc123"` |
| `delta.tool_calls[].function.name` | 工具名称（仅首个 chunk） | `"write_file"` |
| `delta.tool_calls[].function.arguments` | **参数增量字符串** | `"\"path\":"` |

**参数累积过程**：

```
Chunk 1: arguments = "{"
Chunk 2: arguments = "{" + "\"path\":" = "{\"path\":"
Chunk 3: arguments = "{\"path\":" + "\"test.txt\"" = "{\"path\":\"test.txt\""
...
最终:   arguments = "{\"path\":\"test.txt\",\"content\":\"Hello World\"}"
```

#### Anthropic Claude 流式输出

Claude 使用事件驱动的流式格式：

```
Event 1: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_01","name":"write_file","input":{}}}
Event 2: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\""}}
Event 3: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"path\":"}}
Event 4: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"\"test.txt\""}}
...
Event 8: {"type":"content_block_stop","index":0}
Event 9: {"type":"message_delta","delta":{"stop_reason":"tool_use"}}
```

#### 框架统一处理

TRPC Agent 框架将不同模型的输出统一转换为内部格式：

```
┌─────────────────────────────────────────────────────────────────┐
│  OpenAI: delta.tool_calls[].function.arguments                  │
│  Anthropic: content_block_delta + input_json_delta              │
└───────────────────────────┬─────────────────────────────────────┘
                            ↓ 框架统一转换
┌─────────────────────────────────────────────────────────────────┐
│  function_call.args = {                                         │
│      "tool_streaming_args": "本次增量内容"                        │
│  }                                                              │
└─────────────────────────────────────────────────────────────────┘
```

这样你在消费事件时，无需关心底层是 OpenAI 还是 Claude，统一使用 `tool_streaming_args` 获取增量内容。

---

### 快速上手

####使用 `StreamingFunctionTool` 包装

```python
from trpc_agent_sdk.tools import StreamingFunctionTool

"""Agent 工具模块。

提供两种工具类型：
  1. StreamingFunctionTool：支持流式参数传输，适用于大文本内容。
  2. FunctionTool：标准同步工具，适用于简单查询。
"""


def write_file(path: str, content: str) -> dict:
    """写入内容到文件（流式）。

    Args:
        path: 要写入的文件路径。
        content: 要写入的内容。

    Returns:
        包含成功状态、路径和内容大小的字典。
    """
    print(f"\n📄 Writing to {path}...")
    print(f"Content: {content[:100]}...")
    return {"success": True, "path": path, "size": len(content)}

# 创建流式工具
streaming_tool = StreamingFunctionTool(write_file)
```

#### 集成到 Agent

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel, OpenAIModel
from trpc_agent_sdk.tools import FunctionTool, StreamingFunctionTool

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import get_file_info, write_file


def _create_model() -> LLMModel:
    """创建模型实例"""
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def create_agent() -> LlmAgent:
    """创建带有流式和标准工具的 Agent。

    工具列表：
      - write_file: StreamingFunctionTool，流式写入文件。
      - get_file_info: FunctionTool，查询文件信息。
    """
    return LlmAgent(
        name="streaming_tool_demo_agent",
        description="An assistant demonstrating streaming tool usage",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[
            StreamingFunctionTool(write_file),  # 流式工具
            FunctionTool(get_file_info),        # 普通工具
        ],
    )


root_agent = create_agent()
```

#### 运行并处理事件

```python
import asyncio
import uuid

from dotenv import load_dotenv

from trpc_agent_sdk.models import constants as const
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part

# 加载环境变量
load_dotenv()


async def run_streaming_tool_agent():
    """运行流式工具演示 Agent"""

    app_name = "streaming_tool_demo"

    # 导入 Agent
    from agent.agent import root_agent

    # 创建会话服务和运行器
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"

    # 演示查询列表
    demo_queries = [
        "请帮我创建一个 Python 脚本 hello.py，实现简单的计算器功能",
    ]

    for query in demo_queries:
        current_session_id = str(uuid.uuid4())

        # 创建会话
        await session_service.create_session(
            app_name=app_name,
            user_id=user_id,
            session_id=current_session_id,
        )

        print(f"🆔 Session ID: {current_session_id[:8]}...")
        print(f"📝 User: {query}")
        print("🤖 Assistant: ", end="", flush=True)

        user_content = Content(parts=[Part.from_text(text=query)])

        # 用于累积流式内容
        accumulated_content = ""

        async for event in runner.run_async(
            user_id=user_id,
            session_id=current_session_id,
            new_message=user_content,
        ):
            if not event.content or not event.content.parts:
                continue

            # 🔥 流式工具调用事件 - 参数正在生成中
            if event.is_streaming_tool_call():
                for part in event.content.parts:
                    if part.function_call:
                        # 获取增量内容
                        delta = part.function_call.args.get(const.TOOL_STREAMING_ARGS, "")
                        accumulated_content += delta
                        print(f"⏳ Generated {len(accumulated_content)} chars...", end="\r")
                continue

            # 流式文本输出
            if event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                continue

            # ✅ 完整的工具调用 - 参数生成完毕
            for part in event.content.parts:
                if part.thought:
                    continue
                if part.function_call:
                    print(f"\n✅ Code generation complete!")
                    accumulated_content = ""  # 重置累积
                elif part.function_response:
                    print(f"📊 [Tool Result: {part.function_response.response}]")
                elif part.text:
                    print(f"\n💬 {part.text}")

        print("\n" + "-" * 40)

    # 关闭运行器
    await runner.close()


if __name__ == "__main__":
    asyncio.run(run_streaming_tool_agent())
```

---

### 创建流式工具的多种方式

TRPC Agent 提供了多种创建流式工具的方式，适用于不同场景：

#### 1. 包装同步函数

```python
from trpc_agent_sdk.tools import StreamingFunctionTool


def write_file(path: str, content: str) -> dict:
    """写入内容到文件（流式）。"""
    print(f"\n📄 Writing to {path}...")
    print(f"Content: {content[:100]}...")
    return {"success": True, "path": path, "size": len(content)}

# 创建流式工具
streaming_tool = StreamingFunctionTool(write_file)
```

#### 2. 包装异步函数

```python
from trpc_agent_sdk.tools import StreamingFunctionTool


async def async_write_file(path: str, content: str) -> dict:
    """异步写入文件。"""
    print(f"\n📄 Writing to {path}...")
    print(f"Content: {content[:100]}...")
    return {"success": True, "path": path, "size": len(content)}

streaming_tool = StreamingFunctionTool(async_write_file)
```

#### 3. 从 FunctionTool 转换

已有的 `FunctionTool` 可以直接转换为流式工具：

```python
from trpc_agent_sdk.tools import FunctionTool, StreamingFunctionTool

# 已有的 FunctionTool
regular_tool = FunctionTool(write_file)

# 转换为流式工具
streaming_tool = StreamingFunctionTool(regular_tool)
```

#### 4. 自定义 BaseTool

继承 `BaseTool` 并重写 `is_streaming` 属性：

```python
from trpc_agent_sdk.tools import BaseTool
from typing_extensions import override

class CustomStreamingWriteTool(BaseTool):
    """自定义流式工具。"""

    def __init__(self):
        super().__init__(
            name="custom_write",
            description="自定义流式写入工具",
        )

    @property
    @override
    def is_streaming(self) -> bool:
        """启用流式参数。"""
        return True

    @override
    async def _run_async_impl(self, *, tool_context, args):
        # 工具实现逻辑
        return {"success": True}
```

#### 5. 在 ToolSet 中使用流式工具

`ToolSet` 中的流式工具会被框架自动检测：

```python
from trpc_agent_sdk.tools import BaseToolSet, StreamingFunctionTool, FunctionTool


class FileToolSet(BaseToolSet):
    """文件操作工具集。"""

    def __init__(self):
        super().__init__(name="file_tools")
        self._tools = [
            StreamingFunctionTool(self._write_file),  # 流式工具
            FunctionTool(self._get_file_info),         # 普通工具
        ]

    def _write_file(self, path: str, content: str) -> dict:
        """写入内容到文件（流式）。"""
        print(f"\n📄 Writing to {path}...")
        print(f"Content: {content[:100]}...")
        return {"success": True, "path": path, "size": len(content)}

    def _get_file_info(self, path: str) -> dict:
        """获取文件信息（非流式）。"""
        return {"path": path, "exists": True}

    async def get_tools(self, invocation_context=None):
        return self._tools
```

---

### 流式工具完整示例

完整的流式工具使用示例见：[examples/streaming_tools/run_agent.py](../../examples/streaming_tools/run_agent.py)

---

### 流式工具 API 参考

#### @register_tool 装饰器

用于注册工具函数的装饰器，支持流式配置。

```python
@register_tool(
    name: str = '',           # 工具名称（默认使用函数名）
    description: str = '',    # 工具描述（默认使用函数 docstring）
    filters_name: list[str] = None,  # 过滤器名称
)
```

**示例**：

```python
@register_tool("get_weather")
def get_weather(city: str) -> dict:
    """获取天气信息。"""
    return {"temp": 20}
```

> **注意**: `@register_tool` 注册的是普通工具。如需创建流式工具，请使用 `StreamingFunctionTool`。

#### StreamingFunctionTool

支持流式参数的函数工具类。

**构造函数**：

```python
StreamingFunctionTool(
    func: Union[Callable, FunctionTool],  # 要包装的函数或 FunctionTool
    filters_name: list[str] = None,       # 过滤器名称（可选）
    filters: list[BaseFilter] = None,     # 过滤器实例（可选）
)
```

**参数说明**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `func` | `Callable \| FunctionTool` | 要包装的函数，支持同步/异步函数，或已有的 FunctionTool |
| `filters_name` | `list[str]` | 可选的过滤器名称列表 |
| `filters` | `list[BaseFilter]` | 可选的过滤器实例列表 |

#### is_streaming 属性

所有工具都有 `is_streaming` 属性，用于标识是否支持流式参数：

```python
from trpc_agent_sdk.tools import StreamingFunctionTool, FunctionTool
from .tools import write_file, get_file_info

# StreamingFunctionTool 的 is_streaming 始终返回 True
streaming_tool = StreamingFunctionTool(write_file)
print(streaming_tool.is_streaming)  # True

# FunctionTool 的 is_streaming 返回 False
regular_tool = FunctionTool(get_file_info)
print(regular_tool.is_streaming)  # False
```

#### 事件类型

流式工具调用会产生特殊的事件，可以通过以下方式识别：

```python
# 方法 1：使用 is_streaming_tool_call() 方法（推荐）
if event.is_streaming_tool_call():
    # 这是流式工具调用事件
    pass

# 方法 2：检查 partial 标记和 function_call
if event.partial and event.content:
    for part in event.content.parts:
        if part.function_call:
            # 这是流式工具调用事件
            pass
```

#### 增量内容获取

流式工具调用事件的参数中包含 `tool_streaming_args` 字段，表示本次增量：

```python
from trpc_agent_sdk.models import constants as const

if event.is_streaming_tool_call():
    for part in event.content.parts:
        if part.function_call:
            args = part.function_call.args or {}

            # 获取增量内容
            delta = args.get(const.TOOL_STREAMING_ARGS, "")
            # 或使用字符串字面量
            # delta = args.get("tool_streaming_args", "")
```

---

### 流式工具的选择性支持

框架支持在同一个 Agent 中**混合使用**流式工具和普通工具。只有标记为流式的工具才会产生流式事件：

```python
from trpc_agent_sdk.tools import StreamingFunctionTool, FunctionTool
from .tools import write_file, get_file_info

agent = LlmAgent(
    tools=[
        StreamingFunctionTool(write_file),   # ✅ 产生流式事件
        FunctionTool(get_file_info),          # ❌ 不产生流式事件
    ],
)
```

#### 检测流程

框架在运行时自动检测工具的 `is_streaming` 属性：

```
Agent 初始化
     ↓
ToolsProcessor.process_llm_request()
     ↓
遍历所有工具（包括 ToolSet 中的工具）
     ↓
检查每个工具的 is_streaming 属性
     ↓
收集所有 is_streaming=True 的工具名称
     ↓
仅为这些工具启用流式参数传输
```

这意味着：
- `StreamingFunctionTool` 创建的工具会产生流式事件
- 普通 `FunctionTool` 不会产生流式事件
- `ToolSet` 中的流式工具会被正确检测
- 自定义 `BaseTool` 需要重写 `is_streaming` 属性

---

### 与 ClaudeAgent 集成

`ClaudeAgent` 同样支持选择性流式工具，行为与 `LlmAgent` 保持一致：

```python
from trpc_agent_sdk.server.agents.claude import ClaudeAgent
from trpc_agent_sdk.tools import StreamingFunctionTool, FunctionTool
from .tools import write_file, get_file_info

agent = ClaudeAgent(
    name="claude_writer",
    model=_create_model(),
    tools=[
        StreamingFunctionTool(write_file),  # 流式：实时显示参数
        FunctionTool(get_file_info),         # 非流式：参数一次性返回
    ],
)
```

---

### 与 A2A 协议集成

A2A（Agent-to-Agent）协议支持跨服务的 Agent 调用。流式工具调用事件可以通过 A2A 协议实时传输给远程客户端。

#### A2A 事件转换

```
TRPC Agent 事件                     A2A 协议事件
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

### 与 AG-UI 集成

如果你使用 AG-UI 协议，流式工具调用会自动转换为对应的事件：

```
TRPC Agent 事件                    AG-UI 事件
─────────────────────────────────────────────────────────
streaming_tool_call (partial)  →  TOOL_CALL_START
streaming_tool_call (delta)    →  TOOL_CALL_ARGS
tool_call (complete)           →  TOOL_CALL_END
```

前端 JavaScript 示例：

```javascript
const eventSource = new EventSource('/api/agent/stream');

eventSource.onmessage = (event) => {
    const data = JSON.parse(event.data);

    switch (data.type) {
        case 'TOOL_CALL_START':
            console.log(`🔧 工具开始: ${data.toolCallId}`);
            break;

        case 'TOOL_CALL_ARGS':
            // 显示增量参数
            console.log(`⏳ 参数增量: ${data.delta}`);
            break;

        case 'TOOL_CALL_END':
            console.log(`✅ 工具完成`);
            break;
    }
};
```

---

### Streaming Tools 最佳实践

#### 1. 选择合适的参数做流式

流式工具最适合**包含长文本参数**的场景：

```python
from trpc_agent_sdk.tools import StreamingFunctionTool, FunctionTool
from .tools import write_file, get_file_info

# ✅ 适合流式：content 参数可能很长
streaming_tool = StreamingFunctionTool(write_file)

# ❌ 不太需要流式：参数都很短
regular_tool = FunctionTool(get_file_info)
```

#### 2. 在 Runner 层处理流式事件

```python
from trpc_agent_sdk.models import constants as const

# 用于累积流式内容
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
                # 获取增量内容
                delta = part.function_call.args.get(const.TOOL_STREAMING_ARGS, "")
                accumulated_content += delta
                # 显示进度
                print(f"⏳ Generated {len(accumulated_content)} chars...", end="\r")
        continue
```

#### 3. 处理多工具场景

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

                # 根据工具名称分别处理
                if tool_name == "write_file":
                    print(f"📄 写入文件: {delta[:30]}...")
                elif tool_name == "get_file_info":
                    print(f"📋 查询文件: {delta[:30]}...")
```

#### 4. 混合使用流式和非流式工具

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel, OpenAIModel
from trpc_agent_sdk.tools import FunctionTool, StreamingFunctionTool

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import get_file_info, write_file


def create_agent() -> LlmAgent:
    """创建带有流式和标准工具的 Agent。"""
    return LlmAgent(
        name="streaming_tool_demo_agent",
        description="An assistant demonstrating streaming tool usage",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[
            StreamingFunctionTool(write_file),  # 长文本参数：使用流式
            FunctionTool(get_file_info),        # 短参数：使用普通工具
        ],
        # 框架自动检测，无需手动配置 stream_tool_call_args
    )


root_agent = create_agent()
```

---

### Streaming Tools 常见问题

#### Q: 流式工具和普通工具可以混用吗？

**A**: 可以。你可以在同一个 Agent 中同时使用流式工具和普通工具。框架会自动检测每个工具的 `is_streaming` 属性，只为流式工具启用参数流式传输：

```python
from trpc_agent_sdk.tools import StreamingFunctionTool, FunctionTool
from .tools import write_file, get_file_info

agent = LlmAgent(
    tools=[
        StreamingFunctionTool(write_file),  # 流式
        FunctionTool(get_file_info),         # 非流式
    ],
)
```

#### Q: 哪些模型支持流式工具调用？

**A**: 目前已知支持的模型：
- ✅ glm4.7, glm5
- ✅ claude-opus-4.6
- ✅ kimi-k2.5
- ✅ gpt-5.2
- 其他模型需要自行测试

#### Q: 如何累积流式内容？

**A**: 在 Runner 层消费事件时自行累积：

```python
from trpc_agent_sdk.models import constants as const

# 用于累积流式内容
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

#### Q: 如何判断一个工具是否支持流式？

**A**: 检查工具的 `is_streaming` 属性：

```python
from trpc_agent_sdk.tools import StreamingFunctionTool, FunctionTool
from .tools import write_file, get_file_info

streaming_tool = StreamingFunctionTool(write_file)
print(streaming_tool.is_streaming)  # True

regular_tool = FunctionTool(get_file_info)
print(regular_tool.is_streaming)  # False
```

#### Q: ToolSet 中的流式工具会被检测到吗？

**A**: 会。框架在运行时会递归检测 ToolSet 中所有工具的 `is_streaming` 属性，确保流式工具被正确识别。

---

### Streaming Tools 相关资源

- [流式工具完整示例](../../examples/streaming_tools/run_agent.py) - 流式工具运行示例
- [函数工具文档](#function-tools) - 普通函数工具的使用
