# 压测套件

`trpc_agent_bench` 只测框架自身开销：import、构造、单轮 run、多轮会话、带工具的 Agent 执行和内存占用。模型调用由本机 OpenAI-compatible mock server 替代，但 Agent 执行、tool dispatch、事件流和 session 读写都会真实执行，不需要真实 LLM 调用。

- [设计文档](../../design/01-bench-design.md)
- [框架压测数据](../../../bench/README.md)

## 安装

直接使用 PyPI 包：

```bash
pip install "trpc-agent-py[bench]"
```

框架自身开发调测时使用 editable 安装（默认注释掉）：

```bash
# pip install -e ".[bench]"
```

## 快速开始

仓库里自带 `bench/llmagent`，在 `bench/` 目录下可以直接跑：

```bash
cd bench

python -m trpc_agent_bench.run_bench \
  --targets llmagent.base \
  --benchmarks turn1,turn5,turn10,loop1,loop5,loop10 \
  --iterations 10 \
  --results-dir results/direct
```

如果只安装了 PyPI 包、没有仓库里的 `bench/llmagent`，可以按后面的「开发压测 target」写一个自己的 target module，在里面提供 `TARGET`，再用 `--targets your.module` 运行。

压力测试用 `--concurrency`。每个并发值会启动独立子进程：

```bash
python -m trpc_agent_bench.run_bench \
  --targets llmagent.base \
  --benchmarks turn1,turn5,turn10,loop1,loop5,loop10 \
  --concurrency 1,8,16,32 \
  --iterations 10 \
  --results-dir results/stress
```

只跑一个 target 时不需要 `--baseline`；多个 target 时必须指定，而且必须是 `--targets` 里的一个：

```bash
python -m trpc_agent_bench.run_bench \
  --targets llmagent.base,my_bench.my_target \
  --baseline llmagent.base \
  --benchmarks turn5,loop5 \
  --iterations 10
```

每次运行会在结果根目录生成：

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

单个 JSON 里有 `target`、`description`、`display_name`、`label`、`num_iterations`、`warmup_runs`、`result.run_times`、`result.memory_usages`，以及 `result.median_run_time` / `p95_run_time` / `median_memory_usage`。报告支持 Markdown 和自包含 HTML，带并发的结果名带 `_cN` 后缀。

## 命令行参数与内置benchmark

### 命令行参数

| 参数 | 可填的值 | 默认 | 说明 |
|---|---|---|---|
| `--targets` | target module 路径，逗号分隔 | 必填 | 要压测的 target module 列表，例如 `llmagent.base` |
| `--baseline` | `--targets` 里的一个 module 路径 | 多 target 时必填 | 对比基准 |
| `--benchmarks` | 内置短名或自定义 benchmark module 路径，逗号分隔 | 内置默认集（12 项） | 只跑指定的几项 |
| `--concurrency` | 正整数或逗号分隔的多个值，如 `1,8,16,32` | `1` | 只对 `get_bench_profile().supports_concurrency` 为 `True` 的 benchmark 生效 |
| `--iterations` | 正整数 | 选中的 benchmark 需要时必填 | 推荐 10；`import_time` / `memory_footprint` 这类不需要 `--iterations` 的 benchmark 可以省略 |
| `--results-dir` | 目录路径 | 当前目录下的 `results/` | 结果根目录 |

完整参数可以直接看：

```bash
python -m trpc_agent_bench.run_bench --help
```

### 内置 benchmark

| 压测场景 | 指标 | 说明 |
|---|---|---|
| `import_time` | 新进程 import 被测 Agent 包的耗时 | 每次新开一个 Python 进程 import 被测 Agent 包，再减去只启动解释器的时间 |
| `instantiate_agent` | 构造耗时、内存峰值 | 只调用 `target.instantiate()` 创建一个不带工具的 agent，不发起对话 |
| `instantiate_agent_with_tools` | 构造耗时、内存峰值 | 创建一个带 5 个工具的 agent，不发起对话 |
| `turn1` / `turn5` / `turn10` | p50/p95、内存 | 1 / 5 / 10 个 user turn，每个 turn 1 次 LLM，文本 1 chunk |
| `loop1` / `loop5` / `loop10` | p50/p95、内存 | 1 个 user turn，1 / 5 / 10 次 LLM；除最后一次外都返回 function call，最终文本 5 chunks |
| `tools` | p50/p95、内存 | 先调 `add_numbers`，拿到 tool result 后再出最终文本 |
| `tools_parallel` | p50/p95 | 一次回复里返回 3 个不同参数的 tool call |
| `memory_footprint` | 每个常驻 agent 的内存占用 | 批量创建不带工具 / 带工具的 agent 后一直留在内存里，测每个 agent 常驻占用多少内存 |

`turnN` 测同一个 session 内多轮会话的增长；`loopN` 测一轮 Agent 执行内连续多次 LLM 调用（`N-1` 次 tool round）的开销。`--iterations` 是独立采样，每次都会新建 session，不等于多轮对话。

- `instantiate_agent*` 的 memory 是构造过程中 tracemalloc 记录的峰值。
- `memory_footprint` 的 memory 是创建 N 个 agent 后一直留在内存里，占用的内存除以 N；不是构造过程中的峰值。

内置短名对应 `trpc_agent_bench/bench/_builtin/<name>.py`；自定义 benchmark 用带点的 module 路径，例如 `my_bench.my_benchmark`。

## 接入压测套件

从压测入口来看，有如下关系图：

```text
run_bench（父进程）
  │ 解析参数、预检 benchmark profile
  │ 每个 (target, benchmark, concurrency) 起一个子进程
  ▼
benchmark.main()（子进程）
  ├─ setup(script)
  │    ├─ load_target() -> AgentTarget
  │    ├─ 启动 MockOpenAIServer
  │    └─ target.prepare(conf) -> prepared
  ├─ target.handle(prepared) -> AgentHandle（内部持有 Agent）
  ├─ PerformanceEval
  │    └─ 按 script / iterations / concurrency 调 handle.run_turn()
  └─ teardown()
       ├─ target.cleanup(prepared)
       └─ 停止 MockOpenAIServer
```

`run_bench` 负责拉起压测脚本；`benchmark` 负责编排 `handle.run_turn()` 的调用；`AgentTarget` 负责创建 Agent 和返回 Handle，`AgentHandle` 负责跑一轮对话。

开发者只需AgentTarget/AgentHandle即可接入被测 Agent，只需开发benchmark即可自定义压测场景。

### 开发被测 Agent

在任意可 import 的 module 里提供 `TARGET`。下面的例子包含两部分：`MyTarget` 负责接入，`MyHandle` 负责跑一轮对话。

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
        # 执行一轮Agent交互
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
    import_module = "my_agent"  # import_time 用

    async def prepare(self, *, conf: AgentBenchConf) -> Prepared:
        # setup() 时调用：创建 model 和 session service。
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
        # 创建 Agent；tool 在这里解析。
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
        # 返回给 benchmark 调用的 handle。
        agent = await self.instantiate(prepared, tools_factory=tools_factory)
        return MyHandle(agent=agent, session_service=prepared.session_service)

    async def cleanup(self, prepared: Prepared) -> None:
        # teardown() 时调用：释放 target 资源。
        await prepared.session_service.close()


TARGET = MyTarget()
```

通过如下命令来压测目标 Agent：

```bash
python -m trpc_agent_bench.run_bench \
  --targets my_bench.my_target \
  --benchmarks turn1 \
  --iterations 10
```

`run_bench` 会把当前目录加入子进程的 `PYTHONPATH`，所以 target module 在当前目录下即可。

### 开发 benchmark

一个 benchmark 就是一个 Python module，里面提供 `main()`；`main()` 返回 0 表示这项压测成功。内置 benchmark 在 `trpc_agent_bench/bench/_builtin/` 下，可以用短名引用；自定义 benchmark 用带点的 module 路径。

#### 1. 最小示例

下面是一个单轮文本示例，复制后可以直接运行。

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

# 会写进结果 JSON，并显示在报告里。
DESCRIPTION = "单个文本 turn；指标：p50/p95 = 一次 turn 的耗时。"


async def main() -> int:
    # script 决定 mock 模型返回什么。
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
        # 校验最终文本；带 tool 时加 expect_tool_success=True。
        ensure_turn_completed(events, expected_text="ok")

    try:
        # num_iterations 来自 --iterations；先跑一次 warmup。
        await run_benchmarks([
            PerformanceEval(
                name="my_benchmark",
                func=run_once,
                num_iterations=get_iterations(),
                warmup_runs=1,
            ),
        ])
    finally:
        # 清理 target 和 MockOpenAIServer。
        await teardown(box)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

写完后用这条命令运行：

```bash
python -m trpc_agent_bench.run_bench \
  --targets my_bench.my_target \
  --benchmarks my_bench.my_benchmark \
  --iterations 10
```

`run_bench` 会把当前目录加入子进程的 `PYTHONPATH`，所以 target 和 benchmark module 放在当前目录下即可。

#### 2. 模拟多轮对话

```python
# bench/your_bench.py
...
from trpc_agent_sdk.tools import BaseTool, FunctionTool
from trpc_agent_bench.bench import FunctionBlock, TextBlock

# 一个 Turn 是一轮 user turn；一个 Reply 是一次 LLM 响应。
# FunctionBlock 触发 tool 执行；TextBlock 结束这一轮。
SCRIPT = [
    [[FunctionBlock("add_numbers", {"a": 1, "b": 2})], TextBlock("ok")],
    [[FunctionBlock("add_numbers", {"a": 3, "b": 4})], TextBlock("ok")],
]


def add_numbers(a: int, b: int) -> int:
    """把两个整数相加并返回结果。"""
    return a + b


def make_tools() -> list[BaseTool]:
    return [FunctionTool(add_numbers)]


async def main() -> int:
    box = await setup(SCRIPT)
    handle = await box.target.handle(box.prepared, tools_factory=make_tools)

    async def run_once() -> None:
        session_id = str(uuid.uuid4())
        # 每个 Turn 调一次 run_turn，共用同一个 session。
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

`tools_factory` 也可以是 async 函数；`TextBlock(chunks=N)` 控制 stream chunk 数，文本要够长。

#### 3. 模拟LLM延迟

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

- `request_delay_ms`：请求开始到第一个响应之前的等待，流式和非流式都生效。
- `chunk_delay_ms`：流式响应中第 2 个 chunk 起，每个 chunk 之间等待；`chunks=1` 时不会生效。

`MockOpenAIServer` 不是公开 API；`setup()` 会在内部启动它。

#### 4. 并发压测

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

`supports_concurrency=True` 后，`run_bench` 会为每个 `--concurrency` 值启动独立子进程。结果名带 `_cN` 后缀，每个 session 使用独立的 `user_id`。

#### 5. 禁用 benchmark

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

`usable=False` 时，`run_bench` 打印 `[skip] <name> (<skip_reason>)` 并跳过，不影响其他 benchmark。
