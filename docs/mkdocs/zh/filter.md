# Filters

trpc_agent 提供了强大的拦截器（Filter）机制来拦截和处理请求响应流程。拦截器可以在不同阶段对请求进行处理，包括工具调用、模型调用和代理调用等环节，为开发者提供了灵活的扩展能力。

## Filter 的核心功能

- **请求拦截**：在请求执行前进行预处理和验证
- **响应处理**：在请求执行后进行后处理和结果修改
- **流式处理**：支持流式响应的实时处理
- **链式调用**：支持多个拦截器按顺序执行

Filter 基类提供了两种拦截方法，分别适用于普通调用和流式调用场景：

```python
class BaseFilter(FilterABC):
    async def run_stream(self, ctx: AgentContext, req: Any,
                         handle: FilterAsyncGenHandleType) -> FilterAsyncGenReturnType:
        pass

    async def run(self, ctx: AgentContext, req: Any, handle: FilterHandleType) -> FilterResult:
        pass
```
- `run`: 协程函数
- `run_stream`: 异步生成器，用于实现流式效果

**对于不同的类型的 Filter，用户需要继承改写不同的方法**

## Filter 基本使用

trpc_agent 提供三种类型的拦截器，分别用于不同的处理阶段：

### ToolFilter（工具拦截器）

* **作用范围**：工具调用前后
* **触发时机**：当 Agent 调用工具时
* **注册方式**：使用 `@register_tool_filter` 装饰器

```python
@register_tool_filter("tool_filter")
class ToolFilter(BaseFilter):
    """工具拦截器示例"""

    async def run(self, ctx: AgentContext, req: Any, handle: FilterHandleType) -> FilterResult:
        print(f"\n\n==== run tool filter run start ===")
        # .. run before
        rsp = await handle()
        # .. run after
        print(f"\n\n==== run tool filter run end ===")
        return rsp


# 使用方式
@register_tool("get_weather", filters_name=["tool_filter"])
async def get_weather(location: str) -> str:
    """Get weather information for a location.

    Args:
        location: The location to get weather for

    Returns:
        Weather information string
    """
    return f"The weather in {location} is sunny with 72°F temperature."
```

注意事项：

- 这里只能继承 `run` 函数，因为框架调用是按照协程调用的，继承 `run_stream` 修改无效

### ModelFilter（模型拦截器）

* **作用范围**：模型调用前后和流式响应
* **触发时机**：当 Agent 调用 LLM 模型时
* **注册方式**：使用 `@register_model_filter` 装饰器

```python
@register_model_filter("model_filter")
class ModelFilter(BaseFilter):
    """模型拦截器示例"""
    
    async def run_stream(self, ctx: AgentContext, req: Any,
                         handle: FilterAsyncGenHandleType) -> FilterAsyncGenReturnType:
        print(f"\n\n==== run model filter run_stream start ===")
        async for event in handle():
            print(f"\n\n==== run model filter run_stream event ===")
            yield event
            if not event.is_continue:
                print(f"\n\n==== run model filter run_stream end ===")
                return
        print(f"\n\n==== run model filter run_stream end ===")

# 使用方式
model = OpenAIModel(
    model_name="deepseek-chat",
    api_key=os.environ.get("TRPC_AGENT_API_KEY", ""),
    base_url="https://api.deepseek.com/v1",
    filters_name=["model_filter"],
)
```

注意事项：

- 这里只能继承 `run_stream` 函数，因为框架调用是按照异步生成器调用的，继承 `run` 修改无效

### AgentFilter（代理拦截器）

* **作用范围**：代理调用前后
* **触发时机**：当 Runner 调用 Agent 时
* **注册方式**：使用 `@register_agent_filter` 装饰器

```python
@register_agent_filter("agent_filter")
class AgentFilter(BaseFilter):
    """代理拦截器示例"""
    
    async def run_stream(self, ctx: AgentContext, req: Any,
                         handle: FilterAsyncGenHandleType) -> FilterAsyncGenReturnType:
        print(f"\n\n==== run agent filter run_stream start ===")
        async for event in handle():
            print(f"\n\n==== run agent filter run_stream event ===")
            yield event
            if not event.is_continue:
                print(f"\n\n==== run agent filter run_stream end ===")
                return
        print(f"\n\n==== run agent filter run_stream end ===")

# 使用方式
agent = LlmAgent(
    name="assistant",
    model=model,  # Use the configured Deepseek model
    instruction="You are a helpful assistant with access to weather and calculation tools.",
    filters_name=["agent_filter"],
)

```

注意事项：

- 这里只能继承 `run_stream` 函数，因为框架调用是按照异步生成器调用的，继承 `run` 修改无效


## Callback 的使用

除了显示注册 Filter 类以外，框架还支持注册 Callback 函数的方式执行用户的回调函数，目前框架对于 Tool， Model， Agent三个模块支持设置回调函数；由于回调函数和 Filter 特性一样，内部 Callback 采用 Filter 的方式实现

### Agent Callback 使用

```python
async def before_agent_callback(context: InvocationContext):
  """Agent 执行前触发，返回非空值将跳过 Agent 调用并直接使用该返回值作为结果"""
  print(f'@before_agent_callback context: {type(context)}')
  return None


async def after_agent_callback(context: InvocationContext):
  """Agent 执行后触发，可用于记录日志、修改输出结果等后处理逻辑"""
  print(f'@after_agent_callback context: {type(context)}')
  return None


agent = LlmAgent(
    # ...
    before_agent_callback=before_agent_callback,  # Agent 执行前的回调，返回非空则跳过 Agent 调用
    after_agent_callback=after_agent_callback,     # Agent 执行后的回调
)
```
表示每次运行 Agent 之前和之后执行的回调函数

- before_agent_callback: 若返回结果不为空，agent 不会被调用；正常场景下，agent 调用一次该函数被调用一次
- after_agent_callback: 正常场景下，agent 调用一次该函数被调用一次

### Model Callback 使用

```python
async def before_model_callback(context: InvocationContext, llm_request: LlmRequest):
  print(f'@before_model_callback context: {type(context)}, llm_request: {type(llm_request)}')
  return None


async def after_model_callback(context: InvocationContext, llm_response: LlmResponse):
  print(f'@after_model_callback context: {type(context)}, llm_response: {type(llm_response)}')
  return None


async def on_model_error_callback(context: InvocationContext, llm_request: LlmRequest, error: Exception):
  print(f'@on_model_error_callback context: {type(context)}, llm_request: {type(llm_request)}, error: {error}')
  # 返回 LlmResponse 表示接管异常，并作为模型结果继续传递。
  # 返回 None 表示继续抛出原异常。
  return LlmResponse(content=Content(parts=[Part(text=f"模型调用失败：{error}")]))


agent = LlmAgent(
    # ...
    before_model_callback=before_model_callback,
    after_model_callback=after_model_callback,
    on_model_error_callback=on_model_error_callback,
)
```
表示每次运行用户的模型调用之前和之后执行的回调函数

- before_model_callback: 每次运行 model 之前执行的回调函数，若该返回值不为空，模型函数不会继续被调用, 模型调用一次该函数被调用一次
- after_model_callback: 每次运行 model 流式结果会被调用一次，若流式数据比较多，一次流式结果被调用一次
- on_model_error_callback: model 抛出异常时执行的回调函数。若返回非 `None` 的 `LlmResponse`，该响应会作为模型结果继续传递，原异常视为已接管；若返回 `None`，原异常会继续抛出

目前只有 `LlmAgent` 存在 before_model_callback、after_model_callback 和 on_model_error_callback 方法


### Tool Callback 使用

```python
def before_tool_callback(context: InvocationContext, tool: BaseTool, args: dict, response: Any):
  print(f'@before_tool_callback context: {type(context)}, tool: {type(tool)}, args: {type(args)}, response: {type(response)}')


def after_tool_callback(context: InvocationContext, tool: BaseTool, args: dict, response: Any):
  print(f'@after_tool_callback context: {type(context)}, tool: {type(tool)}, args: {type(args)}, response: {type(response)}')


def on_tool_error_callback(context: InvocationContext, tool: BaseTool, args: dict, error: Exception):
  print(f'@on_tool_error_callback context: {type(context)}, tool: {type(tool)}, args: {type(args)}, error: {error}')
  # 返回 dict 表示接管异常，并作为工具结果继续传递。
  # 返回 None 表示继续抛出原异常。
  return {"status": "failed", "message": str(error)}


agent = LlmAgent(
    # ...
    before_tool_callback=before_tool_callback,
    after_tool_callback=after_tool_callback,
    on_tool_error_callback=on_tool_error_callback,
)
```
表示每次运行用户的tool之前和之后执行的回调函数

- before_tool_callback: 每次运行用户的 tool 之前执行的回调函数，若该返回值不为空，工具不会被调用，工具调用一次该函数被调用一次
- after_tool_callback: 每次运行用户的 tool 并返回结果之后执行的回调函数，工具完成一次该函数被调用一次
- on_tool_error_callback: 工具抛出异常时执行的回调函数。若返回非 `None` 的 dict，该 dict 会作为工具结果继续传递，原异常视为已接管；若返回 `None`，原异常会继续抛出

目前只有 `LlmAgent` 存在 before_tool_callback、after_tool_callback 和 on_tool_error_callback 方法


### Model / Tool Error Callback 设计说明

#### 场景

在 Agent 调用模型或工具时，底层可能直接抛出 Python 异常。例如：

- 模型服务调用失败，如网络异常、鉴权失败、连接中断或服务端错误
- 工具访问外部系统失败，如 HTTP、数据库、缓存或第三方服务异常
- 工具参数校验通过了 callback，但业务执行阶段失败
- 模型或工具执行过程中需要释放资源、更新 trace 或通知前端状态

这类异常如果只沿着普通异常链路向外抛出，业务侧很难在“模型失败”或“工具失败”这个明确语义上做统一收尾。典型影响包括：

- 前端等待模型或工具结果，异常路径没有机会主动写入失败状态，可能出现 UI 挂起
- trace、日志、埋点只能依赖外层兜底，缺少 request/tool、args、error 等关键上下文
- 临时资源、锁、任务状态等清理逻辑需要分散在每个模型调用或工具内部处理

#### 修改原因

原有 callback 只有成功路径的 before/after：

- `before_model_callback`：模型执行前触发，可短路模型调用
- `after_model_callback`：模型正常返回结果后触发，可改写模型结果
- `before_tool_callback`：工具执行前触发，可短路工具调用
- `after_tool_callback`：工具正常返回结果后触发，可改写工具结果

问题在于：模型或工具抛出异常时，基础 Filter 生命周期会把异常标记到 `FilterResult.error` 并停止继续执行，`after_model_callback` / `after_tool_callback` 不会稳定执行。直接强行让 after callback 在异常路径也执行虽然可以解决“能收尾”的问题，但会改变 after 的语义：after 原本表示“已有正常结果之后的后处理”，如果同时承载成功和失败，会让 callback 使用方必须判断 `response` 到底是真实结果还是异常包装结果。

因此本次修改保持 after 机制不变，新增独立的 error callback，让成功路径和异常路径语义分离：

- 模型成功路径：`after_model_callback(context, llm_response)`
- 模型异常路径：`on_model_error_callback(context, llm_request, error)`
- 工具成功路径：`after_tool_callback(context, tool, args, response)`
- 工具异常路径：`on_tool_error_callback(context, tool, args, error)`

#### 解决方式

本次改动新增了 `ModelErrorCallback` / `ModelErrorCallbackFilter` 和 `ToolErrorCallback` / `ToolErrorCallbackFilter`：

- `BaseFilter.handle_error` 作为统一的异常处理阶段，在 `run` / `run_stream` 的 handle 阶段出现 `FilterResult.error` 时触发
- `LLMModel.generate_async` 将 `ModelErrorCallbackFilter` 加入模型调用的 filter 链
- `BaseTool.run_async` 将 `ToolErrorCallbackFilter` 加入工具调用的 filter 链

执行规则如下：

模型执行规则：

1. 模型正常返回：继续走原来的 `after_model_callback`
2. 模型抛出异常：进入 `on_model_error_callback`
3. `on_model_error_callback` 返回非 `None` 的 `LlmResponse`：表示业务接管异常，该响应会继续进入现有模型响应处理流程，包括 `after_model_callback`
4. `on_model_error_callback` 返回 `None`：表示不接管异常，框架继续抛出原始异常

工具执行规则：

1. 工具正常返回：继续走原来的 `after_tool_callback`
2. 工具抛出异常：进入 `on_tool_error_callback`
3. `on_tool_error_callback` 返回非 `None` 的 `dict`：表示业务接管异常，该 `dict` 会作为工具结果继续传递给后续流程
4. `on_tool_error_callback` 返回 `None`：表示不接管异常，框架继续抛出原始异常

示例：

```python
def on_model_error_callback(context: InvocationContext, llm_request: LlmRequest, error: Exception):
    return LlmResponse(content=Content(parts=[Part(text=f"模型调用失败：{error}")]))


def on_tool_error_callback(context: InvocationContext, tool: BaseTool, args: dict, error: Exception):
    # 可在这里更新前端状态、记录 trace、释放资源或构造结构化失败结果
    return {
        "status": "failed",
        "success": False,
        "message": str(error),
    }


agent = LlmAgent(
    # ...
    on_model_error_callback=on_model_error_callback,
    on_tool_error_callback=on_tool_error_callback,
)
```

如果希望保留原始异常行为，只做日志或清理，可以返回 `None`：

```python
def on_tool_error_callback(context: InvocationContext, tool: BaseTool, args: dict, error: Exception):
    logger.exception("tool %s failed with args=%s", tool.name, args)
    return None
```

#### 涉及代码

- `trpc_agent_sdk/filter/_base_filter.py`：新增 `handle_error` 生命周期阶段，并在 `run` / `run_stream` 的 handle 阶段统一调用
- `trpc_agent_sdk/agents/_callback.py`：新增 `ModelErrorCallback` / `ModelErrorCallbackFilter` 和 `ToolErrorCallback` / `ToolErrorCallbackFilter`
- `trpc_agent_sdk/models/_llm_model.py`：把 `ModelErrorCallbackFilter` 加入模型 filter 链
- `trpc_agent_sdk/filter/_run_filter.py`：流式 Filter 遇到 `FilterResult.error` 时重新抛出异常，避免模型异常被吞成空响应
- `trpc_agent_sdk/tools/_base_tool.py`：把 `ToolErrorCallbackFilter` 加入工具 filter 链
- `trpc_agent_sdk/agents/_llm_agent.py`：新增 `on_model_error_callback` 和 `on_tool_error_callback` 配置项
- `trpc_agent_sdk/agents/__init__.py`：导出 `ModelErrorCallback`、`ModelErrorCallbackFilter`、`ToolErrorCallback` 和 `ToolErrorCallbackFilter`
- `tests/models/test_llm_model.py`：覆盖 model error callback 被调用、可接管异常、接管后仍触发 after_model_callback
- `tests/tools/test_base_tool.py`：覆盖 error callback 被调用、可接管异常两类场景
- `tests/agents/test_callback.py`：覆盖 `ModelErrorCallbackFilter` 和 `ToolErrorCallbackFilter` 初始化

#### 与 agent/model callback 的关系

当前对齐 ADK 支持了 model/tool 维度的 error callback。agent 维度目前仍只有 before/after callback，没有独立的 `on_agent_error_callback`。


## Filter 生命周期

每个 Filter 都有完整的生命周期管理：

1. **初始化**：Filter 实例被创建并注册
2. **before 处理**：请求执行前的预处理
3. **执行阶段**：实际的业务逻辑执行
4. **_after_every_stream**：仅流式场景下对于每个流式回复的后处理
5. **after 处理**：请求执行后的后处理

## 实际应用示例

完整的使用示例见：
- [examples/filter_with_agent/](../../../examples/filter_with_agent/)
- [examples/filter_with_model/](../../../examples/filter_with_model/)
- [examples/filter_with_tool/](../../../examples/filter_with_tool/)


## FAQ

### Q：当回复流式数据的时候，期望中途立即退出

解决方式如下：

```python
@register_agent_filter("agent_filter")
class AgentFilter(BaseFilter):
    """代理拦截器示例"""
    
    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        print(f"==== 代理调用前处理 ====")
        print(f"请求: {req}, 上下文: {type(ctx).__name__}")
        # 设置不继续，流式会立即中断
        rsp.is_continue = False
        
    async def _after(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        print(f"==== 代理调用后处理 ====")
        print(f"请求: {req}, 上下文: {type(ctx).__name__}")
```
