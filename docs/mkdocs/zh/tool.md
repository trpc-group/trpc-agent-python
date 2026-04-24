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
| [WebFetchTool](#webfetchtool) | 抓取并文本化单个公网 URL | 实例化 WebFetchTool 并加入 tools | 阅读文档页、RFC、changelog、新闻 |
| [WebSearchTool](#websearchtool) | 公网搜索引擎检索 | 实例化 WebSearchTool 并加入 tools | 实时资讯、版本发布、事实/定义查询 |
| [Agent Code Executor](./code_executor.md) | 自动生成并执行代码场景、数据处理场景 | 配置 CodeExecutor | API 自动调用、表格数据处理 |
---

## Function Tools

Function Tool 是 trpc_agent 框架中最基础且常用的工具类型，它允许开发者将 Python 函数快速转换为 Agent 可以调用的工具。当框架提供的内置工具无法满足特定需求时，开发者可以通过 Function Tool 创建定制化功能。

trpc_agent 提供了多种创建 Function Tool 的方式，适应不同的开发场景和复杂度需求。

### 使用 Tool

#### 1. 直接包装函数

最简单的方式是直接使用 `FunctionTool` 类包装单个 Python 函数：

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

# 在 Agent 中使用
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

Function Tool 的首选返回类型是**字典（dict）**，这样可以向 LLM 提供结构化信息。如果函数返回其他类型，框架会自动包装成字典，键名为 `"result"`。

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

在返回值中包含 `"status"` 字段可以帮助 LLM 理解操作结果：

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

完整的 Function Tool 使用示例见：[examples/function_tools/run_agent.py](../../../examples/function_tools/run_agent.py)



## MCP Tools

MCP Tools（Model Context Protocol Tools）是 trpc_agent 框架中用于集成外部 MCP 服务器工具的机制。通过 MCP 协议，Agent 可以调用其他进程提供的工具。

在 trpc_agent 中，主要的集成模式是：**Agent 作为 MCP 客户端**，通过 `MCPToolset` 连接并使用外部 MCP 服务器提供的工具。

### 使用 MCPToolset

`MCPToolset` 是 trpc_agent 中用于集成 MCP 工具的核心类。它可以连接到 MCP 服务器，自动发现可用工具，并将它们转换为 Agent 可以使用的工具，它的用法很简单，如下所示：

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

用于连接本地进程中的 MCP 服务器：

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
- 这里传递的是 `McpStdioServerParameters` 类型的参数，框架内部会将其转为 `StdioConnectionParams` 类型；如果用户直接使用 `StdioConnectionParams` 类型，则需要换成如下方式
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

#### sse 类型的 SseConnectionParams

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

#### streamable-http 类型的 StreamableHTTPConnectionParams

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

完整的 MCP Tools 使用示例见：[examples/mcp_tools/run_agent.py](../../../examples/mcp_tools/run_agent.py)

### MCP Tools FAQ

#### 出现 `Attempted to exit a cancel scope that isn't the current tasks's current cancel scope`

出现该错误是因为官方 mcp 库依赖 AnyIO：当 cancel scope 的进入与退出发生在不同的任务（Task）上下文时，就会触发此错误。若在运行 Agent 时遇到该错误，请执行：

```python

async def main():
    # ...
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)
    async for event in runner.run_async(user_id=user_id, session_id=current_session_id, new_message=user_content):
        # ...
    await runner.close()

```

若错误仍然出现，请在程序入口处执行：

```python
from trpc_agent_sdk.tools import patch_mcp_cancel_scope_exit_issue

patch_mcp_cancel_scope_exit_issue()

# 主函数入口

```

## ToolSet

Tool Set 是 trpc_agent 框架中一组工具的集合

### 使用 ToolSet

当需要组织多个相关的工具时，可以使用**ToolSet（工具集）**。

#### 1. 创建 ToolSet

创建自定义 ToolSet 需要继承 `BaseToolSet` 并实现必要的方法：

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

ToolSet 可以直接添加到 Agent 的工具列表中：

```python
from trpc_agent_sdk.agents import LlmAgent

from .tools import WeatherToolSet


def create_agent() -> LlmAgent:
    """创建带有天气工具集的 Agent"""
    # 创建 ToolSet 实例并初始化
    weather_toolset = WeatherToolSet()
    weather_toolset.initialize()

    # 在 Agent 中使用：tools 列表中既可以添加 Tool，也可以添加 ToolSet
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

- **功能分组**：将相关功能的工具组织在同一个 ToolSet 中
- **权限控制**：利用`get_tools`方法实现基于用户的工具访问控制
- **资源管理**：在`close`方法中正确清理资源
- **初始化**：在`initialize`方法中完成工具的创建和配置
- **关闭**：在 Runner 运行完成后需要执行`close`方法优雅退出


### ToolSet 完整示例

完整的 ToolSet 使用示例见：[examples/toolsets/run_agent.py](../../../examples/toolsets/run_agent.py)

---

## Agent Tools

trpc_agent 提供了 **AgentTool**，允许将 Agent 包装成 Tool，实现将一个 Agent 的输出，作为另一个 Agent 的输入。


### 使用 AgentTool

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.tools import AgentTool

# 创建专业的翻译 Agent
translator = LlmAgent(
    name="translator",
    model=model,
    description="A professional text translation tool",
    instruction=TRANSLATOR_INSTRUCTION,
)

# 将 Agent 包装成 Tool
translator_tool = AgentTool(agent=translator)

# 在主 Agent 中使用
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

完整的 AgentTool 使用示例见：[examples/agent_tools/run_agent.py](../../../examples/agent_tools/run_agent.py)

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
from trpc_agent_sdk.agents import LlmAgent
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

完整的 File Tools 使用示例见：[examples/file_tools/run_agent.py](../../../examples/file_tools/run_agent.py)

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

完整的 File Tools 使用示例见：[examples/file_tools/agent/agent.py](../../../examples/file_tools/agent/agent.py)

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

完整的使用示例见：[examples/langchain_tools/run_agent.py](../../../examples/langchain_tools/run_agent.py)

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

trpc_agent 框架将不同模型的输出统一转换为内部格式：

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

#### 使用 `StreamingFunctionTool` 包装

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

trpc_agent 提供了多种创建流式工具的方式，适用于不同场景：

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

完整的流式工具使用示例见：[examples/streaming_tools/run_agent.py](../../../examples/streaming_tools/run_agent.py)

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
trpc_agent 事件                     A2A 协议事件
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
trpc_agent 事件                    AG-UI 事件
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

- [流式工具完整示例](../../../examples/streaming_tools/run_agent.py) - 流式工具运行示例
- [函数工具文档](#function-tools) - 普通函数工具的使用

## WebFetchTool (网页获取工具)

`WebFetchTool` 是 trpc-agent-python 框架内置的**单 URL 联网抓取工具**。当 Agent 需要阅读、摘要或引用某个公开网页的内容时，可以通过该工具发起一次 HTTP GET 请求，框架会将响应统一转换为可供 LLM 消费的结构化文本：HTML 会被裁剪为 Markdown 纯文本，其它 `text/*` / `application/json` 等文本型 MIME 按原样返回，二进制响应则以结构化错误拒收。

### 功能特性

- **单次 HTTP GET**：HTML 自动转换为 Markdown 纯文本（去除 `<script>` / `<style>` / `<svg>` 等非内容块）；其他文本型 MIME 按原样返回；二进制响应（PDF、图片、归档等）以 `UNSUPPORTED_CONTENT_TYPE` 错误拒收
- **SSRF 防护**：`block_private_network=True`（默认）会对请求目标及**每一跳重定向**做 DNS 解析校验，拒绝回环 / 私网 / 链路本地（含 `169.254.169.254` 云元数据端点）/ 保留 / 组播 / 未指定地址
- **域名白/黑名单**：`allowed_domains` / `blocked_domains` 为**工具级**配置，子域感知匹配（`www.` 前缀剥离，`python.org` 同时匹配 `docs.python.org`）
- **内容与字节双重裁剪**：`max_content_length`（字符）与 `max_response_bytes`（字节）分别控制返回文本长度与实际读取的原始字节；LLM 还可在调用时通过 `max_length` 参数进一步控制
- **手动重定向控制**：`follow_redirects` / `max_redirects` 提供可预期的重定向循环上限，避免无限跳转
- **进程内 LRU 缓存**：`enable_cache=True` 时启用 URL → `FetchResult` LRU；`cache_ttl_seconds` / `cache_max_bytes` 控制TTL与缓存字节预算，命中时响应上 `cached=true`，缓存键会做 URL 归一化（统一 scheme 大小写、剥离 `www.`、忽略默认端口和尾部 `/`）

### WebFetchTool 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `timeout` | `float` | `30.0` | HTTP 超时时间（秒） |
| `user_agent` | `str` | `"trpc-agent-python-webfetch/1.0"` | HTTP `User-Agent` 头，便于下游日志区分来源流量 |
| `proxy` | `Optional[str]` | `None` | 可选的 HTTP 代理 URL，直接转发给 `httpx` |
| `http_client` | `Optional[httpx.AsyncClient]` | `None` | 可选的预构建 `httpx.AsyncClient`，用于复用连接池（调用方负责其生命周期） |
| `max_content_length` | `int` | `100_000` | 返回 `content` 的字符上限，`0` 表示不限；可被调用参数 `max_length` 覆盖 |
| `max_response_bytes` | `int` | `5 * 1024 * 1024`（5 MB） | 读取的原始响应字节上限，`0` 表示不限；流式读取命中上限即终止 |
| `allowed_domains` | `Optional[List[str]]` | `None` | 工具级 host 白名单（子域感知，`www.` 前缀剥离），LLM 无法覆盖 |
| `blocked_domains` | `Optional[List[str]]` | `None` | 工具级 host 黑名单，匹配规则同白名单；**优先于白名单**检查 |
| `block_private_network` | `bool` | `True` | SSRF 防护开关；开启时拒绝所有解析到私网 / 回环 / 链路本地等地址的目标 |
| `follow_redirects` | `bool` | `True` | 是否手动跟随 3xx 重定向 |
| `max_redirects` | `int` | `5` | 重定向最大跳数上限 |
| `enable_cache` | `bool` | `False` | 是否启用进程内 LRU 缓存 |
| `cache_ttl_seconds` | `float` | `900.0`（15 分钟） | 缓存项 TTL，超时后下次访问穿透并淘汰 |
| `cache_max_bytes` | `int` | `50 * 1024 * 1024`（50 MB） | 缓存总字节容量；超过该容量将被静默跳过 |
| `filters_name` | `Optional[List[str]]` | `None` | 关联的 filter 名称，透传给 `BaseTool` |
| `filters` | `Optional[List[BaseFilter]]` | `None` | 直接注入的 filter 实例，透传给 `BaseTool` |

**LLM 调用参数**（由 LLM 在调用时填充，非构造参数）：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `url` | `string` | 是 | 绝对 http(s) URL，必须包含 scheme（如 `https://docs.python.org/3/whatsnew/3.13.html`） |
| `max_length` | `integer` | 否 | 本次调用的 `content` 字符上限（覆盖工具级 `max_content_length`），`0` 禁用该上限 |

**`FetchResult` 返回字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `url` | `str` | 重定向后最终的 URL |
| `status_code` | `int` | HTTP 状态码（请求未完成时为 `0`） |
| `status_text` | `str` | HTTP 状态原因短语 |
| `content_type` | `str` | 规范化后的 media type（无附加参数） |
| `content` | `str` | 文本化后的正文，可能被截断 |
| `bytes` | `int` | `content` 的 UTF-8 字节长度 |
| `duration_ms` | `int` | 整个请求的耗时（毫秒） |
| `cached` | `bool` | 是否命中进程内 LRU 缓存 |
| `error` | `str` | 失败或被拒绝时的结构化错误码（如 `BLOCKED_URL` / `SSRF_BLOCKED_URL` / `HTTP_STATUS` / `UNSUPPORTED_CONTENT_TYPE` / `HTTP_ERROR`） |

### 使用方式

#### 构造 WebFetchTool Agent

在`agent/agent.py` 中创建 WebFetchTool Agent：

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel, OpenAIModel
from trpc_agent_sdk.tools import WebFetchTool

from .config import get_model_config
from .prompts import INSTRUCTION


def _create_model() -> LLMModel:
    """创建 LLM 模型"""
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def create_default_fetch_agent() -> LlmAgent:
    """创建 WebFetchTool Agent"""
    web_fetch = WebFetchTool(
        timeout=10.0, # 超时时间配置
        user_agent="trpc-agent-python-webfetch-example/1.0", # User-Agent 头
        max_content_length=4000, # 返回文本字符上限
        max_response_bytes=1 * 1024 * 1024, # 读取的原始字节上限
        follow_redirects=True, # 手动重定向循环
        max_redirects=3, # 重定向最大跳数上限
        block_private_network=True, # SSRF 防护开关
        # allowed_domains=["python.org"], # 域名白名单
        # blocked_domains=["example.com"], # 域名黑名单
        # enable_cache=True, # 启用缓存
        # cache_ttl_seconds=120.0, # 缓存TTL
        # cache_max_bytes=1 * 1024 * 1024, # 缓存字节容量
    )
    return LlmAgent(
        name="default_webfetch_assistant",
        description="Web-reading assistant that fetches a single URL and summarises its textual content.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[web_fetch],
    )
```

> **注意**：
> - `allowed_domains` / `blocked_domains` 是**工具级**配置，LLM **无法在调用参数里覆盖**；匹配规则为**子域感知**（`www.` 前缀会被剥离），且**每一跳重定向都会重新校验**，防止"合法首跳 → 跳到被禁主机"的绕过
> - `block_private_network=True` 默认开启 SSRF 防护；仅当调用方已用外部白名单限定目标且确信输入可信时可以考虑关闭
> - `enable_cache` 默认关闭，需显式 opt-in；缓存键会做 URL 归一化（统一 scheme 大小写、剥离 `www.`、忽略默认端口、忽略尾 `/`），`https://example.com` 与 `https://www.example.com/` 共享同一条缓存项

#### 驱动 Agent 并打印工具事件

`run_agent.py` 驱动 Agent，逐条执行 `(label, query)` 场景，并从事件流里提取 `function_call` / `function_response` 以便直观观察工具调用：

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
            # 跳过思考部分
            if part.thought:
                continue
            # 打印工具调用
            if part.function_call:
                print(f"\n🔧 [Invoke Tool: {part.function_call.name}({part.function_call.args})]")
            # 打印工具响应
            elif part.function_response:
                resp = part.function_response.response
                print(f"📊 [Tool Result: {resp}]")

    print("\n" + "-" * 40)


async def _drive_agent(agent: LlmAgent, *, scenarios: list[tuple[str, str]]) -> None:
    """驱动 Agent 执行场景"""
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

#### 运行示例

**返回值示例**：

成功抓取时，`function_response` 中的 `FetchResult` 形如：

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

命中缓存时 `cached=True`；被域名策略或 SSRF 防护拦截时 `error` 会包含结构化错误码：

```python
# 被域名策略拒绝
{"url": "https://example.com", "error": "BLOCKED_URL: 'example.com' is not permitted by the tool's domain policy", ...}

# 被 SSRF 防护拒绝（如目标解析到 127.0.0.1）
{"url": "http://localhost:8080", "error": "SSRF_BLOCKED_URL: localhost resolves to private/reserved address 127.0.0.1", ...}

# 二进制响应被拒
{"url": "https://example.com/a.pdf", "error": "UNSUPPORTED_CONTENT_TYPE: application/pdf", ...}
```

建议在 Agent 的 `instruction` 中约定：当工具返回 `error` 字段时，应向用户**复述错误码并解释原因**，而不是编造内容；当 `content` 被截断或命中缓存时，也应在回答中显式说明

### WebFetchTool 最佳实践

- **安全优先**：在能访问云元数据端点（如 AWS EC2 的 `169.254.169.254`）或内网资源的环境中部署 Agent 时，**保留 `block_private_network=True` 默认值**
- **内容裁剪**：为防止长页面撑爆上下文窗口，建议为 `max_content_length` 设置一个与模型窗口匹配的合理值（例如 4000~20000 字符）；LLM 仅需摘要时可通过 `max_length`设置
- **字节预算**：对大文件（如巨型 HTML、日志页）优先依赖 `max_response_bytes` 在网络层提前止损，而不是先下载再裁剪
- **缓存策略**：对热点文档 / changelog / status page 打开 `enable_cache=True`，并依据页面平均大小设置 `cache_max_bytes`；注意 TTL 过长可能返回过期内容，`cached=true` 可用于下游判断
- **域名策略**：需要把 Agent 限定在可信站点时使用 `allowed_domains`；想屏蔽噪声或高风险站点则使用 `blocked_domains`；两者可组合使用，**黑名单优先**
- **与 MCP 工具配合**：当存在专用的 MCP 抓取工具（带鉴权、JS 渲染、表单提交能力）时，优先使用 MCP 工具；`WebFetchTool` 更适合对无需登录的公开文档类页面做快速阅读
- **自定义 HTTP 行为**：需要对接公司代理、mTLS 或复用连接池时，通过构造参数 `proxy` 或注入 `http_client` 实现

### WebFetchTool 完整示例

完整的 WebFetchTool 使用示例见：[examples/webfetch_tool/run_agent.py](../../../examples/webfetch_tool/run_agent.py)

示例中覆盖了以下场景：

- 基线：HTTP 形态默认项 + SSRF 默认项
- `max_length` 按调用覆盖：LLM 在调用参数里进一步返回文本长度
- LRU 缓存命中：同一 URL 连续抓取两次，第二次 `cached=true`
- 白名单拒绝：非白名单主机被拒并返回 `BLOCKED_URL`
- 黑名单拒绝：黑名单主机被拒并返回 `BLOCKED_URL`

## WebSearchTool （网络搜索工具）

`WebSearchTool` 是 trpc-agent-python 框架内置的**公网搜索工具**。当 Agent 需要回答"最新动态 / 版本号 / 事件 / 定义 / 事实类"等超出模型知识截止日期的问题时，可以通过该工具调用主流搜索引擎的检索 API，获取带标题、URL 与摘要的结构化结果，并按约定将所有引用以 Markdown 超链接的形式列在 `Sources:` 段落中。

该工具采用**可插拔 provider** 设计，目前内置两种后端：

- **`duckduckgo`（默认）**：DuckDuckGo Instant Answer API，**无需 API Key**。返回 DDG 精选的 instant answer / abstract / definition 摘要及相关主题，适合百科/定义/事实类查询；注意返回的并非完整的实时网页结果，而是 DDG 的 curated 结果集
- **`google`**：Google Custom Search（CSE）JSON API，需要配置 `api_key` 与 `engine_id`（即 CSE 的 `cx`）；返回真实的公网搜索结果，支持 `siteSearch`、`hl`（语言）、`safe`（SafeSearch）、`dateRestrict`（时效性）等 CSE 原生参数

在此基础上，`WebSearchTool` 还内置了**域名白/黑名单过滤、URL 归一化去重、结果裁剪、引用规范强制注入、HTTP 连接池复用**等能力，帮助你在生产环境中稳定、可控地把联网检索接入 LLM Agent。

### 功能特性

- **双 Provider 支持**：`duckduckgo`（keyless，适合定义/百科）与 `google`（需要 CSE API Key，支持真实公网搜索）通过 `provider` 参数切换，对 LLM 暴露的 `FunctionDeclaration` 保持一致
- **域名白/黑名单**：LLM 可在调用时填入 `allowed_domains` / `blocked_domains`（二者互斥），工具会做**子域感知**匹配（`www.` 前缀剥离，`python.org` 同时匹配 `docs.python.org`）；Google 单域名时走服务端 `siteSearch` 快速路径，多域名自动回退到客户端过滤
- **URL 归一化去重**：`dedup_urls=True`（默认）会按 scheme/host/path 归一化键合并重复命中，避免 `Sources:` 段里出现同一来源多次；设置为 `False` 可保留原始召回列表，便于接入下游 re-ranker / 多样化采样 / 离线评估
- **结果裁剪**：`results_num` / `snippet_len` / `title_len` 分别控制返回条数、单条摘要与标题的字符上限，所有参数都会按 `[1, _MAX_*]` 做 clamp，避免误配超上下文窗口；LLM 还可通过 `count` 参数在调用时进一步控制返回条数
- **强制引用规范**：工具在 `process_request` 阶段自动向 LLM 追加指令，**强制**要求：（1）回答末尾必须追加 `Sources:` 段并以 `[Title](URL)` 列出工具返回的 URL；（2）不得编造 URL；（3）涉及"最新/recent"类查询时使用**当前年月**入参，避免幻觉旧年份
- **Provider 原生参数透传**：`ddg_extra_params` / `google_extra_params` 让你把 provider 专属的高级参数（如 Google CSE 的 `safe`、`dateRestrict`、`gl`、`cr`）固化在 agent 层，每次工具调用自动带上，无需在 `FunctionDeclaration` 里额外暴露
- **共享 httpx 连接池**：通过构造参数 `http_client` 传入预建好的 `httpx.AsyncClient`，可在多个 agent / 多次调用之间复用连接池；调用方负责其生命周期（工具不会帮你 `aclose`）
- **结构化输出**：统一返回 `WebSearchResult`，包含 `query` / `provider` / `results: List[{title, url, snippet}]` / `summary`，便于 LLM 引用拼装，也便于下游做 re-rank / RAG

### WebSearchTool 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `provider` | `Literal["duckduckgo", "google"]` | `"duckduckgo"` | 搜索后端；`google` 需要同时配置 `api_key` 与 `engine_id`，否则调用时返回未配置提示 |
| `api_key` | `Optional[str]` | `None` | Google CSE API Key，缺省时回退到环境变量 `GOOGLE_CSE_API_KEY` |
| `engine_id` | `Optional[str]` | `None` | Google CSE 引擎 ID（即 `cx`），缺省时回退到环境变量 `GOOGLE_CSE_ENGINE_ID` |
| `base_url` | `Optional[str]` | provider 默认 | 覆盖 provider 的 API Base URL（主要用于测试 / 代理） |
| `user_agent` | `str` | `"trpc-agent-python-websearch/1.0"` | HTTP `User-Agent` 头，便于下游日志区分来源流量 |
| `proxy` | `Optional[str]` | `None` | 可选的 HTTP 代理 URL，直接转发给 `httpx` |
| `lang` | `Optional[str]` | `None` | Google CSE 默认语言（对应 `hl` 参数），DDG 会忽略；LLM 可通过调用参数 `lang` 覆盖 |
| `http_client` | `Optional[httpx.AsyncClient]` | `None` | 可选的预构建 `httpx.AsyncClient`，用于复用连接池（调用方负责生命周期） |
| `results_num` | `int` | `5` | 默认返回条数上限，clamp 到 `[1, 10]`；可被调用参数 `count` 覆盖 |
| `snippet_len` | `int` | `300` | 单条 `snippet` 的字符上限，clamp 到 `[1, 1000]` |
| `title_len` | `int` | `100` | 单条 `title` 的字符上限，clamp 到 `[1, 200]` |
| `timeout` | `float` | `15.0` | HTTP 超时时间（秒） |
| `dedup_urls` | `bool` | `True` | 是否按归一化键合并重复 URL；`False` 时保留原始命中顺序 |
| `ddg_extra_params` | `Optional[dict]` | `None` | 透传给 DDG 的额外查询参数 |
| `google_extra_params` | `Optional[dict]` | `None` | 透传给 Google CSE 的额外查询参数（如 `{"safe": "active"}`、`{"dateRestrict": "m6"}`、`{"gl": "us"}` 等） |
| `filters_name` | `Optional[List[str]]` | `None` | 关联的 filter 名称，透传给 `BaseTool` |
| `filters` | `Optional[List[BaseFilter]]` | `None` | 直接注入的 filter 实例，透传给 `BaseTool` |

**LLM 调用参数**（由 LLM 在调用时填充，非构造参数）：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | `string` | 是 | 检索关键词，至少 2 个字符；对于"最新/版本号"类主题建议显式带上年份/版本号 |
| `count` | `integer` | 否 | 本次调用的返回条数上限，`1-10`（clamp）；默认为工具级 `results_num` |
| `allowed_domains` | `array[string]` | 否 | 域名白名单（host only，子域感知，`www.` 自动剥离）；与 `blocked_domains` 互斥 |
| `blocked_domains` | `array[string]` | 否 | 域名黑名单，匹配规则同上；与 `allowed_domains` 互斥 |
| `lang` | `string` | 否 | 仅 Google CSE 生效（对应 `hl`），DDG 会忽略；覆盖工具级 `lang` |

**`WebSearchResult` 返回字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `query` | `str` | 本次检索的查询词（原样回显） |
| `provider` | `"duckduckgo" \| "google"` | 实际使用的 provider |
| `results` | `List[SearchHit]` | 结构化命中列表，每项包含 `title` / `url` / `snippet` |
| `summary` | `str` | DDG 的 instant answer / abstract / definition 聚合摘要；Google 在发生拼写纠错或 API 错误时也会写入此字段 |

当调用参数非法（如同时传入 `allowed_domains` 与 `blocked_domains`、`query` 过短）或 HTTP 出错时，工具会返回结构化错误对象（如 `{"error": "INVALID_ARGS: ..."}` / `{"error": "HTTP_ERROR: ..."}`），便于 LLM 做降级处理。

### 使用方式

#### 构造 WebSearchTool Agent

在 `agent/agent.py` 中创建 WebSearchTool Agent（以默认的 DuckDuckGo provider 为例，**无需任何 API Key** 即可开跑）：

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel, OpenAIModel
from trpc_agent_sdk.tools import WebSearchTool

from .config import get_model_config
from .prompts import INSTRUCTION


def _create_model() -> LLMModel:
    """创建 LLM 模型"""
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def create_ddg_agent() -> LlmAgent:
    """创建基于 DuckDuckGo 的 WebSearchTool Agent"""
    web_search = WebSearchTool(
        provider="duckduckgo",     # keyless，适合定义/百科/事实类查询
        results_num=3,             # 默认返回最多 3 条
        snippet_len=300,           # 每条摘要最多 300 字符
        title_len=80,              # 每条标题最多 80 字符
        timeout=10.0,
        # dedup_urls=False,                    # 关闭 URL 归一化去重，保留原始召回
        # ddg_extra_params={"region": "us-en"},  # DDG 原生参数透传
    )
    return LlmAgent(
        name="ddg_research_assistant",
        description="Web research assistant powered by DuckDuckGo Instant Answers.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[web_search],
    )
```

**切换到 Google Custom Search**：当需要真正的公网搜索结果、或需要时效性/语言/SafeSearch 控制时，切换到 `provider="google"`。Google 需要先在 [Google Cloud Console](https://developers.google.com/custom-search/v1/overview) 申请 API Key，在 [Programmable Search Engine](https://programmablesearchengine.google.com/) 创建引擎获取 `cx`；

```python
import httpx

from .config import get_google_cse_config, get_http_proxy

# 业务侧创建并负责生命周期：程序退出前调用 await shared_client.aclose()
shared_client = httpx.AsyncClient(
    timeout=15.0,
    limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
)


def create_google_agent() -> LlmAgent:
    """创建基于 Google Custom Search 的 WebSearchTool Agent"""
    api_key, engine_id = get_google_cse_config()
    web_search = WebSearchTool(
        provider="google", # 使用 Google Custom Search
        api_key=api_key, # Google CSE API Key
        engine_id=engine_id, # Google CSE Engine ID
        user_agent="trpc-agent-python-websearch-demo/1.0 (+google-cse)", # User-Agent 头
        proxy=get_http_proxy(),                # 可选：出口代理
        lang="en", # 语言设定
        http_client=shared_client,             # 复用连接池，需要调用方显式关闭
        results_num=3, # 返回条数
        snippet_len=240, # 单条摘要字符上限
        title_len=80, # 单条标题字符上限
        timeout=15.0, # 超时时间
        dedup_urls=True, # 开启 URL 归一化去重
        google_extra_params={"safe": "active"},       # 打开 Google SafeSearch
        # google_extra_params={"dateRestrict": "m6"}, # 仅保留过去 6 个月索引的结果
    )
    return LlmAgent(
        name="google_research_assistant",
        description="Web research assistant powered by Google Custom Search (SafeSearch on).",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[web_search],
    )
```

> **注意**：
> - `allowed_domains` / `blocked_domains` 由 **LLM 在调用参数里**填入（而非构造参数），LLM 可根据用户 prompt 决定是否启用；两者互斥，同时传入会返回 `INVALID_ARGS`
> - 当传入外部 `http_client` 时，`WebSearchTool` **不会**帮你调用 `aclose()`，需要调用方在**同一个事件循环**内显式关闭，避免 `Unclosed client` 警告
> - 即使复用外部 client，工具内部仍会在每次 `GET` 时强制应用构造器里的 `timeout` 与 `user_agent`，保证 agent 层的约束始终生效
> - 其他常用的 Google CSE 透传参数包括 `gl`（地理偏向）、`cr`（国家限制）、`filter`、`sort` 等；对 DuckDuckGo 可通过 `ddg_extra_params` 透传 `region`、`kl` 等

#### 驱动 Agent 并打印工具事件

`run_agent.py` 驱动 Agent，逐条执行 `(label, query)` 场景，并从事件流里提取 `function_call` / `function_response` 以便直观观察工具调用：

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
            # 打印工具调用  
            if part.function_call:
                print(f"\n🔧 [Invoke Tool: {part.function_call.name}({part.function_call.args})]")
            # 打印工具响应
            elif part.function_response:
                resp = part.function_response.response
                print(f"📊 [Tool Result: {resp}]") # 工具响应

    print("\n" + "-" * 40)


async def _drive_agent(agent: LlmAgent, *, scenarios: list[tuple[str, str]]) -> None:
    """驱动 Agent 执行场景"""
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

LLM 看到上述 prompt 后，会按 `FunctionDeclaration` 自动组装出类似如下的工具调用：

```python
# LLM 自动生成的 function_call.args
{
    "query": "Python (programming language)",
    "count": 3,
    "allowed_domains": ["wikipedia.org"],
}
```

#### 运行示例

**返回值示例**：

成功时，`function_response` 中的 `WebSearchResult` 形如：

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

DuckDuckGo provider 在命中 instant answer 时，`summary` 字段会包含 DDG 聚合的摘要文本（如维基摘要、词典定义等），LLM 可直接引用；当 DDG 没有 `Results` 时，工具会兜底把 DDG 搜索页 URL 作为唯一来源返回，保证 `Sources:` 段不为空。

**错误处理**：当参数非法或 HTTP 出错时，工具返回结构化错误对象：

```python
# 同时传入白/黑名单
{"error": "INVALID_ARGS: cannot specify both allowed_domains and blocked_domains in the same request"}

# query 过短
{"error": "INVALID_QUERY: query must be at least 2 characters"}

# Google CSE 未配置 api_key / engine_id
{"query": "...", "provider": "google", "results": [],
 "summary": "Google provider is not configured: set api_key + engine_id ..."}

# 网络 / HTTP 错误
{"error": "HTTP_ERROR: ConnectTimeout(...)", "provider": "google", "query": "..."}
```

建议在 Agent 的 `instruction` 中约定：当工具返回 `error` 字段时，应向用户**复述错误原因并给出降级方案**（如提示用户稍后重试、改换查询词、检查域名白名单等），而不是编造内容。

**自动注入的引用规范**：`WebSearchTool.process_request` 会在每次请求前自动 `append_instructions`，强制要求 LLM：

- 回答末尾必须追加 `Sources:` 段并以 `[Title](URL)` 列出工具返回的 URL
- **不得编造 URL**，只能引用工具实际返回的 URL
- 涉及"最新/recent/current"类查询时使用**当前月份与年份**入参，避免幻觉旧年份

这部分逻辑无需用户在 `instruction` 中重复声明，只要挂载 `WebSearchTool` 即自动生效。

### WebSearchTool 最佳实践

- **Provider 选择**：仅需无 API Key、轻量的定义/百科/事实类检索时使用默认的 `duckduckgo`；需要真实公网搜索、支持 site/语言/SafeSearch/时效性时切换到 `google` 并配置 CSE 凭据
- **结果裁剪**：为防止长摘要超过上下文窗口，建议为 `snippet_len` / `title_len` / `results_num` 设置与模型窗口匹配的合理值；LLM 仅需少量来源时可通过调用参数 `count` 进一步收紧
- **域名策略**：需要把 Agent 限定在可信站点（如企业官网、官方文档）时，在 prompt 中显式要求 LLM 填入 `allowed_domains`；想屏蔽内容农场或噪声站点时使用 `blocked_domains`；两者互斥
- **Google 多域名过滤**：Google CSE 的 `siteSearch` 只接受单个值，因此多域名白/黑名单时工具会自动回退到客户端过滤。若希望单域名走服务端快速路径，prompt 中约束 LLM 一次只传一个域名即可
- **去重开关**：默认开启URL归一化去重，避免 `Sources:` 段里出现同一来源多次；设置为 `False` 可保留原始召回列表，便于接入下游 re-ranker / 多样化采样 / 离线评估
- **时效性控制**：对"最新/what's new/today"类 Agent，把 `google_extra_params={"dateRestrict": "m6"}`（最近 6 个月）/ `"m1"`（1 个月）/ `"d7"`（7 天）固化在 agent 层，比在 prompt 里反复强调更可靠
- **连接池复用**：同一进程内挂载多个 `WebSearchTool` 或高频调用时，通过 `http_client` 传入共享的 `httpx.AsyncClient`，并在程序退出时由调用方显式 `aclose()`
- **凭据与代理**：Google CSE 的 `api_key` / `engine_id` 建议通过环境变量（`GOOGLE_CSE_API_KEY` / `GOOGLE_CSE_ENGINE_ID`）注入，不硬编码在源码；需要经过企业出口代理时通过 `proxy` 参数配置
- **与 WebFetchTool 配合**：`WebSearchTool` 用来"发现 URL"，`WebFetchTool` 用来"读取 URL 全文"——当 LLM 需要对某条搜索结果做深入阅读 / 摘要 / 引述时，把两个工具同时挂到 Agent 上，形成"搜索 → 精读"的两阶段工作流
- **与 Knowledge/RAG 配合**：把搜索结果作为实时补充语料接入 RAG 流程时，参考 [examples/knowledge_with_searchtool_rag_agent](../../../examples/knowledge_with_searchtool_rag_agent)

### WebSearchTool 完整示例

完整的 WebSearchTool 使用示例见：[examples/websearch_tool/run_agent.py](../../../examples/websearch_tool/run_agent.py)

示例中构建了四个独立 Agent，覆盖以下场景：

- **DuckDuckGo 基线**（`ddg_agent`，`dedup_urls=True`）：实体名查询 + 白名单 + 黑名单
- **DuckDuckGo 原始命中**（`ddg_raw_agent`，`dedup_urls=False`）：保留 provider 原始召回列表，便于下游处理
- **Google 基线**（`google_agent`，`safe=active`）：真实公网搜索 + 服务端单域 `siteSearch` + 客户端多域过滤 + 黑名单 + per-call `lang` 覆盖
- **Google 时效性 Agent**（`google_raw_agent`，`dateRestrict=m6` + `dedup_urls=False`）：只保留过去 6 个月索引的结果，适合"最新/what's new"类查询
