# Filters

trpc_agent provides a powerful Filter mechanism to intercept and process the request-response flow. Filters can handle requests at different stages, including tool invocations, model invocations, and agent invocations, offering developers flexible extensibility.

## Core Features of Filter

- **Request Interception**: Preprocessing and validation before request execution
- **Response Handling**: Post-processing and result modification after request execution
- **Streaming Support**: Real-time processing of streaming responses
- **Chain Invocation**: Sequential execution of multiple filters

The Filter base class provides two interception methods for regular invocations and streaming invocations respectively:

```python
class BaseFilter(FilterABC):
    async def run_stream(self, ctx: AgentContext, req: Any,
                         handle: FilterAsyncGenHandleType) -> FilterAsyncGenReturnType:
        pass

    async def run(self, ctx: AgentContext, req: Any, handle: FilterHandleType) -> FilterResult:
        pass
```
- `run`: Coroutine function
- `run_stream`: Async generator for implementing streaming behavior

**For different types of Filters, users need to inherit and override different methods**

## Basic Usage of Filter

trpc_agent provides three types of filters, each for a different processing stage:

### ToolFilter

* **Scope**: Before and after tool invocations
* **Trigger**: When an Agent invokes a tool
* **Registration**: Using the `@register_tool_filter` decorator

```python
@register_tool_filter("tool_filter")
class ToolFilter(BaseFilter):
    """Tool filter example"""

    async def run(self, ctx: AgentContext, req: Any, handle: FilterHandleType) -> FilterResult:
        print(f"\n\n==== run tool filter run start ===")
        # .. run before
        rsp = await handle()
        # .. run after
        print(f"\n\n==== run tool filter run end ===")
        return rsp


# Usage
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

Notes:

- Only the `run` method should be overridden here, as the framework invokes it as a coroutine. Overriding `run_stream` has no effect.

### ModelFilter

* **Scope**: Before and after model invocations and streaming responses
* **Trigger**: When an Agent invokes an LLM model
* **Registration**: Using the `@register_model_filter` decorator

```python
@register_model_filter("model_filter")
class ModelFilter(BaseFilter):
    """Model filter example"""
    
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

# Usage
model = OpenAIModel(
    model_name="deepseek-chat",
    api_key=os.environ.get("TRPC_AGENT_API_KEY", ""),
    base_url="https://api.deepseek.com/v1",
    filters_name=["model_filter"],
)
```

Notes:

- Only the `run_stream` method should be overridden here, as the framework invokes it as an async generator. Overriding `run` has no effect.

### AgentFilter

* **Scope**: Before and after agent invocations
* **Trigger**: When the Runner invokes an Agent
* **Registration**: Using the `@register_agent_filter` decorator

```python
@register_agent_filter("agent_filter")
class AgentFilter(BaseFilter):
    """Agent filter example"""
    
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

# Usage
agent = LlmAgent(
    name="assistant",
    model=model,  # Use the configured Deepseek model
    instruction="You are a helpful assistant with access to weather and calculation tools.",
    filters_name=["agent_filter"],
)

```

Notes:

- Only the `run_stream` method should be overridden here, as the framework invokes it as an async generator. Overriding `run` has no effect.


## Usage of Callback

In addition to explicitly registering Filter classes, the framework also supports registering callback functions to execute user-defined callbacks. Currently, the framework supports setting callback functions for the Tool, Model, and Agent modules. Since callbacks share the same characteristics as Filters, the internal Callback implementation is based on the Filter mechanism.

### Agent Callback Usage

```python
async def before_agent_callback(context: InvocationContext):
  """Triggered before Agent execution. Returning a non-null value will skip the Agent invocation and use the returned value as the result directly."""
  print(f'@before_agent_callback context: {type(context)}')
  return None


async def after_agent_callback(context: InvocationContext):
  """Triggered after Agent execution. Can be used for logging, modifying output results, and other post-processing logic."""
  print(f'@after_agent_callback context: {type(context)}')
  return None


agent = LlmAgent(
    # ...
    before_agent_callback=before_agent_callback,  # Callback before Agent execution; returns non-null to skip Agent invocation
    after_agent_callback=after_agent_callback,     # Callback after Agent execution
)
```
Represents callback functions executed before and after each Agent tool run.

- before_agent_callback: If the return value is non-null, the agent will not be invoked. Under normal circumstances, this function is called once per agent invocation.
- after_agent_callback: Under normal circumstances, this function is called once per agent invocation.

### Model Callback Usage

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
Represents callback functions executed before and after each model invocation.

- before_model_callback: Callback function executed before each model run. If the return value is non-null, the model function will not be invoked. This function is called once per model invocation.
- after_model_callback: Called once per streaming result of each model run. If there are many streaming data chunks, this function is called once per streaming result.

Currently, only `LlmAgent` has the before_model_callback and after_model_callback methods.


### Tool Callback Usage

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
Represents callback functions executed before and after each tool run.

- before_tool_callback: Callback function executed before each tool run. If the return value is non-null, the tool will not be invoked. This function is called once per tool invocation.
- after_tool_callback: Callback function executed after each tool run. This function is called once per tool invocation.

Currently, only `LlmAgent` has the before_tool_callback and after_tool_callback methods.


## Filter Lifecycle

Each Filter has a complete lifecycle management:

1. **Initialization**: The Filter instance is created and registered
2. **Before Processing**: Preprocessing before request execution
3. **Execution Phase**: Actual business logic execution
4. **_after_every_stream**: Post-processing for each streaming response (streaming scenarios only)
5. **After Processing**: Post-processing after request execution

## Practical Examples

For complete usage examples, see:
- [examples/filter_with_agent/](../../../examples/filter_with_agent/)
- [examples/filter_with_model/](../../../examples/filter_with_model/)
- [examples/filter_with_tool/](../../../examples/filter_with_tool/)


## FAQ

### Q: How to exit immediately while streaming response data midway?

The solution is as follows:

```python
@register_agent_filter("agent_filter")
class AgentFilter(BaseFilter):
    """Agent filter example"""
    
    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        print(f"==== Before agent invocation processing ====")
        print(f"Request: {req}, Context: {type(ctx).__name__}")
        # Set is_continue to False to interrupt the stream immediately
        rsp.is_continue = False
        
    async def _after(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        print(f"==== After agent invocation processing ====")
        print(f"Request: {req}, Context: {type(ctx).__name__}")
```
