# CodeAct

CodeAct is an optional execution mode for `LlmAgent`. When enabled, the model
generates Python code to orchestrate tools, process data, and submit results
instead of relying only on traditional Function Calling.

The CodeAct design in tRPC-Agent-Python is inspired by
[NVIDIA NeMo Labs OO Agents (NOOA)](https://github.com/NVIDIA-NeMo/labs-OO-Agents)
and integrated with the existing Agent, Tool, Runner, and Event systems.

## Overview

CodeAct provides two capabilities:

### CodeAct Tools

The model calls tools configured on the Agent through `self.<tool>(...)` in a
persistent Python environment:

```python
numbers = await self.load_numbers(count=100000)
total = sum(number * number for number in numbers if number % 7 == 0)
return_result({"value": total})
```

Large tool results can remain in the local ObjectStore. Later cells continue
using the same variable without repeatedly serializing the full object into the
model context.

For complete setup, tests, and result analysis, see the
[CodeAct Tools example README](../../../examples/codeact_with_tools/README.md).

### Generation Method

`CodeActTool` can dynamically implement a function whose body consists only of
`...`. The function name, parameters, type annotations, and docstring define
the implementation contract:

```python
def analyze_order_operations(
    orders: list[OrderRecord],
    overdue_days: int = 3,
) -> OrderOperationsReport:
    """Analyze order statuses, amounts and overdue orders."""
    ...
```

Successful generated code can be stored as a candidate, manually approved, and
then reused directly to reduce later model calls.

For the complete generation, approval, reuse, and rollback flow, see the
[Generation Method example README](../../../examples/codeact_with_generation_method/README.md).

## Features

### Python-Based Tool Orchestration

The model can combine multiple tools with loops, branches, comprehensions,
aggregation, exception handling, and temporary variables. You do not need to
design a separate tool for every intermediate step.

CodeAct does not replace the existing tool system. `FunctionTool`,
`BaseToolSet`, argument conversion, `InvocationContext`, filters, and
before/after callbacks continue to work through the existing path. The
difference is that the model invokes them through the Python `self` proxy.

### Persistent Python Variables

All generated cells within one invocation share the same Python globals. Data
loaded in the first cell remains available in later cells without another tool
call.

State exists only for the current invocation. The Runtime releases its globals
and ObjectStore when the invocation finishes.

### Large Objects Retained by Reference

Tool results pass through `CodeActObjectStore.wrap(...)`:

- Small JSON-serializable values are returned inline.
- Large lists, dictionaries, and complex objects remain in the ObjectStore.
- The Python environment receives a `CodeActObjectRef` to the real object.
- The model sees only the type, length, and a bounded preview.

An ObjectRef supports indexing, slicing, iteration, `len()`, and public method
calls, so generated code can still process the underlying object directly.

ObjectRef prevents the framework from automatically transporting the entire
object. It is not a mandatory data-loss-prevention mechanism. If generated code
explicitly runs `print(list(value))`, the full contents still enter the model
context.

### Progressive Capability Discovery

The model can inspect available tools and objects with `doc()`:

```python
print(doc(self))
print(doc(self.load_numbers))
print(doc(numbers))
```

This avoids keeping every complete tool schema and large object value in every
model request.

### Structured Result Validation

The model submits its final result with `return_result(value)`. When
`output_schema` is configured, the framework validates the value with
Pydantic:

```python
class CalculationResult(BaseModel):
    expression: str
    value: int
```

If validation fails, the framework emits a `codeact.validation_error` event and
allows the model to correct the value in another cell. A valid result produces
a final Event with `object="codeact.result"` and ends the Agent loop without an
extra model call for a textual summary.

### One Cell per Model Response

Only the first fenced `python` block in each model response is executed. Text
and additional code blocks after it are not speculatively executed. The model
must wait for the actual execution result before deciding the next action.

`return_result(...)` immediately terminates the current cell, preventing code
after the final result from producing additional side effects.

### Model-Implemented Functions

`CodeActTool` accepts only functions whose body contains an optional docstring
and `...`. On invocation, it:

1. Validates input against the function signature.
2. Creates a nested CodeAct invocation.
3. Exposes the current arguments through `self.get_function_arguments()`.
4. Allows generated code to call explicitly configured or inherited tools.
5. Validates the result against the function return type.
6. Returns the validated value as a regular Tool Response to the parent Agent.

This feature does not modify the function body in the source file. A regular
`FunctionTool` still runs deterministic developer-written code. Only functions
explicitly wrapped in `CodeActTool` are implemented by the model.

### Generated Implementation Approval and Reuse

`CodeActTool` can save generated code that passes input and output validation.
After a candidate is approved, later calls execute it directly in the CodeAct
Runtime without creating a nested Agent or asking a model to regenerate it.

The framework computes a contract hash from the function module, name,
signature, docstring, and input/output schemas. It also computes a capability
hash from callable tools. An implementation is not reused automatically after
its contract or capabilities change.

### Separation from CodeExecutor

CodeAct Runtime executes model-generated control code. CodeExecutor handles
ordinary code blocks, Skill scripts, and user-supplied code. They can be
configured together but have separate responsibilities:

```text
Model control code → CodeAct Runtime
Skill / user code → Local, Container, or Cube CodeExecutor
```

When CodeAct is enabled, Python from model responses is sent only to
`code_act.runtime`; `code_executor` does not execute the same block again.
Business tools can still call CodeExecutor through explicit entry points to
run user code in a container or remote sandbox.

## Workflows

### CodeAct Tools Workflow

```text
User request
→ LlmAgent injects the CodeAct protocol
→ Model generates one Python cell
→ CodeActResponseProcessor extracts the code
→ CodeAct Runtime executes the cell
→ self.<tool>(...) calls an existing tool
→ Large results remain as ObjectRef values
→ Execution observations return to the model
→ Later cells reuse existing variables
→ return_result(...) submits and validates the result
```

### Generation Method Workflow

```text
Parent Agent calls CodeActTool
→ Validate function arguments
→ Find a compatible approved implementation
├─ Found: Runtime executes approved code directly
└─ Not found: nested CodeAct Agent generates and executes code
→ Validate the function return type
→ Save a successful candidate when a Store is configured
→ Return to the parent Agent
```

## Architecture

### Request Processing

After `LlmAgent.code_act` is configured, the framework injects the CodeAct
protocol into the model request. It instructs the model to:

- Generate one Python code block per response.
- Call tools with `await self.<tool>(...)`.
- Inspect tools and objects with `doc()`.
- Submit intermediate observations with `print(...)`.
- Submit the final result with `return_result(...)`.
- Finish within `max_iterations`.

When the Runtime supports tools, Agent tools are not sent as ordinary provider
Function Calling schemas. The model accesses them through the `self` proxy.

### Response Processing

`CodeActResponseProcessor` extracts the first Python block, injects the
`return_result(...)` protocol, and passes the code to the Runtime. The Runtime
returns stdout, stderr, and execution status, from which the Processor creates
code execution Events.

If no final result was submitted, the observation enters history and triggers
another model call. A valid final result ends the Agent loop.

### Persistent Runtime

The default `InProcessCodeActRuntime` keeps this state per invocation:

```python
class _ExecutionState:
    globals: dict[str, Any]
    store: CodeActObjectStore
    agent_proxy: CodeActAgentProxy
```

- `globals` stores variables shared between cells.
- `store` retains large or complex Python objects.
- `agent_proxy` exposes Agent tools as `self.<tool>(...)`.

Cells are compiled with `ast.PyCF_ALLOW_TOP_LEVEL_AWAIT`, so generated code can
call asynchronous tools at the top level without creating an event loop.

### Tool Proxy

`CodeActAgentProxy` resolves the current Agent's `BaseTool` and `BaseToolSet`
instances and maps them by name:

```text
self.load_numbers(count=100000)
→ CodeActAgentProxy
→ BaseTool.run_async(...)
→ FunctionTool
→ load_numbers(...)
```

The proxy preserves existing tool behavior, including:

- Required argument and type validation.
- Automatic `tool_context` injection.
- Synchronous and asynchronous function support.
- Filters and callbacks.
- Per-invocation tool call limits.

Tool calls currently require keyword arguments and `await`:

```python
numbers = await self.load_numbers(count=100000)
```

Positional arguments and progress-streaming tools are not currently supported.

## Basic Usage

### Enable CodeAct Tools

```python
from pydantic import BaseModel

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.codeact import CodeActConfig
from trpc_agent_sdk.tools import FunctionTool


class CalculationResult(BaseModel):
    expression: str
    value: int


def load_numbers(count: int) -> list[int]:
    """Return integers from zero up to, but excluding, count."""
    return list(range(count))


agent = LlmAgent(
    name="codeact_calculator",
    model=model,
    instruction="Use load_numbers once and calculate the requested result.",
    tools=[FunctionTool(load_numbers)],
    code_act=CodeActConfig(),
    output_schema=CalculationResult,
)
```

The user instruction only needs to name the tool. It does not need to teach the
user or business prompt to write `await self.load_numbers(...)`.
`CodeActConfig` adds protocol instructions that guide the model to translate
tool names into correct Python calls.

For runnable code, expected output, and a real Tool Calling comparison, see the
[CodeAct Tools example README](../../../examples/codeact_with_tools/README.md).

### Enable a Generation Method

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.tools import CodeActTool


def analyze_order_operations(
    orders: list[OrderRecord],
    overdue_days: int = 3,
) -> OrderOperationsReport:
    """Analyze order statuses, amounts and overdue orders."""
    ...


agent = LlmAgent(
    name="order_agent",
    model=model,
    tools=[CodeActTool(analyze_order_operations)],
)
```

`CodeActTool` generates an implementation from the function contract and
validates its result against `OrderOperationsReport`. For complete business
inputs, generated code, and output verification, see the
[Generation Method example README](../../../examples/codeact_with_generation_method/README.md).

## Configuration

### CodeActConfig

```python
from trpc_agent_sdk.codeact import CodeActConfig
from trpc_agent_sdk.codeact import InProcessCodeActRuntime

config = CodeActConfig(
    runtime=InProcessCodeActRuntime(),
    max_iterations=10,
    require_result=True,
)
```

- `runtime`: Runtime for model-generated code. The default is
  `InProcessCodeActRuntime`.
- `max_iterations`: Maximum model/code iterations per invocation. The default
  is `10`.
- `require_result`: Whether the model must finish through
  `return_result(...)`.
- `text_only_retry_message`: Retry feedback when the model returns only text.

### CodeActTool

```python
tool = CodeActTool(
    analyze_order_operations,
    model=model,
    tools=capability_tools,
    code_act=CodeActConfig(),
    filters=filters,
    implementation_store=store,
    implementation_policy="prefer_approved",
)
```

- `model`: Model that generates the implementation. If omitted, the model from
  the calling context is used.
- `tools`: Additional tools available to generated code.
- `code_act`: CodeAct configuration for nested invocations.
- `filters`: Filters applied to the Tool.
- `implementation_store`: Candidate and approved implementation store.
- `implementation_policy`: Strategy for generation and approved-code reuse.

## Implementation Persistence

### Policies

`implementation_policy` supports:

- `dynamic`: Always generate with the model; save successful candidates when a
  Store is configured.
- `prefer_approved`: Run a compatible approved implementation first, or
  generate one when no approved version exists.
- `approved_only`: Run only a compatible approved implementation and fail when
  none exists.

When a non-`dynamic` policy has no explicit Store,
`InMemoryCodeActImplementationStore` is used by default. It does not survive a
process restart and is not shared across workers. Configure a persistent Store
for production.

### Approval and Rollback

```python
candidates = await tool.list_implementations()
candidate = candidates[-1]

await tool.approve(candidate.version, approved_by="reviewer")
```

Approve an older version again to roll back:

```python
await tool.approve(previous_version, approved_by="rollback")
```

Approval only selects which code version to reuse. The application must test
business correctness, security, permissions, and performance before approval.

### Storage Backends

The framework provides:

- `InMemoryCodeActImplementationStore` for single-process tests.
- `FileCodeActImplementationStore` for local file persistence.
- `RedisCodeActImplementationStore` for shared multi-worker persistence.
- `SqlCodeActImplementationStore` for relational database persistence.

```python
from trpc_agent_sdk.codeact import FileCodeActImplementationStore
from trpc_agent_sdk.codeact import RedisCodeActImplementationStore
from trpc_agent_sdk.codeact import SqlCodeActImplementationStore

file_store = FileCodeActImplementationStore(".codeact")
redis_store = RedisCodeActImplementationStore(
    redis_url="redis://localhost:6379/0",
)
sql_store = SqlCodeActImplementationStore(
    db_url="sqlite:///codeact.db",
)
```

These backends reuse `trpc_agent_sdk.storage`. Existing `FileStorage`,
`RedisStorage`, or `SqlStorage` instances can also be injected with `storage=`.
Call `await store.close()` during application shutdown. When external Storage
is injected, its lifecycle remains the caller's responsibility.

For policy transitions, automatic approval demonstrations, and production
guidance, see the
[Generation Method example README](../../../examples/codeact_with_generation_method/README.md).

## Examples

### CodeAct Tools

[examples/codeact_with_tools/README.md](../../../examples/codeact_with_tools/README.md)
covers:

- Calling tools through `self.<tool>(...)`.
- Reusing variables across two CodeAct cells.
- Processing large objects through ObjectRef.
- Returning structured results.
- Comparing request size, tokens, and elapsed time with real Tool Calling.
- Testing different object sizes.
- Development and production considerations.

### Generation Method

[examples/codeact_with_generation_method/README.md](../../../examples/codeact_with_generation_method/README.md)
covers:

- Implementing an ellipsis-bodied function with `CodeActTool`.
- Viewing the actual model-generated code.
- Generating and approving a candidate with `prefer_approved`.
- Executing approved code without model generation under `approved_only`.
- File, Redis, and SQL Store use cases.
- Candidate testing, approval, and rollback guidance.

Refer to the corresponding example README for commands, test inputs, output
comparisons, and acceptance criteria. This document does not duplicate
example-specific execution results.

## Comparison with Traditional Tool Calling

Traditional Tool Calling:

```text
Send tool schemas to the model
→ Model returns a FunctionCall
→ Framework executes the tool
→ Serialize the complete result as a FunctionResponse
→ Send the result to the model again
```

CodeAct Tools:

```text
Model generates Python
→ self.<tool>(...) invokes a tool
→ Result remains in the Python Runtime
→ Loops, filtering, and aggregation happen locally
→ Only necessary observations and the final result are submitted
```

The primary benefit of CodeAct is not a different function-call syntax. It:

- Expresses complex control flow in Python.
- Avoids repeated JSON serialization of large intermediate results.
- Reduces tokens consumed by large objects in model context.
- Continues processing real Python objects within one invocation.

For simple arguments and small results, traditional Tool Calling is more
direct and easier to approve or audit call by call.

## Use Cases

CodeAct is suitable for:

- Filtering and aggregating large lists, tables, or complex objects.
- Tasks requiring loops, branches, and multi-step computation.
- Tool pipelines with dependent intermediate data.
- Large tool results that produce a small final answer.
- Generating implementations from typed function contracts.
- Managing, approving, and reusing generated implementations.

Traditional Tool Calling is usually better for:

- One-off API calls with simple arguments.
- Calls requiring independent human confirmation or auditing.
- High-privilege side effects such as payments or deletion.
- Small tasks that do not need Python control flow.

## Limitations

### Processes and Workers

The `globals` and ObjectStore of `InProcessCodeActRuntime` live in the current
Worker process. Later model calls in the same invocation must continue on that
Worker.

State is lost when:

- The request moves to another Worker.
- The Worker restarts.
- The process crashes or fails over.
- The invocation completes and releases its Runtime.

Session Events can store code and execution output, but they cannot restore
arbitrary live Python objects. Redis and SQL Implementation Stores persist
Generation Method code versions only; they do not make current invocation
variables or ObjectRef values distributed.

Cross-worker execution requires an externally stateful
`BaseCodeActRuntime` with remote object handles, lifecycle management, and
concurrency control.

### Current Runtime Support

The only built-in implementation is `InProcessCodeActRuntime`. A Container or
remote sandbox CodeAct Runtime is not currently provided. Container/Cube
CodeExecutor cannot directly replace it because ordinary CodeExecutor does not
provide the `self` tool proxy, persistent live Python objects, or ObjectRef
protocol.

### Tool Call Restrictions

- Calls through `self` must use keyword arguments.
- Asynchronous capabilities must use `await`.
- Progress-streaming Tools are not currently supported by the CodeAct proxy.
- ObjectRef cannot be transported directly between processes.
- Generated code can still explicitly print a large object.

## Security

`InProcessCodeActRuntime` executes model-generated Python in the Agent host
process with the same filesystem, network, environment-variable, and process
permissions. It is not a security sandbox and is suitable only for
development, testing, and trusted input.

Before using CodeAct in production:

- Provide OS-level isolation with a container, VM, or remote sandbox.
- Limit CPU, memory, execution time, filesystem access, and process count.
- Restrict network egress, environment variables, and credentials.
- Expose only least-privilege tools through `self`.
- Add authorization, idempotency, auditing, and human confirmation to writes.
- Test and security-review Generation Method candidates before approval.
- Provide Worker affinity or a distributed Runtime for each invocation.
- Run Skill and user code through Container/Cube CodeExecutor instead of
  mixing it with CodeAct control code.

Do not execute untrusted model-generated code directly in a multi-tenant
production service until an isolated Runtime is available.
