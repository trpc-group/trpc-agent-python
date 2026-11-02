# trpc-agent-python 压测套件设计

> **结论摘要**：压测套件按 Agno `cookbook/performance/` 的思路设计，只测框架自身开销：模型调用用 Mock 替代，agent 的真实 loop 照常执行。套件是一个 `trpc_agent_bench` 通用包，顶层只放入口 `run_bench.py`；包内 `bench/` 下是公开 API 和实现（`_eval.py`、`_model.py`、`_target.py`、`_report.py`），内置 benchmark 放在 `bench/_builtin/`，子进程入口是 `bench/_runner.py`；仓库的 `bench/llmagent/` 放内置 target 适配，用户自定义的 target/benchmark 可以是任意可 import 的 module。

## 1. 背景与目标

需要量化 agent 编排层在 import、构造、单轮 run、流式、工具循环、会话持久化、内存占用上的框架开销，并支持多个 target 的横向对比。

直接用真实 LLM 做对比不可行：

- 模型时延和排队时间远大于编排层开销，差异会被噪声淹没；
- 同一 prompt 的返回不稳定，模型调用次数、工具调用次数都会变化；
- 需要真实凭证和网络，CI 里不可复现。

Agno 的 performance 套件给了我们一个成熟模板：**在框架自己的模型调用处注入 mock，跑真实 loop，只测框架开销**。本方案照这个模板设计，更多请参考[第 4 章](#4-参考agno-的-performance-套件)。

### 1.1 目标

- **测框架开销**：模型 Mock 固定返回，真实执行 import、构造、消息拼装、tool dispatch、事件流、session 读写。
- **可复现**：无网络、无凭证；同一脚本每次的调用次数、工具执行次数、最终文本一致。
- **可对比**：不同 target 用同一套 benchmark、同一种 mock，输出 median/p95 和倍数。
- **可回归**：同一套脚本可重复跑，`--iterations 10` 做调测；报告可直接看。
- **可扩展**：新增 benchmark 或 target 只加一个文件。

### 1.2 非目标

- 不评估回答质量、token 计费准确性；
- 不压测真实模型服务（TTFT/TPOT、GPU、KV cache）；
- 不做 pass@k、flakiness 这类质量指标——我们的 workload 是确定性的；

## 2. 方案设计

### 2.1 设计原则与总体架构

**设计原则**

1. **只测框架开销**：模型、网络、真实 LLM 全部用 Mock 替代；报告里明确每个数字不包含 provider 服务时间。
2. **驱动真实 loop**：不重实现 agent，只在模型调用处注入 mock；消息拼装、tool dispatch、事件流、session 读写都走真实路径。
3. **统一 mock 路径**：所有 target 都走本机 HTTP mock，数字包含相同的 HTTP client、SSE 解析和 wire 转换开销。
4. **确定性 + 正确性断言**：`TextBlock` / `FunctionBlock` 脚本按请求里的 message 位置无状态取响应；每次 run 校验最终文本、工具执行、错误事件；失败直接抛异常。
5. **进程隔离**：每个 benchmark 文件单独起一个新进程；模型 server 独立进程，不计入被测进程的 CPU/RSS。
6. **时间/内存分开测**：`tracemalloc` 会拖慢执行，timed pass 不追踪内存。
7. **统计用 median/p95**：GC 长尾会让 mean 失真；保留原始样本。
8. **模块依赖规则**：包内 `bench/_eval.py`、`bench/_model.py`、`bench/_target.py`、`bench/_setup.py`、`bench/_builtin/`、`bench/_report.py`、`run_bench.py` 只使用 `trpc_agent_sdk` 的公共 tool/log 类型，不 import agent/runner 实现；只有仓库的 `bench/llmagent/` 知道具体 target 的构造和运行方式。

**总体架构**

```text
┌───────────────── trpc_agent_bench/run_bench.py ─────────────────────────────┐
│                                                                           │
│  target module paths              report.py                    results/   │
│  ┌────────────────┐               ┌─────────────────┐          ┌────────┐ │
│  │ target agent A │               │ JSON + Markdown │          │ *.json │ │
│  │ target agent B │               │ HTML + 倍数表   │          └────────┘ │
│  └───────┬────────┘               └────────▲────────┘                     │
│           │ spawn per (target, benchmark)  │                              │
│           ▼                                │                              │
│  ┌──────────────────────────────┐          │                              │
│  │ fresh subprocess             │          │                              │
│  │  benchmark file              │          │                              │
│  │   ├─ target adapter          │          │                              │
│  │   ├─ model mock             │          │                              │
│  │   │    MockOpenAI server    │          │                              │
│  │   ├─ PerformanceEval         │          │                              │
│  │   │    warmup → runtime      │          │                              │
│  │   │    → memory              │          │                              │
│  │   └─ result JSON ────────────┼──────────┘                              │
│  └──────────────────────────────┘                                         │
│                                                                           │
│  通用部分：_eval.py + _model.py + _target.py + _builtin/ + _report.py      │
│  内置适配：仓库的 bench/llmagent/；自定义 target/benchmark：任意可 import   │
│            的 module                                                       │
└───────────────────────────────────────────────────────────────────────────┘
```

一次完整运行的流程：

```text
1. run_bench.py 读取 --targets <target module...>、--benchmarks、--concurrency
2. benchmark 只描述 turn script；session service 由 target 创建和清理
3. 对每个选中的 benchmark 调 get_bench_profile()；usable=False 时打印 [skip] 并跳过
4. 如果选中的 benchmark 有 requires_iterations=True，命令行必须提供 --iterations
5. 对 supports_concurrency=True 的 benchmark，按 --concurrency 的每个值展开
6. 对每个 (target module, benchmark module, concurrency)：
   a. spawn 一个 fresh Python 进程
   b. 进程通过 BENCH_TARGET 找到 target module，读取 module.TARGET
   c. setup() 启动 MockOpenAIServer，把 base_url 写进 AgentBenchConf
   d. target.prepare() 创建 session service / runtime / run_dir
   e. PerformanceEval 跑 warmup → runtime → memory
   f. teardown() 关 target 资源和 mock server
   g. 写 results/<target module>/<benchmark>[_cN].json
7. 收集 summary.json（machine info + 所有结果）
8. report.py 生成 target 对比表、Markdown/HTML
9. sanity 失败或 benchmark 崩溃 → run_bench.py 退出非 0
```

**包结构与调用链**

```text
trpc-agent-python/
├── trpc_agent_bench/          # 通用层：不 import agent/runner 实现
│   ├── __init__.py            # 只导出 run_bench
│   ├── run_bench.py           # 唯一入口：子进程编排
│   ├── bench/                 # 公开 API + 性能压测实现
│   │   ├── __init__.py        # 公开导出
│   │   ├── _config.py         # AgentBenchConf / ModelConf
│   │   ├── _eval.py           # PerformanceEval、统计、断言、run_benchmarks
│   │   ├── _model.py          # TextBlock/FunctionBlock、内部 MockOpenAIServer
│   │   ├── _profile.py        # BenchProfile
│   │   ├── _setup.py          # setup / teardown / BenchSetup
│   │   ├── _target.py         # AgentTarget / AgentHandle、load_target()
│   │   ├── _report.py         # JSON / Markdown / HTML
│   │   ├── _runner.py         # 单 benchmark 子进程入口
│   │   └── _builtin/          # 内置 benchmark
│   │       └── _utils/        # session helper、命名 helper
│   └── eval/                  # 效果评测占位，当前只有 __init__.py
├── bench/
│   ├── README.md              # 目标、命令、结果与结论
│   ├── llmagent/              # 内置 target 模块
│   │   ├── __init__.py
│   │   └── base.py            # LlmAgentTarget
│   └── results/               # gitignored
└── tests/
    └── trpc_agent_bench/
        ├── test_run_bench.py
        └── bench/
```

包内 `bench/_builtin/` 一文件一项，`bench/_runner.py` 按 module 路径加载并调用 `main()`；仓库的 `bench/llmagent/` 放内置 target module。`bench/` 下的实现模块统一用 `_` 前缀，公开名字只从 `bench/__init__.py` 导出。

```text
bench/_builtin/<benchmark>.py
    │  load_target("<target module>")   # 只知道 AgentTarget / AgentHandle
    ▼
<target module>.py                       # 提供 module.TARGET
    │  LlmAgent + Runner
    ▼
AgentHandle.run_turn(...) ──► events
```

benchmark 文件只通过 `AgentHandle.run_turn()` 发起对话，不直接依赖某个 Agent 实现；target module 把具体 Agent 接成这个接口，并管理 session service 的生命周期。

模块依赖规则用一条测试守住：

| 路径 | 允许 import | 说明 |
|---|---|---|
| `bench/_eval.py` / `bench/_target.py` / `bench/_report.py` / `run_bench.py` | stdlib + `trpc_agent_sdk` 公共 tool 类型 | 不依赖 agent/runner 实现 |
| `bench/_model.py` | 同上 + fastapi/uvicorn | 本地 mock server |
| `bench/_setup.py` | 同上 | setup/teardown 与 mock server 生命周期 |
| `bench/_builtin/` | 以上模块 + stdlib | 不依赖 agent/runner 实现 |
| 仓库 `bench/llmagent/` | `trpc_agent_sdk` 的 agents/models/runners/sessions | 只有这一层会构造和运行具体 Agent |

扫描整个 `trpc_agent_bench/` 的 import，不应该出现具体 agent/runner 的实现 import；agent 实现只放在 `bench/llmagent/`。这样通用层不会意外耦合到具体 target 的实现。

### 2.2 模型 Mock 与 Target 抽象

**模型 Mock**

所有 target 统一走内部 `MockOpenAIServer`：本地起一个 OpenAI 兼容 server，target 通过自己的 OpenAI client 访问。这样 v1/v2 以及后续接入的 agent 都走同一条 HTTP、SSE 和 wire 转换路径，数字可比；同时不访问外网，也不包含真实模型推理。

`MockOpenAIServer` 不是公开 API，由 `setup()` 创建和启动，固定监听 `127.0.0.1`，端口随机。启动后把地址写进 `AgentBenchConf.base_url`，target 从 `conf.base_url` 读取。

模型返回内容用 `TextBlock` / `FunctionBlock` 脚本：

```python
@dataclass(frozen=True)
class TextBlock:
    text: str = "ok"
    chunks: int = 1

@dataclass(frozen=True)
class FunctionBlock:
    name: str
    args: dict[str, Any]
```

```python
Reply = TextBlock | list[FunctionBlock]
Turn = list[Reply]
```

脚本结构是 `list[Turn]`，每个 `Turn` 是 `list[Reply]`。一个 `Reply` 要么是一个 `TextBlock`，要么是一组 `FunctionBlock`。

- `TextBlock` 表示一次文本响应，一个 reply 只有一个 `TextBlock`；`chunks` 控制拆成几个 stream chunk
- `FunctionBlock` 表示模型发起一次 tool call；一个 reply 里的多个 `FunctionBlock` 表示并行调用
- `FunctionResponseBlock` 不存在；tool response 由 agent 真正执行工具后产生

`setup(turns, conf)` 把脚本序列化给 mock server。server 根据请求里的 user/assistant message 数量计算：

```text
turn = user message 数量
reply = 当前 turn 里 assistant message 数量
```

然后返回 `turns[turn - 1][reply]`。这个推导是无状态的，并发 session 各自按自己的 messages 取响应，不共享游标。

`TextBlock` 转成文本 chunk；`FunctionBlock` 列表转成 OpenAI 的 `tool_calls`。`MockOpenAIServer` 的职责：

```text
POST /v1/chat/completions    stream=true/false；SSE；tool_calls deltas；usage
GET  /v1/models              client 探测模型/context length 时返回
GET  /health                 就绪检查
```

实现要点：

- 用 FastAPI + uvicorn（仓库已有依赖）；每个 benchmark 在测量前启动，测量结束后停止；
- 独立子进程运行，端口随机；benchmark 进程只通过 `base_url` 连接；
- `ModelConf.request_delay_ms` / `chunk_delay_ms` 控制 mock 响应节奏；
- 脚本由 `setup(turns)` 提供，server 负责序列化成 OpenAI 的 wire format。

模型延迟通过 `AgentBenchConf.model_conf` 配置：

```python
from trpc_agent_bench.bench import AgentBenchConf, ModelConf, TextBlock, setup

conf = AgentBenchConf(
    model_conf=ModelConf(request_delay_ms=50, chunk_delay_ms=10),
)

box = await setup(
    [[TextBlock("hello", chunks=3)]],
    conf=conf,
)
```

`request_delay_ms` 在流式和非流式 chat 请求上都生效；`chunk_delay_ms` 只在多个 stream chunk 之间生效。

**Target 协议与 adapter 骨架**

```python
# trpc_agent_bench/bench/_target.py
class AgentTarget(ABC):
    name: str
    import_module: str               # target 的 import 路径

    @abstractmethod
    async def prepare(self, *, conf: AgentBenchConf) -> Any: ...
    @abstractmethod
    async def instantiate(self, prepared: Any, *, tools_factory: ToolsFactory) -> Any: ...
    @abstractmethod
    async def handle(self, prepared: Any, *, tools_factory: ToolsFactory) -> AgentHandle: ...
    @abstractmethod
    async def cleanup(self, prepared: Any) -> None: ...

class AgentHandle(ABC):
    @abstractmethod
    async def run_turn(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        message: str,
    ) -> AsyncIterator[Event]: ...
```

`AgentTarget` 和 `AgentHandle` 都是 ABC，具体实现必须显式继承并实现各自的抽象方法。

`prepare()` 从 `conf.base_url` 读取 mock server 地址，创建 session service、agent runtime 和 target 自己的临时目录；`instantiate()` 才构造 agent，这样 `instantiate_agent.py` 的计时只包含 agent 构造。

`AgentBenchConf` 的字段：

```python
@dataclass
class AgentBenchConf:
    app_name: str = "bench_app"
    user_id: str = "bench_user"
    agent_conf: Any = None
    model_conf: ModelConf = field(default_factory=ModelConf)
    base_url: str | None = field(default=None, repr=False)
```

`agent_conf` 是 target 自己的配置，类型和字段由 target 定义，压测套件不解析。当前 `LlmAgent` target 没有额外配置；后续 target 可以把 instruction、model 参数、目录等放进自己的 conf 类型，通过 `conf.agent_conf` 传入。

**session service 归 target**

target 在 `prepare()` 里创建 session service，在 `cleanup()` 里关闭。benchmark 不创建 session service，也不管理它的生命周期。

当前只有 `LlmAgent` target，使用 `InMemorySessionService`：

```python
# bench/llmagent/base.py
class LlmAgentTarget(AgentTarget):
    name = "llm_agent"
    import_module = "trpc_agent_sdk"

    def create_session_service(self) -> BaseSessionService:
        return InMemorySessionService()
```

在 `bench/` 目录下通过 `--targets llmagent.base` 选择该 target；benchmark 仍然是 `turn1` / `turn5` / `turn10` 和 `loop1` / `loop5` / `loop10`。

**setup / teardown**

```text
setup(turns, conf)
  ├─ MockOpenAIServer(turns, conf.model_conf).start()
  ├─ conf.base_url = base_url
  ├─ await target.prepare(conf=conf)
  └─ BenchSetup(target, prepared, conf, _server)

teardown(box)
  ├─ await target.cleanup(prepared)
  └─ box._server.stop()
```

`target.cleanup()` 负责关闭 session service、target runtime 和 target 的 `run_dir`。Runner 在每次 turn 结束时关闭。

### 2.3 压测项与断言

**压测项清单**

核心 benchmark：

| 文件 | 操作 | 脚本 | 断言 | 主要指标 |
|---|---|---|---|---|
| `import_time.py` | 新进程 import 被测 Agent 包 | — | import 成功 | 减掉只启动解释器时间后的 median/p95 |
| `instantiate_agent.py` | 构造不带工具的 agent，不发起对话 | — | 返回对象不为空 | 每次构造耗时 + 内存峰值 |
| `instantiate_agent_with_tools.py` | 构造带 5 个工具的 agent，不发起对话 | — | 返回对象不为空 | 每次构造耗时 + 内存峰值 |
| `turn1.py` | 1 个 user turn，1 次 LLM 调用，文本 1 chunk；target 决定 session service；`--concurrency` 控制并发数 | `TEXT` | 每次采样正常完成 | 会话耗时、内存峰值 |
| `turn5.py` | 5 个 user turn，每个 turn 1 次 LLM 调用，文本 1 chunk；target 决定 session service；支持并发 | `TEXT ×5` | 每次采样正常完成 | 会话耗时、内存峰值 |
| `turn10.py` | 10 个 user turn，每个 turn 1 次 LLM 调用，文本 1 chunk；target 决定 session service；支持并发 | `TEXT ×10` | 每次采样正常完成 | 会话耗时、内存峰值 |
| `loop1.py` | 1 个 user turn，1 次 LLM 调用，最终文本 5 chunks，无 tool round | `TextBlock(chunks=5)` | 正常完成 | 会话耗时、内存峰值 |
| `loop5.py` | 1 个 user turn，5 次 LLM 调用，4 次 tool round，最终文本 5 chunks | `FC ×4, TEXT(chunks=5)` | 每次采样正常完成、tool 成功 | 会话耗时、内存峰值 |
| `loop10.py` | 1 个 user turn，10 次 LLM 调用，9 次 tool round，最终文本 5 chunks | `FC ×9, TEXT(chunks=5)` | 每次采样正常完成、tool 成功 | 会话耗时、内存峰值 |
| `tools.py` | 一轮 Agent 执行（1 次 tool round） | `FunctionBlock -> TextBlock` | 工具执行成功且无 error、最终文本 == "ok" | median/p95、内存峰值 |
| `memory_footprint.py` | 批量创建不带工具 / 带工具的 agent 后一直留在内存里 | — | 构造出的 agent 数量正确 | 每个常驻 agent 占用的内存 |

`turnN` 按 user turn 数命名：`turn1` / `turn5` / `turn10`，每个 user turn 只调一次 LLM，文本固定 1 chunk，用来测 session 历史增长和多轮调用开销。`loopN` 按一轮 Agent 执行内的 LLM 调用数命名：`loop1` / `loop5` / `loop10`，除最后一次外每次都返回 function call，所以有 `N-1` 次 tool round，最终文本固定 5 chunks，用来测一轮 Agent 执行内 tool round 次数增加时的开销。默认集包含 `turn1` / `turn5` / `turn10`、`loop1` / `loop5` / `loop10` 以及 import/构造/tools/memory 等 12 项。session service 由 target 创建和清理，不由 benchmark 管理。`--concurrency` 只对 `get_bench_profile().supports_concurrency` 为 `True` 的 benchmark 生效；并发大于 1 时结果名追加 `_cN`，HTML 中显示 `concurrency=N`。每个并发 session 使用独立的 `user_id`（`<base>_0`、`<base>_1` 等），避免共享 session 状态。

扩展 benchmark：

| 文件 | 操作 | 想验证 |
|---|---|---|
| `tools_parallel.py` | 一个响应里 M 个 tool call | 并行工具调度 |

扩展项不再单独分组，内置 benchmark 按固定顺序展示；自定义 benchmark 排在最后，按名称自然排序。

**benchmark 钩子**

每个 benchmark module 可以选择提供一个 `get_bench_profile()`：

```python
from trpc_agent_bench.bench import BenchProfile


def get_bench_profile() -> BenchProfile:
    """Describe availability, concurrency, and iteration requirements."""
```

没写时按默认 profile 处理：

```python
BenchProfile(
    usable=True,
    supports_concurrency=False,
    requires_iterations=True,
    skip_reason="",
)
```

`run_bench` 在启动子进程前调用 `get_bench_profile()`。`usable=False` 时打印 `[skip] <name> (<skip_reason>)` 并跳过该 benchmark，继续跑其他项；`supports_concurrency=True` 时，`--concurrency 1,8` 会为每个值启动独立子进程，并发大于 1 的结果名追加 `_cN`；为 `False` 时只跑一次。`requires_iterations=True` 时，选中的 benchmark 会要求命令行提供 `--iterations`；`import_time` / `memory_footprint` 这类不需要 `--iterations` 的 benchmark 返回 `requires_iterations=False`，因此只选它们时可以省略 `--iterations`。普通 benchmark（`tools` 等）不需要写这个方法。

**一个 benchmark 文件长什么样**

```python
# my_benches/my_benchmark.py
import uuid

from trpc_agent_bench.bench import (
    PerformanceEval,
    TextBlock,
    ensure_turn_completed,
    get_iterations,
    run_benchmarks,
    setup,
    teardown,
)


TURN_SCRIPT = [[TextBlock()]]


async def main() -> int:
    box = await setup(TURN_SCRIPT)
    handle = await box.target.handle(box.prepared, tools_factory=lambda: [])

    async def run_once() -> None:
        events = [
            event async for event in handle.run_turn(
                app_name=box.conf.app_name,
                user_id=box.conf.user_id,
                session_id=str(uuid.uuid4()),
                message="Say ok.",
            )
        ]
        ensure_turn_completed(events, expected_text="ok")

    try:
        await run_benchmarks(
            [
                PerformanceEval(
                    name="my_benchmark",
                    func=run_once,
                    num_iterations=get_iterations(),
                    warmup_runs=1,
                ),
            ],
        )
    finally:
        await teardown(box)
    return 0
```

`run_benchmarks` 在调用方的事件循环里跑异步 eval；runner 也支持 `async def main()`。

**benchmark 说明（DESCRIPTION）**

每个 benchmark module 顶层定义一个 `DESCRIPTION` 字符串，写清楚两件事：

1. 这个 benchmark 在压什么场景；
2. 报告里的 p50/p95（以及 memory 行）分别代表什么。

```python
# turn5.py
DESCRIPTION = (
    "5 个 user turn，每个 turn 1 次 LLM 调用，每个 turn 的文本固定 1 chunk。"
    "指标：p50/p95 = 整个会话的耗时；memory = 本次采样中 tracemalloc 记录的峰值。"
)
```

数据流：

```text
benchmark module.DESCRIPTION
  → runner.load_benchmark() 写入 BENCH_DESCRIPTION
  → write_result() 写入 result JSON 的 description 字段
  → report 的 Benchmark 说明 bullet list 展示
```

`PerformanceEval` 也支持逐项 `description=` 覆盖，给同一 module 里多个结果用。自定义 benchmark 没有 `DESCRIPTION` 时，report 显示 `-`，不影响运行。

**正确性断言**

```python
def ensure_turn_completed(events, *, expected_text=None, expect_tool_success=False):
    if any(e.is_error() for e in events):
        raise RuntimeError("turn returned an error event")
    if not any(e.turn_complete for e in events):
        raise RuntimeError("turn did not complete")
    final = "".join(
        part.text
        for e in events
        if not e.partial and e.content is not None
        for part in e.content.parts
        if part.text
    )
    if expected_text is not None and final != expected_text:
        raise RuntimeError(f"unexpected final text: {final!r}")
    if expect_tool_success:
        calls = [
            part.function_call
            for e in events
            if e.content is not None
            for part in e.content.parts
            if part.function_call
        ]
        results = [
            part.function_response
            for e in events
            if e.content is not None
            for part in e.content.parts
            if part.function_response
        ]
        if not calls or not results:
            raise RuntimeError("tool loop did not execute")
        if any(isinstance(r.response, dict) and r.response.get("error") for r in results):
            raise RuntimeError("tool call failed")
```

断言成本是纳秒级，相对几百微秒到毫秒的 run 可以忽略；但能防止“错误路径被当成正常结果”。

### 2.4 测量、统计与报告

**测量口径**

`PerformanceEval` 的执行顺序：

```text
PerformanceEval.run()
  ├─ warmup_runs = 10           直接 func()，不计入统计
  ├─ runtime pass               N 次，Timer 只包住 func()
  ├─ memory baseline            空函数跑 3 次 tracemalloc，取平均
  ├─ memory pass                N 次，tracemalloc peak - baseline
  └─ PerformanceResult
       run_times / memory_usages
       avg / min / max / std / median / p95
```

要点：

- **时间/内存分开跑**：`tracemalloc` 会拖慢执行，timed iteration 不追踪内存；
- **warmup 丢弃**：首次 import、首次建连、首次初始化不算；
- **median/p95 优先**：GC 长尾会让 mean 失真；
- **原始样本保留**：JSON 里存 `run_times` / `memory_usages`，报告可以重算。

import time：

```text
for sample in range(SAMPLES):
    t = subprocess.run([python, "-c", "import <target>"]).wall_time
baseline = median(subprocess.run([python, "-c", "pass"]).wall_time)
adjusted = [max(0, t - baseline) for t in samples]
```

每次都是 fresh 进程；`-X importtime` 的 top 模块另存，方便定位是哪一段依赖变重了。

memory footprint：

```text
per_agent = (tracemalloc_after - tracemalloc_before) / batch_size
```

批量创建 N 个 agent 后一直留在内存里，不释放；测的是每个常驻 agent 占用的内存，不是构造过程中的瞬时峰值。构造峰值由 `instantiate_agent.py` 的 memory pass 覆盖。

统计口径：

| 指标 | 定义 |
|---|---|
| `median_run_time` | 排序后的中位数 |
| `p95_run_time` | inclusive quantile，95th |
| `avg/min/max/std` | 辅助诊断 |
| `median_memory_usage` | 每次 run 的 peak 中位数 |
| `run_times` / `memory_usages` | 原始样本，供报告重算 |

**进程隔离**

`run_bench.py` 对每个 `(target, benchmark)` 起一个 fresh Python 进程：

```text
run_bench.py --targets <target-a>,<target-b>
  ├─ target-a × import_time.py          → fresh process
  ├─ target-a × instantiate_agent.py    → fresh process
  ├─ target-a × turn1.py               → fresh process
  ├─ target-a × turn5.py               → fresh process
  ├─ target-a × turn10.py              → fresh process
  ├─ target-a × loop1.py               → fresh process
  ├─ target-a × loop5.py               → fresh process
  ├─ target-a × loop10.py              → fresh process
  ├─ target-b × import_time.py          → fresh process
  ├─ ...
  └─ summary.json
```

原因：

- 不继承前一个 benchmark 的 import 缓存 / 内存分配器状态；
- 不同 target 的模块级状态、线程池、全局 registry 互不污染；
- 每个进程的 RSS/CPU 可以单独统计。

模型 server 独立于 benchmark 进程：server 是另一个子进程，agent 进程只计自己的 CPU/RSS；server 的请求时长单独记录。

**结果 JSON**

```json
{
  "name": "turn5",
  "description": "5 个 user turn，每个 turn 1 次 LLM 调用，每个 turn 的文本固定 1 chunk；指标：p50/p95 = 整个会话的耗时",
  "display_name": "turn5",
  "label": "",
  "target": "<target>",
  "num_iterations": 10,
  "warmup_runs": 1,
  "result": {
    "run_times": [0.000081, 0.000084],
    "memory_usages": [0.000012, 0.000011],
    "median_run_time": 0.000081,
    "p95_run_time": 0.000084,
    "median_memory_usage": 0.000012
  }
}
```

**target 对比表**

多 target 时必须指定 `--baseline`，baseline 可以是 `--targets` 里的任意一个。每个非 baseline target 单独一张对比表，`turn1` / `turn5` / `turn10` 和 `loop1` / `loop5` / `loop10` 分别放在相邻位置：

```text
# trpc-agent-python bench report
# results: results/

## Benchmark 说明

- `import_time`：每次新开一个 Python 进程 import 被测 Agent 包，再减去只启动解释器的时间。指标：p50/p95 = 新进程 import 耗时。
- `instantiate_agent`：只调用 `target.instantiate(..., tools_factory=...)` 构造一个不带工具的 agent，不发起对话。指标：p50/p95 = 单次构造耗时。
- `turn1`：1 个 user turn，1 次 LLM 调用，文本固定 1 chunk。指标：p50/p95 = 整个会话的耗时。
- `turn5`：5 个 user turn，每个 turn 1 次 LLM 调用，每个 turn 的文本固定 1 chunk。指标：p50/p95 = 整个会话的耗时。
- `turn10`：10 个 user turn，每个 turn 1 次 LLM 调用，每个 turn 的文本固定 1 chunk。指标：p50/p95 = 整个会话的耗时。
- `loop5`：1 个 user turn，5 次 LLM 调用，4 次 tool round，最终文本 5 chunks。指标：p50/p95 = 整个 turn 耗时。
- `loop10`：1 个 user turn，10 次 LLM 调用，9 次 tool round，最终文本 5 chunks。指标：p50/p95 = 整个 turn 耗时。
- `memory_per_agent`：批量创建 agent 后一直留在内存里，不释放。指标：memory = 每个常驻 agent 占用的内存。

## target A vs target B

| benchmark        | baseline p50 | target B p50 | baseline p95 | target B p95 | baseline memory | target B memory |
|------------------|--------------|--------------|--------------|--------------|-----------------|-----------------|
| import_time      | 147 ms       | 120 ms -18.4% | 155 ms       | 128 ms -17.4% | -               | -               |
| instantiate_agent| 4.7 us       | 3.1 us -34.0% | 5.2 us       | 3.5 us -32.7% | 2.0 KiB         | 1.7 KiB -15.0%  |
| turn1            | 65 us        | 41 us -36.9%  | 72 us        | 46 us -36.1%  | 1.2 KiB         | 1.0 KiB -16.7%  |
| turn5            | 2.9 ms       | 1.1 ms -62.1% | 3.0 ms       | 1.2 ms -60.0% | 1.3 MiB         | 2.1 MiB +61.5%  |
| turn10           | 5.8 ms       | 2.2 ms -62.1% | 6.0 ms       | 2.4 ms -60.0% | 2.3 MiB         | 3.1 MiB +34.8%  |
| loop1            | 65 us        | 41 us -36.9%  | 72 us        | 46 us -36.1%  | 1.2 KiB         | 1.0 KiB -16.7%  |
| loop5            | 2.9 ms       | 1.1 ms -62.1% | 3.0 ms       | 1.2 ms -60.0% | 1.3 MiB         | 2.1 MiB +61.5%  |
| loop10           | 5.8 ms       | 2.2 ms -62.1% | 6.0 ms       | 2.4 ms -60.0% | 2.3 MiB         | 3.1 MiB +34.8%  |

# 数字仅为格式示例，不是实测结果。
```

口径：

```text
Δ p50 = (target p50 - baseline p50) / baseline p50 * 100%
Δ p95 = (target p95 - baseline p95) / baseline p95 * 100%
Δ memory = (target median_memory_usage - baseline median_memory_usage)
           / baseline median_memory_usage * 100%
```

当前指标都是时间或内存，负数表示更快/更省，正数表示更慢/更多；百分比直接跟在 target 的数字后面，HTML 里负数绿色、正数红色。HTML 里 benchmark 列和 target 的 p50/p95/memory 列加粗，baseline 列保持正常字重，方便扫读。`memory_per_agent*` 只测内存，p50/p95 显示 `-`，memory 列填值；`import_time` 不测内存，memory 列显示 `-`。只有一个 target 时不需要 baseline，报告退化成 `benchmark | p50 | p95 | memory` 的绝对值表。

报告同时输出：

- `report.md`：Benchmark 说明 bullet list + 分节对比表 + 口径；
- `report.html`：自包含，按 target 分节，带百分比颜色；带并发的结果在 benchmark 名下面显示 `concurrency=N`；
- `summary.json`：机器可读，CI artifact，带 `targets` / `baseline` 和每条 result 的 `description`、`display_name`、`label`。

**运行方式**

```bash
cd bench
python -m trpc_agent_bench.run_bench \
  --targets llmagent.base \
  --benchmarks turn5,loop5 \
  --concurrency 1,8 \
  --iterations 10
```

命令在 `bench/` 目录下执行，当前目录已在 `sys.path` 里。`--targets` 填 target module 路径；`--baseline` 在多 target 时必填，必须是 `--targets` 里的一个；`--benchmarks` 内置项可以用短名，自定义项填 module 路径。用户自定义的 module 只要在 `PYTHONPATH` 上即可。

调测用 `--iterations 10`。只有选中的 benchmark 有 `requires_iterations=True` 时才必须传；只跑 `import_time` / `memory_footprint` 时可以省略。结果统一写到 `results/`。完整跑在空闲机器上手动/定时执行。

内部通过环境变量把配置传给每个 benchmark 子进程：

| 变量 | 作用 |
|---|---|
| `BENCH_TARGET` | target module 路径 |
| `BENCH_BENCHMARK` | benchmark module 路径 |
| `BENCH_CONCURRENCY` | 当前子进程的并发数；由 `--concurrency` 展开 |
| `BENCH_RESULT_SUFFIX` | 并发大于 1 时的结果名后缀，如 `_c8` |
| `BENCH_ITERATIONS` | 需要 iterations 的 benchmark 的迭代数；由 `--iterations` 写入 |
| `BENCH_RESULTS_DIR` | 结果输出目录 |
| `BENCH_DESCRIPTION` | runner 从 benchmark module 的 `DESCRIPTION` 写入，供 `write_result` 记录 |
| `BENCH_QUIET` | 不打印每项 summary |
| `GIT_COMMIT` | 写入结果 JSON 的 machine 信息 |
| `PYTHONPATH` | 仓库根目录 + 执行命令时的当前目录 |

## 3. 压测目标 Agent

前面的方案设计是通用的：mock、Target 协议、benchmark 和报告都不绑定具体实现。这一章把它落到 `trpc-agent-python` 的 `LlmAgent` 上。内置 target 放在 `bench/llmagent/`。

同一套 benchmark 会跑 import、构造、单轮 run、turn 多轮、一轮 Agent 执行内的多次 LLM 调用和 memory。对每个 target，adapter 只需提供三个入口：

```python
await prepare(conf=...)                    # 从 conf.base_url 连接 mock，创建 target 资源
instantiate(prepared, tools_factory=...)   # 构造 agent；工具创建算在这里
handle(prepared, tools_factory=...).run_turn(...)  # 跑一轮；benchmark 自己 drain 事件
```

`run_turn` 使用 `LlmAgent + Runner`：每次 turn 先确保 session 存在，再新建 `Runner`，共用 target 自己创建的 session service。`app_name` / `user_id` / `session_id` 由 benchmark 调用 `run_turn` 时显式传入，handle 不保存固定身份。`cleanup()` 负责关闭 session service；Runner 在每次 turn 结束时关闭。

```python
# bench/llmagent/base.py
async def prepare(self, *, conf: AgentBenchConf) -> PreparedLlmAgent:
    session_service = self.create_session_service()   # InMemorySessionService
    model = OpenAIModel(
        model_name="bench-model",
        api_key="bench",
        base_url=conf.base_url,                       # setup() 写入的 mock 地址
    )
    return PreparedLlmAgent(model=model, session_service=session_service)

async def instantiate(prepared, *, tools_factory):
    tools = await resolve_tools(tools_factory)
    return LlmAgent(
        name="bench_llm_agent",
        model=prepared.model,
        instruction=INSTRUCTION,
        tools=tools,
        parallel_tool_calls=True,
    )

async def run_turn(self, *, app_name, user_id, session_id, message):
    await ensure_session(...)
    runner = Runner(
        app_name=app_name,
        agent=self.agent,
        session_service=self.session_service,
        enable_post_turn_processing=False,            # 不触发总结/记忆的额外模型调用
        close_session_service_on_close=False,         # session service 由 target 管理
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
```

`LlmAgent` 的 `turnN` 脚本只返回 `TextBlock`，不带 `FunctionBlock`；`loop1` / `loop5` / `loop10`、`tools` 和 `tools_parallel` 的脚本才返回 `FunctionBlock`。mock 通过 `conf.base_url` 注入，agent 的真实 loop、tool dispatch、事件流和 session 读写都会执行。

## 4. 参考：Agno 的 performance 套件

Agno 在 `cookbook/performance/` 下有一套完整的性能基准，核心思路写在 `cookbook/performance/README.md`：

> measures framework overhead: the time and memory an agent framework itself adds to importing, constructing, and running an agent, isolated from any model provider. All benchmarks replace the model with an in-process mock at the framework's own model boundary.

### 4.1 它测哪些东西

| benchmark 文件 | 操作 | 度量 |
|---|---|---|
| `import_time.py` | fresh 进程 import `agno` / `agno.agent` | 减掉解释器启动的 median/p95 |
| `instantiate_agent.py` | 构造 `Agent` | 每次构造的耗时 + 内存峰值 |
| `instantiate_agent_with_tools.py` | 构造带 5 个工具的 Agent | 同上 |
| `instantiate_team.py` / `instantiate_workflow.py` | 构造 Team / Workflow | 同上 |
| `run_agent.py` | 一次 `run()` / `arun()` | 每次 run 的框架开销 |
| `run_agent_streaming.py` | 一次流式 run，drain 整个事件流 | 流式机制固定开销 |
| `run_agent_with_tools.py` | 两轮 tool loop | tool dispatch + 第二轮模型调用 |
| `run_agent_with_storage.py` | 带 in-memory DB 和 history 的 run | session 持久化开销 |
| `memory_footprint.py` | 批量创建 agent 后一直留在内存里 | 每个常驻 agent 占用的内存 |

### 4.2 它的 mock 与测量口径

`cookbook/performance/_bench.py` 里两个类：

```python
class MockModel(Model):
    """Minimal offline model: returns a canned text response without any network call."""

    def __init__(self, response_content: str = "ok"):
        super().__init__(id="mock-model", name="mock-model", provider="mock")
        self._mock_response = ModelResponse(
            content=response_content, role="assistant", response_usage=MessageMetrics()
        )

    def invoke(self, *args, **kwargs) -> ModelResponse:
        return self._mock_response

    async def ainvoke(self, *args, **kwargs) -> ModelResponse:
        return self._mock_response

    def invoke_stream(self, *args, **kwargs) -> Iterator[ModelResponse]:
        yield self._mock_response
```

```python
class MockToolModel(MockModel):
    """Offline model that requests one tool call, then answers once the tool result is present."""

    def _make_response(self, messages) -> ModelResponse:
        has_tool_result = any(
            getattr(m, "role", None) == "tool" for m in (messages or [])
        )
        if has_tool_result:
            return ModelResponse(content="done", role="assistant", ...)
        return ModelResponse(
            role="assistant",
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "add_numbers", "arguments": '{"a": 1, "b": 2}'},
            }],
            ...
        )
```

两个关键点：

- mock 继承框架自己的 `Model` 基类，所以真实 loop 照常执行，只有 provider 调用被换掉；
- tool 决策是**无状态**的：看请求的 messages 里有没有 `role="tool"`，不看“第几次调用”，所以并发/重放都稳定。

`libs/agno/agno/eval/performance.py` 的 `PerformanceEval`：

```text
PerformanceEval.run()
  ├─ warmup_runs = 10      直接 func()，不计入统计
  ├─ runtime pass          N 次，Timer 只包住 func()，记录 run_times
  ├─ memory pass           N 次，tracemalloc.start() → func() → get_traced_memory()
  │                        和 runtime 分开跑，因为 tracemalloc 会拖慢执行
  └─ PerformanceResult
       avg / min / max / std / median / p95
       p95 = statistics.quantiles(..., method="inclusive")[94]
```

套件运行方式：

```text
run_all.py
  ├─ subprocess: import_time.py          → import_time.json
  ├─ subprocess: instantiate_agent.py    → instantiate_agent.json
  ├─ subprocess: run_agent.py            → run_agent.json
  ├─ ...
  └─ summary.json（machine info + 所有 benchmark 结果）
report.py → 自包含 HTML 报告（图表 + 统计 + 跨框架倍数）
baselines/*.json → committed baseline
TEST_LOG.md → 每次跑的环境、结果、异常
```

每个 benchmark 文件都在新进程里运行，避免继承前一个 benchmark 的 import 缓存、内存分配器状态和 GC 状态。调测用 `--iterations 10`，结果写到 `results/`；不传时只有不需要 `--iterations` 的 benchmark 可以运行，不会冒充 baseline。

### 4.3 我们采用什么、改什么

`comparison/run_all.py` 把同一操作跑在 Agno、LangGraph、PydanticAI、CrewAI 上，每个框架在**自己的模型调用处**注入 mock：

| 框架 | mock 位置 |
|---|---|
| Agno | `Model` 子类 |
| LangGraph | `GenericFakeChatModel` |
| PydanticAI | `TestModel` |
| CrewAI | `BaseLLM` 子类 |

`comparison/README.md` 明确写了 fairness notes 和 exclusions（例如 CrewAI 的 tool 协议是版本内部格式，mock 会变成“测 mock”）。报告里的数字是各框架的 **floor**，不是完整 provider 路径成本。

| Agno 的做法 | 我们采用 | 我们的调整 |
|---|---|---|
| in-process mock at the model call site | ✅ | 统一改成在本机 HTTP 层注入 mock，v1/v2 都走同一条 client/stream 路径 |
| 按指标分文件（import/instantiate/run/streaming/tools/storage/memory） | ✅ | 文件名和分组直接对齐 |
| `PerformanceEval`：warmup + median/p95 + 时间/内存分开 | ✅ | 复刻同样的统计口径 |
| 每文件 fresh process | ✅ | `run_bench.py` 按 `(target, benchmark)` 起子进程 |
| 每次 run 断言正确性 | ✅ | `ensure_turn_completed` |
| HTML report + 调测 | ✅ | 报告同时输出 Markdown 和 HTML；调测用 `--iterations 10` |
| 跨框架对比 | ✅ | 对比对象是同一套操作下的不同 target |
| pass@k / 质量指标 | ❌ | 我们的 workload 确定性，不需要 |
