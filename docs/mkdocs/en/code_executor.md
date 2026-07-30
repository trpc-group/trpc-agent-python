# Agent Code Executor

To provide Agents with a high degree of flexibility, there are times when an Agent needs to generate and execute code. The tRPC-Agent-Python framework supports CodeExecutor for this scenario.

When this feature is enabled, if the LLM returns text containing code snippets, the framework will invoke the corresponding CodeExecutor to execute the code and return the execution results to the LLM, which can then continue generating responses based on those results.

## Code Executor Types

Three types of code executors are currently available:

### UnsafeLocalCodeExecutor

**Features:**
- Executes LLM-generated code within the Agent's own process
- Non-sandboxed environment, directly uses the local Python/Bash runtime; currently only supports `Python/Bash`
- Fast execution speed, no Docker environment required
- **Security Warning**: LLM-generated code may pose risks and is not suitable for production environments

**Use Cases:**
- Development and testing environments
- Trusted code execution scenarios
- Scenarios requiring rapid iteration and debugging

### ContainerCodeExecutor

**Features:**
- Agent dispatches code snippets to a Docker container for execution; currently only supports `Python/Bash`
- Sandboxed environment, providing better isolation and security
- Supports custom Docker images or Dockerfiles
- Requires Docker environment

**Use Cases:**
- Production environments
- Scenarios requiring execution of untrusted code
- Scenarios requiring environment isolation

### CubeCodeExecutor

**Features:**
- Agent dispatches code snippets to a remote Cube/E2B sandbox for execution; supports `Python/Bash`
- Strong sandboxed environment running on a remote host, suitable for executing untrusted code at scale
- Decoupled lifecycle: the same sandbox can be re-attached across processes via `sandbox_id` (`create` / `attach` / `create_or_recreate` factories)
- Ships an optional `CubeWorkspaceRuntime` that adds per-execution workspace directories, file upload/download (single files or whole directories via tar), and structured program runs — useful for the Skill subsystem
- Requires the optional `[cube]` extra (`pip install 'trpc-agent-py[cube]'`, which installs `e2b-code-interpreter`) and access to a Cube/E2B-compatible gateway

**Use Cases:**
- Production environments where Docker is not available on the agent host
- Scenarios requiring strong remote isolation for untrusted code
- Long-lived skill/code execution that needs a persistent workspace surviving across multiple `execute_code` calls
- Multi-tenant agent platforms that share a remote sandbox fleet

## Usage Examples

When creating an LlmAgent, build a CodeExecutor and configure the `code_executor` parameter to enable code execution functionality.


### Building a CodeExecutor

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import UnsafeLocalCodeExecutor
from trpc_agent_sdk.code_executors import ContainerCodeExecutor
# Cube is an optional extra (`pip install 'trpc-agent-py[cube]'`)
from trpc_agent_sdk.code_executors.cube import CubeCodeExecutor
from trpc_agent_sdk.code_executors.cube import CubeCodeExecutorConfig
from trpc_agent_sdk.log import logger

async def _create_code_executor(code_executor_type: str = "unsafe_local") -> BaseCodeExecutor:
    """Create a code executor.

    Args:
        code_executor_type: Type of code executor to use. Options:
            - "unsafe_local": Use UnsafeLocalCodeExecutor (default, no Docker required)
            - "container": Use ContainerCodeExecutor (requires Docker)
            - "cube": Use CubeCodeExecutor (requires the [cube] extra and a Cube/E2B gateway)
            - None: Auto-detect from environment variable CODE_EXECUTOR_TYPE,
                    or default to "unsafe_local"

    Returns:
        BaseCodeExecutor instance.

    Raises:
        RuntimeError: If container type is requested but Docker is not available.
            The error message will include detailed instructions on how to fix the issue.
    """
    # Get executor type from environment variable if not specified
    if code_executor_type == "unsafe_local":
        return UnsafeLocalCodeExecutor(timeout=10)
    elif code_executor_type == "container":
        # ContainerCodeExecutor will raise a clear error if Docker is not available
        # The error message includes detailed instructions on how to fix the issue
        executor = ContainerCodeExecutor(image="python:3-slim", error_retry_attempts=1)
        logger.info("ContainerCodeExecutor initialized successfully")
        return executor
    elif code_executor_type == "cube":
        # CubeCodeExecutor reads E2B_API_URL / E2B_API_KEY / CUBE_TEMPLATE_ID
        # from the environment when the corresponding cfg fields are unset.
        # `create()` opens a fresh remote sandbox; pass `sandbox_id=...` in
        # the cfg to attach to an existing one instead.
        cfg = CubeCodeExecutorConfig(execute_timeout=30.0, idle_timeout=600)
        executor = await CubeCodeExecutor.create(cfg)
        logger.info("CubeCodeExecutor initialized: sandbox_id=%s", executor.sandbox_id)
        return executor
    else:
        raise ValueError(f"Invalid code executor type: {code_executor_type}. "
                         "Valid options are: 'unsafe_local', 'container', 'cube'")

```

### Using UnsafeLocalCodeExecutor

```python
# ...
def create_agent() -> LlmAgent:
    """Create an agent with code execution capabilities.

    The agent can:
    - Execute Python code blocks generated by the LLM
    - Use tools like get_weather_report
    - Perform calculations and data processing through code execution

    Note: UnsafeLocalCodeExecutor executes code in the current process context.
    For production use, consider using ContainerCodeExecutor for better security.
    """
    # Select unsafe_local
    executor = _create_code_executor(code_executor_type="unsafe_local")
    agent = LlmAgent(
        name="code_assistant",
        description="Code execution assistant",
        model=_create_model(),  # You can change this to your preferred model
        instruction=INSTRUCTION,
        code_executor=executor,  # Enables code execution functionality
    )
    return agent


root_agent = create_agent()
```

**Execution Result Example:**

![UnsafeLocalCodeExecutor Execution Result](../assets/imgs/local0.png)
![UnsafeLocalCodeExecutor Execution Result 1](../assets/imgs/local1.png)

### Using ContainerCodeExecutor

```python

# ...
def create_agent() -> LlmAgent:
    """Create an agent with code execution capabilities.

    The agent can:
    - Execute Python code blocks generated by the LLM
    - Use tools like get_weather_report
    - Perform calculations and data processing through code execution

    Note: UnsafeLocalCodeExecutor executes code in the current process context.
    For production use, consider using ContainerCodeExecutor for better security.
    """
    # Select container
    executor = _create_code_executor(code_executor_type="container")
    agent = LlmAgent(
        name="code_assistant",
        description="Code execution assistant",
        model=_create_model(),  # You can change this to your preferred model
        instruction=INSTRUCTION,
        code_executor=executor,  # Enables code execution functionality
    )
    return agent

# Ensure Docker is installed and running before use
# Linux: sudo systemctl start docker
# Windows/Mac: Start Docker Desktop
```

**Execution Result Example:**

![ContainerCodeExecutor Execution Result](../assets/imgs/container0.png)
![ContainerCodeExecutor Execution Result 1](../assets/imgs/container1.png)

### Using CubeCodeExecutor

```python
# ...
async def create_agent() -> LlmAgent:
    """Create an agent backed by a remote Cube/E2B sandbox.

    Required environment (read by CubeCodeExecutorConfig.resolve_*):
    - E2B_API_URL:      Cube/E2B-compatible gateway URL
    - E2B_API_KEY:      API key for the gateway
    - CUBE_TEMPLATE_ID: Cube template id (e.g. `std-XXXXXXXX`)

    Note: `_create_code_executor` is async because `CubeCodeExecutor.create`
    opens the remote sandbox over the network. The executor owns the
    sandbox; call `await executor.destroy()` when the agent shuts down to
    free the remote resource. `executor.close()` only drops the local
    handle and lets the sandbox idle out on its own.
    """
    # Select cube
    executor = await _create_code_executor(code_executor_type="cube")
    agent = LlmAgent(
        name="code_assistant",
        description="Code execution assistant",
        model=_create_model(),  # You can change this to your preferred model
        instruction=INSTRUCTION,
        code_executor=executor,  # Enables code execution functionality
    )
    return agent

# Install the optional extra before use:
#   pip install 'trpc-agent-py[cube]'
# And export the gateway credentials:
#   export E2B_API_URL=...
#   export E2B_API_KEY=...
#   export CUBE_TEMPLATE_ID=...
```

#### Attaching to an existing sandbox

`CubeCodeExecutor` exposes three async factories so callers can choose the
lifecycle policy explicitly. All three read the bound sandbox id from
`cfg.sandbox_id` so it is the single source of truth:

```python
# 1. Strict create-or-attach: when cfg.sandbox_id is set, attach and assert
#    the sandbox is RUNNING; otherwise create a fresh one.
executor = await CubeCodeExecutor.create(cfg)

# 2. Attach-only: requires cfg.sandbox_id to be set; never creates fresh.
executor = await CubeCodeExecutor.attach(cfg)

# 3. Attach-or-recreate: invokes `on_recreate` when the sandbox is gone,
#    then transparently provisions a new one. Useful for long-lived agents
#    whose external locator state must be cleared on recreate.
executor = await CubeCodeExecutor.create_or_recreate(
    cfg, on_recreate=lambda old_id: clear_locator(old_id),
)
```

`close()` is a no-op for the remote sandbox (it just drops the local
handle); `destroy()` explicitly kills the remote sandbox.

## Configuration Parameters

### UnsafeLocalCodeExecutor Parameters

```python
from trpc_agent_sdk.code_executors import UnsafeLocalCodeExecutor, CodeBlockDelimiter

code_executor = UnsafeLocalCodeExecutor(
    # Number of retries on code execution failure, default is 2
    error_retry_attempts=2,

    # Code block delimiters, used to identify code blocks in LLM responses
    # Default support: ```tool_code\n and ```python\n
    code_block_delimiters=[
        CodeBlockDelimiter(start="```python\n", end="\n```"),
        CodeBlockDelimiter(start="```tool_code\n", end="\n```"),
    ],

    # Working directory; uses a temporary directory if empty
    work_dir="",

    # Code execution timeout in seconds
    timeout=10,

    # Whether to clean up temporary files after execution, default is True
    clean_temp_files=True,
)
```

### ContainerCodeExecutor Parameters

```python
from trpc_agent_sdk.code_executors import ContainerCodeExecutor, CodeBlockDelimiter

code_executor = ContainerCodeExecutor(
    # Docker image name (required, mutually exclusive with docker_path)
    image="python:3-slim",

    # Dockerfile path (required, mutually exclusive with image)
    # docker_path="/path/to/Dockerfile",

    # base_url for remote Docker (optional)
    # base_url="tcp://remote-docker-host:2375",

    # Number of retries on code execution failure, default is 2
    error_retry_attempts=2,

    # Code block delimiters, default uses ```tool_code\n
    code_block_delimiters=[
        CodeBlockDelimiter(start="```tool_code\n", end="\n```"),
    ],
)
```

### CubeCodeExecutor Parameters

`CubeCodeExecutor` is configured via two dataclasses split by ISP:
`CubeCodeExecutorConfig` carries only sandbox-lifecycle / command-execution
settings, and `CubeWorkspaceRuntimeConfig` carries only workspace settings
(see the next section).

```python
from trpc_agent_sdk.code_executors.cube import (
    CubeCodeExecutor,
    CubeCodeExecutorConfig,
)

cfg = CubeCodeExecutorConfig(
    # Cube template id for new sandboxes; falls back to env CUBE_TEMPLATE_ID.
    template=None,

    # E2B-compatible Cube API URL; falls back to env E2B_API_URL.
    api_url=None,

    # E2B API key; falls back to env E2B_API_KEY.
    api_key=None,

    # Existing remote sandbox id. When set, factories attach instead of
    # creating a fresh sandbox.
    sandbox_id=None,

    # Default per-command timeout in seconds (float). Shared by the bare
    # executor and the workspace runtime. Default: 60.0.
    execute_timeout=60.0,

    # Sandbox idle lifetime in seconds (int >= 1); renewed on every
    # command. Default: 3600 (1 hour). The underlying e2b API takes
    # integer seconds — sub-second values are rejected at construction.
    idle_timeout=3600,
)

executor = await CubeCodeExecutor.create(cfg)
```

`CubeCodeExecutor` accepts the same `code_block_delimiters` as the other
executors; by default it adds a `bash` delimiter on top of the default
`python` and `tool_code` delimiters so plain `\`\`\`bash\n ... \n\`\`\``
fences are also picked up.

## CubeWorkspaceRuntime

For skill execution and other use cases that need a per-execution
workspace (input staging, structured program runs, output collection),
the Cube package additionally ships `CubeWorkspaceRuntime`. It composes
`CubeWorkspaceManager` (workspace directory lifecycle), `CubeWorkspaceFS`
(file/directory upload, download and glob-based collection), and
`CubeProgramRunner` (structured `cmd` + `args` execution) on top of the
same `CubeSandboxClient`.

```python
from trpc_agent_sdk.code_executors._types import (
    WorkspaceOutputSpec,
    WorkspacePutFileInfo,
    WorkspaceRunProgramSpec,
)
from trpc_agent_sdk.code_executors.cube import (
    CubeCodeExecutor,
    CubeCodeExecutorConfig,
    CubeWorkspaceRuntimeConfig,
    create_cube_workspace_runtime,
)

executor = await CubeCodeExecutor.create(CubeCodeExecutorConfig())

# `workspace_cfg` is optional. When omitted the runtime uses
# DEFAULT_REMOTE_WORKSPACE = "/workspace/cube_agent" as the root.
runtime = create_cube_workspace_runtime(
    executor,
    workspace_cfg=CubeWorkspaceRuntimeConfig(
        # Remote root under which the manager creates per-execution
        # `ws_<exec_id>_<suffix>` subtrees.
        remote_workspace="/workspace/cube_agent",
    ),
)

manager = runtime.manager()
fs = runtime.fs()
runner = runtime.runner()

ws = await manager.create_workspace("demo-1")           # /workspace/cube_agent/ws_demo-1_<ts>

await fs.put_files(ws, [
    WorkspacePutFileInfo(path="work/script.py",
                         content=b"print('script ran')\n"),
])

run_result = await runner.run_program(
    ws,
    WorkspaceRunProgramSpec(cmd="python3", args=["work/script.py"], timeout=15.0),
)
print(run_result.exit_code, run_result.stdout)

outputs = await fs.collect_outputs(
    ws, WorkspaceOutputSpec(globs=["work/*.py"], inline=True),
)
for ref in outputs.files:
    print(ref.name, len(ref.content))

await manager.cleanup("demo-1")
```

The runtime plugs straight into the Skill subsystem — pass it as
`workspace_runtime` when constructing a skill repository (see
[skill.md](skill.md) for details).

## Code Block Format

The Agent automatically identifies and executes code blocks in LLM responses. Supported code block formats:

### Default Format

````python
```python
print("Hello, World!")
```

```tool_code
result = 15 + 27 * 3
print(result)
```
````

### Execution Result Format

After code execution, the results are returned to the LLM in the following format:

````python
```tool_output
96
```
````

## Supported Languages

### UnsafeLocalCodeExecutor
- Python (`python`, `py`, `python3`)
- Bash (`bash`, `sh`)

### ContainerCodeExecutor
- Python (`python`, `py`, `python3`, empty string defaults to Python)
- Bash (`bash`, `sh`)

### CubeCodeExecutor
- Python (`python`, `py`, `python3`, empty string defaults to Python)
- Bash (`bash`, `sh`)

## Workflow

1. **User Query** → Agent receives the user query
2. **LLM Response Generation** → LLM generates a response containing code blocks
3. **Code Extraction** → The framework automatically extracts code blocks (based on `code_block_delimiters`)
4. **Code Execution** → CodeExecutor executes the code
5. **Result Return** → Execution results are returned to the LLM
6. **Final Response** → LLM generates the final response based on the execution results


## 123 Sandbox CodeExecutor Usage

Reference: Pcg123 Sandbox Usage Example (example to be added)

## FAQ

### 1. Docker Connection Failure

**Problem:** Docker connection failure is reported when using ContainerCodeExecutor

**Solution:**
- Linux: Ensure the Docker daemon is running: `sudo systemctl start docker`
- Windows/Mac: Start the Docker Desktop application
- Check Docker permissions: `sudo chmod 666 /var/run/docker.sock` or add the user to the docker group
- Verify Docker is running: `docker ps`
- If using remote Docker, check the `base_url` configuration

### 2. Code Execution Timeout

**Problem:** Code execution takes too long and times out

**Solution:**
```python
# Set timeout for UnsafeLocalCodeExecutor
code_executor = UnsafeLocalCodeExecutor(timeout=30)  # 30-second timeout
```

### 3. Code Execution Fails with No Error Message

**Problem:** Code execution fails but no error message is displayed

**Solution:**
- Check the `error_retry_attempts` setting and increase the retry count
- Review the log output; the framework logs detailed error information
- For ContainerCodeExecutor, check the container logs

### 4. CubeCodeExecutor Cannot Connect / Authenticates as Wrong Tenant

**Problem:** `CubeCodeExecutor.create` raises with messages like
`Cube sandbox requires \`api_url\` or E2B_API_URL env`, `... api_key ...`,
or `... template ... CUBE_TEMPLATE_ID ...`.

**Solution:**
- Install the optional extra: `pip install 'trpc-agent-py[cube]'`
- Export the three required env vars (or pass them on
  `CubeCodeExecutorConfig`): `E2B_API_URL`, `E2B_API_KEY`, `CUBE_TEMPLATE_ID`
- For multi-tenant deployments, prefer setting the cfg fields explicitly so
  each agent instance uses its own credentials instead of falling back to
  the process-wide environment

### 5. CubeCodeExecutor Sandbox Disappears Between Calls

**Problem:** A sandbox attached via `cfg.sandbox_id` raises
`SandboxNotFoundException` (gone) or `SandboxException` (PAUSED) on the
next command.

**Solution:**
- For long-lived agents, use `CubeCodeExecutor.create_or_recreate(cfg, on_recreate=...)`
  so the executor transparently provisions a new sandbox and notifies the
  caller to clear any external locator state
- Tune `idle_timeout` (default 3600s) upward if you legitimately need a
  longer idle window between commands; every command renews the lease
- Use `CubeWorkspaceManager.cleanup(exec_id)` instead of `executor.destroy()`
  if you only want to drop one workspace while keeping the sandbox alive

## Complete Example

See the complete example code: [examples/code_executors/agent/agent.py](../../../examples/code_executors/agent/agent.py)

End-to-end Cube example (executor + workspace runtime):
[examples/code_executors/cube_demo.py](../../../examples/code_executors/cube_demo.py)

## Security Recommendations

1. **Production Environment**: It is strongly recommended to use `ContainerCodeExecutor` for sandbox isolation
2. **Code Review**: Review LLM-generated code before deploying to production
3. **Resource Limits**: Set appropriate resource limits for ContainerCodeExecutor
4. **Access Control**: Restrict the code executor's file system access permissions
5. **Network Isolation**: Restrict container network access as needed

## Performance Considerations

- **UnsafeLocalCodeExecutor**: Fast execution speed, suitable for rapid iteration
- **ContainerCodeExecutor**: The initial startup requires pulling the image; subsequent executions are relatively fast
- **CubeCodeExecutor**: Adds network round-trips to a remote sandbox per command, but amortizes well for long-lived sessions because the sandbox is reused across calls (and across processes via `sandbox_id`); workspace file transfers use a tar-based protocol so directory uploads/downloads stay a single round-trip
- It is recommended to use ContainerCodeExecutor or CubeCodeExecutor in production environments and UnsafeLocalCodeExecutor in development environments
