# Benchmark Suite

`trpc_agent_bench` measures only the framework's own overhead: import, construction, a single-turn run, multi-turn sessions, Agent execution with tools, and memory usage. Model calls are served by a local OpenAI-compatible mock server, but Agent execution, tool dispatch, event streaming, and session reads/writes all run for real — no live LLM calls are required.

- [Design document](../../design/01-bench-design.md)
- [Framework benchmark data](../../../bench/README.md)

## Installation

To use the PyPI package:

```bash
pip install "trpc-agent-py[bench]"
```

For framework development, use an editable install (commented out by default):

```bash
# pip install -e ".[bench]"
```

## Quick start

The repository ships with `bench/llmagent`, so you can run it directly from the `bench/` directory:

```bash
cd bench

python -m trpc_agent_bench.run_bench \
  --targets llmagent.base \
  --benchmarks turn1,turn5,turn10,loop1,loop5,loop10 \
  --iterations 10 \
  --results-dir results/direct
```

If you only installed the PyPI package and don't have `bench/llmagent` from the repository, write your own target module as described in [Developing the agent under test](#developing-the-agent-under-test) below, expose a `TARGET` in it, and run it with `--targets your.module`.

Use `--concurrency` for stress testing. Each concurrency value starts its own child process:

```bash
python -m trpc_agent_bench.run_bench \
  --targets llmagent.base \
  --benchmarks turn1,turn5,turn10,loop1,loop5,loop10 \
  --concurrency 1,8,16,32 \
  --iterations 10 \
  --results-dir results/stress
```

You don't need `--baseline` with a single target. With multiple targets it is required and must be one of the modules in `--targets`:

```bash
python -m trpc_agent_bench.run_bench \
  --targets llmagent.base,my_bench.my_target \
  --baseline llmagent.base \
  --benchmarks turn5,loop5 \
  --iterations 10
```

Each run produces the following under the results root:

```text
results/direct/
├── llmagent.base/
│   ├── turn1.json
│   ├── turn5.json
│   ├── turn10.json
│   ├── loop1.json
│   ├── loop5.json
│   └── loop10.json
├── summary.json
├── report.md
└── report.html
```

Each JSON file contains `target`, `description`, `display_name`, `label`, `num_iterations`, `warmup_runs`, `result.run_times`, `result.memory_usages`, and `result.median_run_time` / `p95_run_time` / `median_memory_usage`. Reports are available as Markdown and self-contained HTML; result names include a `_cN` suffix when concurrency is used.

## Command-line options and built-in benchmarks

### Command-line options

| Argument | Values | Default | Description |
|---|---|---|---|
| `--targets` | Target module paths, comma-separated | Required | Target modules to benchmark, for example `llmagent.base` |
| `--baseline` | One of the module paths in `--targets` | Required with multiple targets | Comparison baseline |
| `--benchmarks` | Built-in short names or custom benchmark module paths, comma-separated | Built-in default set (12 items) | Run only the listed items |
| `--concurrency` | A positive integer or a comma-separated list such as `1,8,16,32` | `1` | Only applies to benchmarks whose `get_bench_profile().supports_concurrency` is `True` |
| `--iterations` | Positive integer | Required when the selected benchmarks need it | 10 is recommended; benchmarks such as `import_time` / `memory_footprint` that don't need it may omit it |
| `--results-dir` | Directory path | `results/` under the current directory | Result root directory |

For the full list of options, run:

```bash
python -m trpc_agent_bench.run_bench --help
```

### Built-in benchmarks

| Scenario | Metric | Description |
|---|---|---|
| `import_time` | Time to import the Agent package under test in a fresh process | Starts a new Python process to import the Agent package, then subtracts the time taken to start the interpreter alone |
| `instantiate_agent` | Construction time, peak memory | Calls only `target.instantiate()` to create an agent without tools; no conversation |
| `instantiate_agent_with_tools` | Construction time, peak memory | Creates an agent with 5 tools; no conversation |
| `turn1` / `turn5` / `turn10` | p50/p95, memory | 1 / 5 / 10 user turns, one LLM call per turn, text in 1 chunk |
| `loop1` / `loop5` / `loop10` | p50/p95, memory | 1 user turn with 1 / 5 / 10 LLM calls; every call except the last returns a function call, and the final text arrives in 5 chunks |
| `tools` | p50/p95, memory | Calls `add_numbers` first, then produces the final text after receiving the tool result |
| `tools_parallel` | p50/p95 | Returns 3 tool calls with different arguments in a single reply |
| `memory_footprint` | Memory footprint per resident agent | Creates agents without / with tools in bulk and keeps them in memory, measuring how much memory each resident agent occupies |

`turnN` measures the growth of multi-turn conversations within the same session; `loopN` measures the cost of multiple consecutive LLM calls inside one Agent execution (`N-1` tool rounds). Each `--iterations` sample is independent and creates a new session, so it is not the same thing as a multi-turn conversation.

- For `instantiate_agent*`, memory is the peak recorded by tracemalloc during construction.
- For `memory_footprint`, memory is the total memory of N agents still resident after creation divided by N — not the peak during construction.

Built-in short names map to `trpc_agent_bench/bench/_builtin/<name>.py`; custom benchmarks use a dotted module path, for example `my_bench.my_benchmark`.

## Integrating with the benchmark suite

From the entry point's perspective, the pieces relate as follows:

```text
run_bench (parent process)
  │ parses arguments, pre-checks benchmark profiles
  │ starts one child process per (target, benchmark, concurrency)
  ▼
benchmark.main() (child process)
  ├─ setup(script)
  │    ├─ load_target() -> AgentTarget
  │    ├─ start MockOpenAIServer
  │    └─ target.prepare(conf) -> prepared
  ├─ target.handle(prepared) -> AgentHandle (holds the Agent)
  ├─ PerformanceEval
  │    └─ calls handle.run_turn() according to script / iterations / concurrency
  └─ teardown()
       ├─ target.cleanup(prepared)
       └─ stop MockOpenAIServer
```

`run_bench` launches the benchmark scripts; a benchmark orchestrates the `handle.run_turn()` calls; `AgentTarget` creates the Agent and returns a Handle, and `AgentHandle` runs one conversation turn.

To integrate an agent under test, developers only need `AgentTarget`/`AgentHandle`; to define a custom benchmark scenario, they only need to write a benchmark.

### Developing the agent under test

Expose `TARGET` in any importable module. The example below has two parts: `MyTarget` handles integration, and `MyHandle` runs a single conversation turn.

```python
# my_bench/my_target.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import BaseSessionService, InMemorySessionService
from trpc_agent_sdk.types import Content, Part

from trpc_agent_bench.bench import (
    AgentBenchConf,
    AgentHandle,
    AgentTarget,
    ToolsFactory,
    resolve_tools,
)


@dataclass
class Prepared:
    model: OpenAIModel
    session_service: BaseSessionService


class MyHandle(AgentHandle):
    def __init__(self, *, agent: LlmAgent, session_service: BaseSessionService):
        self.agent = agent
        self.session_service = session_service

    async def run_turn(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        message: str,
    ) -> AsyncIterator[Any]:
        # Run one Agent turn.
        runner = Runner(
            app_name=app_name,
            agent=self.agent,
            session_service=self.session_service,
            enable_post_turn_processing=False,
            close_session_service_on_close=False,
        )
        try:
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=Content(parts=[Part.from_text(text=message)]),
            ):
                yield event
        finally:
            await runner.close()


class MyTarget(AgentTarget):
    name = "my_agent"
    import_module = "my_agent"  # used by import_time

    async def prepare(self, *, conf: AgentBenchConf) -> Prepared:
        # Called from setup(): create the model and session service.
        session_service = InMemorySessionService()
        try:
            model = OpenAIModel(
                model_name="bench-model",
                api_key="bench",
                base_url=conf.base_url,
            )
        except Exception:
            await session_service.close()
            raise

        return Prepared(model=model, session_service=session_service)

    async def instantiate(
        self,
        prepared: Prepared,
        *,
        tools_factory: ToolsFactory,
    ) -> LlmAgent:
        # Create the Agent; tools are resolved here.
        tools = await resolve_tools(tools_factory)
        return LlmAgent(
            name="my_bench_agent",
            model=prepared.model,
            instruction="You are a deterministic benchmark agent.",
            tools=tools,
        )

    async def handle(
        self,
        prepared: Prepared,
        *,
        tools_factory: ToolsFactory,
    ) -> AgentHandle:
        # Return the handle the benchmark will call.
        agent = await self.instantiate(prepared, tools_factory=tools_factory)
        return MyHandle(agent=agent, session_service=prepared.session_service)

    async def cleanup(self, prepared: Prepared) -> None:
        # Called from teardown(): release target resources.
        await prepared.session_service.close()


TARGET = MyTarget()
```

Benchmark the target agent with:

```bash
python -m trpc_agent_bench.run_bench \
  --targets my_bench.my_target \
  --benchmarks turn1 \
  --iterations 10
```

`run_bench` adds the current directory to the child process's `PYTHONPATH`, so the target module only needs to be in the current directory.

### Developing a benchmark

A benchmark is just a Python module that provides `main()`; returning 0 from `main()` means the benchmark succeeded. Built-in benchmarks live under `trpc_agent_bench/bench/_builtin/` and can be referenced by short name; custom benchmarks use a dotted module path.

#### 1. Minimal example

The following single-turn text example can be copied and run as-is.

```python
# my_bench/my_benchmark.py
from __future__ import annotations

import asyncio
import sys
import uuid

from trpc_agent_bench.bench import (
    PerformanceEval,
    TextBlock,
    drain,
    ensure_turn_completed,
    get_iterations,
    run_benchmarks,
    setup,
    teardown,
)

# Written to the result JSON and shown in the report.
DESCRIPTION = "A single text turn; metric: p50/p95 = latency of one turn."


async def main() -> int:
    # The script decides what the mock model returns.
    box = await setup([[TextBlock("ok")]])
    handle = await box.target.handle(box.prepared, tools_factory=lambda: [])

    async def run_once() -> None:
        events = await drain(
            handle.run_turn(
                app_name=box.conf.app_name,
                user_id=box.conf.user_id,
                session_id=str(uuid.uuid4()),
                message="Say ok.",
            ),
        )
        # Validate the final text; add expect_tool_success=True when tools are involved.
        ensure_turn_completed(events, expected_text="ok")

    try:
        # num_iterations comes from --iterations; one warmup run first.
        await run_benchmarks([
            PerformanceEval(
                name="my_benchmark",
                func=run_once,
                num_iterations=get_iterations(),
                warmup_runs=1,
            ),
        ])
    finally:
        # Clean up the target and the MockOpenAIServer.
        await teardown(box)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

Run it with:

```bash
python -m trpc_agent_bench.run_bench \
  --targets my_bench.my_target \
  --benchmarks my_bench.my_benchmark \
  --iterations 10
```

`run_bench` adds the current directory to the child process's `PYTHONPATH`, so the target and benchmark modules only need to be in the current directory.

#### 2. Simulating multi-turn conversations

```python
# bench/your_bench.py
...
from trpc_agent_sdk.tools import BaseTool, FunctionTool
from trpc_agent_bench.bench import FunctionBlock, TextBlock

# One Turn is one user turn; one Reply is one LLM response.
# FunctionBlock triggers tool execution; TextBlock ends the turn.
SCRIPT = [
    [[FunctionBlock("add_numbers", {"a": 1, "b": 2})], TextBlock("ok")],
    [[FunctionBlock("add_numbers", {"a": 3, "b": 4})], TextBlock("ok")],
]


def add_numbers(a: int, b: int) -> int:
    """Add two integers and return the result."""
    return a + b


def make_tools() -> list[BaseTool]:
    return [FunctionTool(add_numbers)]


async def main() -> int:
    box = await setup(SCRIPT)
    handle = await box.target.handle(box.prepared, tools_factory=make_tools)

    async def run_once() -> None:
        session_id = str(uuid.uuid4())
        # Call run_turn once per Turn, sharing the same session.
        for _ in SCRIPT:
            events = await drain(
                handle.run_turn(
                    app_name=box.conf.app_name,
                    user_id=box.conf.user_id,
                    session_id=session_id,
                    message="Say ok.",
                ),
            )
            ensure_turn_completed(
                events,
                expected_text="ok",
                expect_tool_success=True,
            )

    ...
```

`tools_factory` may also be an async function; `TextBlock(chunks=N)` controls the number of stream chunks, and the text must be long enough.

#### 3. Simulating LLM latency

```python
# bench/your_bench.py
...
from trpc_agent_bench.bench import AgentBenchConf, ModelConf, TextBlock


async def main() -> int:
    conf = AgentBenchConf(
        model_conf=ModelConf(
            request_delay_ms=50,
            chunk_delay_ms=10,
        ),
    )
    box = await setup([[TextBlock("hello", chunks=3)]], conf=conf)
    ...
```

- `request_delay_ms`: delay between the start of the request and the first response; applies to both streaming and non-streaming responses.
- `chunk_delay_ms`: delay between chunks starting from the 2nd chunk of a streaming response; has no effect when `chunks=1`.

`MockOpenAIServer` is not public API; `setup()` starts it internally.

#### 4. Concurrency benchmarks

```python
# bench/your_bench.py
...
from trpc_agent_bench.bench import BenchProfile, get_concurrency


def get_bench_profile() -> BenchProfile:
    return BenchProfile(supports_concurrency=True)


async def main() -> int:
    ...
    current = get_concurrency()
    name = f"my_benchmark_c{current}" if current > 1 else "my_benchmark"
    label = f"concurrency={current}" if current > 1 else ""

    async def run_session(index: int) -> None:
        user_id = f"{box.conf.user_id}_{index}"
        session_id = str(uuid.uuid4())
        for _ in SCRIPT:
            events = await drain(
                handle.run_turn(
                    app_name=box.conf.app_name,
                    user_id=user_id,
                    session_id=session_id,
                    message="Say ok.",
                ),
            )
            ensure_turn_completed(events, expected_text="ok", expect_tool_success=True)

    async def run_once() -> None:
        await asyncio.gather(*(run_session(i) for i in range(current)))

    await run_benchmarks([
        PerformanceEval(
            name=name,
            display_name="my_benchmark",
            label=label,
            func=run_once,
            num_iterations=get_iterations(),
            warmup_runs=1,
        ),
    ])
    ...
```

With `supports_concurrency=True`, `run_bench` starts a separate child process for each `--concurrency` value. Result names get a `_cN` suffix, and each session uses its own `user_id`.

#### 5. Disabling a benchmark

```python
# bench/your_bench.py
import os

from trpc_agent_bench.bench import BenchProfile


def get_bench_profile() -> BenchProfile:
    if not os.environ.get("MY_BACKEND_URL"):
        return BenchProfile(
            usable=False,
            skip_reason="MY_BACKEND_URL is not set",
        )
    return BenchProfile()


async def main() -> int:
    ...
```

When `usable=False`, `run_bench` prints `[skip] <name> (<skip_reason>)` and skips it without affecting other benchmarks.
