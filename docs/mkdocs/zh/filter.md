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
表示每次运行用户的tool之前和之后执行的回调函数

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


agent = LlmAgent(
    # ...
    before_model_callback=before_model_callback,
    after_model_callback=after_model_callback,
)
```
表示每次运行用户的模型调用之前和之后执行的回调函数

- before_model_callback: 每次运行 model 之前执行的回调函数，若该返回值不为空，模型函数不会继续被调用, 模型调用一次该函数被调用一次
- after_model_callback: 每次运行 model 流式结果会被调用一次，若流式数据比较多，一次流式结果被调用一次

目前只有 `LlmAgent` 存在 before_model_callback 和 after_model_callback 方法


### Tool Callback 使用

```python
def before_tool_callback(context: InvocationContext, tool: BaseTool, args: dict, response: Any):
  print(f'@before_tool_callback context: {type(context)}, tool: {type(tool)}, args: {type(args)}, response: {type(response)}')


def after_tool_callback(context: InvocationContext, tool: BaseTool, args: dict, response: Any):
  print(f'@after_tool_callback context: {type(context)}, tool: {type(tool)}, args: {type(args)}, response: {type(response)}')


agent = LlmAgent(
    # ...
    before_tool_callback=before_tool_callback,
    after_tool_callback=after_tool_callback,
)
```
表示每次运行用户的tool之前和之后执行的回调函数

- before_tool_callback: 每次运行用户的 tool 之前执行的回调函数，若该返回值不为空，工具不会被调用，工具调用一次该函数被调用一次
- after_tool_callback: 每次运行用户的tool之后执行的回调函数, 工具调用一次该函数被调用一次

目前只有 `LlmAgent` 存在 before_tool_callback 和 after_tool_callback 方法


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
