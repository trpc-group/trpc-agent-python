# DSHAgent 设计方案

## 1. 背景

DeepSeek Harness（dsh）是 DeepSeek 开源的 Agent Harness（Agent 运行时），核心设计是 **everything-is-a-plugin**（基于 Cordis）：模型、工具、skill、sandbox、session 持久化、CLI/Web UI 都可以作为插件存在。官方提供了一批基础插件，社区也在围绕 `dsh-plugin` 持续贡献；业务可以按自己的场景写私有插件，再通过 profile + patch 组合进 runtime。

```text
DeepSeek Harness 插件生态
├─ 官方插件
│  ├─ model provider / tool / skill
│  ├─ sandbox / session persistence
│  ├─ terminal / subagent / workflow
│  └─ CLI / Web UI
├─ 社区插件
│  ├─ search / browser / database
│  └─ integration / community tools
└─ 业务插件
   ├─ 私有工具 / 数据接入
   └─ 权限 / 审计 / 流程
        │
        ▼
   profile + patch 组合
        │
        ▼
   dsh runtime
```

这带来两个直接好处：

- 业务不用从零实现 Agent loop、工具调用、沙箱、会话存储这些底座，按需选插件即可。
- 同一个 runtime 可以通过不同 profile/patch 组合出不同形态，官方、社区、业务插件可以混用。框架插件和用户插件也可以分层组合，不需要把业务插件合并进框架。

但 dsh 本身是 Node.js runtime/CLI，通常以本地进程或 Web UI 的方式运行。业务要把它部署成 tRPC 服务，还需要自己解决进程生命周期、会话映射、多用户 / 多 session 隔离、事件流适配、可观测性和部署这些问题。这些恰好是 tRPC-Agent 已经提供的服务能力。与其让每个业务把 dsh 包一遍，不如在框架里提供一个统一的 DSHAgent。

所谓生态扩展 Agent，是把外部 Agent runtime 接进 tRPC-Agent 的 Agent 接口，让它像普通 Agent 一样参与编排、会话和服务化，而不是再造一套 Agent 框架。

```text
┌────────────────────────────────────────────┐
│  tRPC-Agent 业务代码 / tRPC 服务           │
└──────────────────────┬─────────────────────┘
                       │
                       ▼
┌────────────────────────────────────────────┐
│  DSHAgent（生态扩展 Agent）                │
└──────────────────────┬─────────────────────┘
                       │ Python SDK / stdio JSON-RPC
                       ▼
┌────────────────────────────────────────────┐
│  dsh runtime（Node.js 进程）               │
└──────────────────────┬─────────────────────┘
                       │
                       ▼
                       官方 / 社区 / 业务插件
```

DSHAgent 的定位是：**不替代 dsh，也不把 dsh 插件重写成 Python。** 它把 dsh runtime 作为 Agent 的执行后端接进来，让 dsh 的插件生态和业务定制插件可以直接用在 tRPC 服务里。具体来说，DSHAgent 通过 dsh 官方 Python SDK 以 stdio JSON-RPC 控制 dsh 子进程，业务侧不用直接管 Node 进程。对业务来说，使用方式仍然是一个 tRPC-Agent Agent；对 dsh 来说，插件、profile、session 这些机制保持不变。

```text
直接部署 dsh
  业务代码 ──► dsh CLI / Web ──► 插件
  进程、会话、协议、多租户、部署都要自己接

通过 DSHAgent
  业务代码 ──► tRPC-Agent 服务 ──► DSHAgent ──► dsh runtime ──► 插件
  复用 tRPC 服务能力，保留 DSH 插件生态
```

适合的场景：

- 已经有 dsh Agent 或插件，想部署成 tRPC 服务，复用 tRPC-Agent 的模型接入、会话、观测和部署能力。
- 已经在用 tRPC-Agent，想直接使用 dsh 的 Agent loop、工具、skill、sandbox 等能力，不想再维护一套 Node 服务。
- 新业务想同时使用 tRPC-Agent 和 dsh 的生态，按需组合。

DSH 目前还是 developer preview，插件和协议仍在快速迭代。DSHAgent 把这层变化收在适配层里，业务侧尽量只依赖 tRPC-Agent 的 Agent 接口。

## 2. 架构设计

### 2.1 整体架构

```text
┌──────────────────────────────────────────────────────────────┐
│ tRPC-Agent Runner                                            │
│                                                              │
│ run_async / tRPC Event                                       │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────┐
│ DSHAgent                                                     │
│                                                              │
│ DSHRuntime (Python) lifecycle / request + notification       │
│ to_trpc_event()          notification → tRPC Event           │
└───────────────┬──────────────────────────────┬───────────────┘
                │                              │
                │ session/trpc-prompt          │ read/write
                │   sessionId + contentBlocks  │ dsh jsonl
                │   + jsonl + version          │
                │   + model + metadata         ▼
                │                          ┌──────────────────────────────┐
                │                          │ SessionService               │
                │                          │                              │
                │                          │ state: dsh jsonl             │
                │                          │ event: tRPC Event            │
                │                          └──────────────────────────────┘
                │
                │ stdio JSON-RPC（request + notification）
                ▼
┌──────────────────────────────────────────────────────────────┐
│ DSHProcess                                                   │
│                                                              │
│ trpc-jsonrpc-server                                          │
│   ├── session/trpc-prompt   载入 jsonl，执行一轮             │
│   ├── session/trpc-cancel   中断当前轮                       │
│   └── 通知：assistant.delta / event / status                 │
│                                                              │
│ trpc-session-backend                                         │
│   ├── 从请求 jsonl 构建 session                              │
│   ├── append 更新内存                                        │
│   └── flush 导出 jsonl                                       │
│                                                              │
│ DSH AgentLoop / tools / skills                               │
└──────────────────────────────────────────────────────────────┘
                ▲
                │ trpc plugins patch（file:// 插件）
                │
┌───────────────┴──────────────────────────────────────────────┐
│ DSH Plugins                                                  │
│                                                              │
│ trpc plugins (js)              user plugins (js)             │
│ ├── trpc-jsonrpc-server        ├── business tools            │
│ ├── trpc-session-backend       ├── policy                    │
│ └── trpc-request-context       └── skills                    │
└──────────────────────────────────────────────────────────────┘
```

一次请求：

```text
 Runner              DSHAgent           SessionService               DSHProcess
   │                     │                     │                         │
   │                     │ run_async(request)  │                         │
   ├────────────────────►│                     │                         │
   │                     │                     │                         │
   │                     │ get_state(sessionId)│                         │
   │                     ├────────────────────►│                         │
   │                     │◄── {jsonl,version} ─┤                         │
   │                     │                     │                         │
   │                     │ session/trpc-prompt │                         │
   │                     │ { sessionId,        │                         │
   │                     │   contentBlocks,    │                         │
   │                     │   jsonl, version,   │                         │
   │                     │   model, metadata } │                         │
   │                     ├─────────────────────┼────────────────────────►│
   │                     │                     │                         ├── backend 载入 jsonl
   │                     │                     │                         ├── resume / 复用 agent
   │                     │                     │                         ├── agent.model.select(model)
   │                     │                     │                         ├── agent.followup(input)
   │                     │◄── {messageId} ─────┼─────────────────────────┤ receipt
   │                     │◄── session.assistant.delta ───────────────────┤  流式 chunk
   │◄── tRPC Event ──────┤                     │                         │
   │                     │◄── session.event(tool/call) ──────────────────┤
   │◄── tRPC Event ──────┤                     │                         │
   │                     │◄── session.event(tool/result) ────────────────┤
   │◄── tRPC Event ──────┤                     │                         │
   │                     │◄── session.event(turn/end) ───────────────────┤
   │                     │◄── session.status(idle) ──────────────────────┤
   │                     │                     │                         │
   │                     │ append events       │                         │
   │                     │   → jsonl           │                         │
   │                     │ write_state_delta   │                         │
   │                     │ { jsonl, version }  │                         │
   │                     ├────────────────────►│                         │
   │                     │                     │                         │
   │◄── final response ──┤                     │                         │
   │                     │                     │                         │
```

### 2.2 用户用法

**安装。**

```sh
pip install "trpc-agent-py[dsh]"
```

`dsh` extra 会带上 DSH Python SDK 和 runtime 依赖；安装后确认 `dsh` 命令可用：

```sh
dsh --version
```

**创建 profile。**

用 `dsh` 命令生成一个 profile 目录，后续所有 DSH 插件都放在这里：

```sh
export DSH_HOME="./dsh-home"

dsh --profile my-agent \
  --from-default-profile sdk \
  --dump-default-config >/dev/null
```

生成：

```text
$DSH_HOME/profiles/my-agent/
├── package.json
├── cordis.yml
├── cordis.patch.yml
└── ...
```

`--from-default-profile` 是破坏性重置：目标 profile 已存在时会被重建，第一次创建时用即可。

**创建 DSHAgent。**

把 profile 目录传给 `DSHAgent`：

```python
from trpc_agent_sdk.agents import DSHAgent
from trpc_agent_sdk.configs import DSHConfig
from trpc_agent_sdk.models import OpenAIModel

agent = DSHAgent(
    name="my_agent",
    dsh_config=DSHConfig(
        profile_dir="dsh-home/profiles/my-agent",
        env={"OPENAI_API_KEY": "..."},
    ),
    model=OpenAIModel(model_name="deepseek-chat"),
)
```

`profile_dir` 支持绝对路径和相对路径；相对路径按 **Python 进程的 cwd** 解析，默认 `DSHConfig()` 使用相对路径 `"profiles/sdk"`。`dsh_home`、`cwd` 同样支持相对路径；不填 `dsh_home` 时会从 `profile_dir` 推导，不填 `cwd` 时用 Python 进程 cwd。`DSHRuntime` 从 profile 路径推导 profile 名，启动 `dsh --profile <name>`。

**模型怎么选。**

`model` 支持三种写法：

| 写法 | 行为 |
|---|---|
| `model=OpenAIModel(...)` | DSHAgent 把它转成 `DSHModelSpec`，随每轮 `session/trpc-prompt` 发给 dsh，覆盖 profile 默认模型 |
| `model=model_factory` | 每次 run 用 `RunConfig.custom_data` 调回调，动态决定模型 |
| `model=None` | 不覆盖，使用 DSH profile / cordis 里配置的默认模型 |

如果模型已经在 profile 的 `cordis.yml` 或 `cordis.patch.yml` 里配好了，就把 `model` 设为 `None`：

```python
agent = DSHAgent(
    name="my_agent",
    dsh_config=DSHConfig(
        profile_dir="dsh-home/profiles/my-agent",
    ),
    model=None,
)
```

回调形式：

```python
async def model_factory(custom_data):
    return OpenAIModel(
        model_name=custom_data.get("model", "deepseek-chat"),
    )

agent = DSHAgent(
    name="my_agent",
    dsh_config=DSHConfig(
        profile_dir="dsh-home/profiles/my-agent",
        env={"OPENAI_API_KEY": "..."},
    ),
    model=model_factory,
)
```

> `OpenAIModel` 的转换只带 `provider` / `model` 选择；`api_key` / `base_url` 不通过 stdio JSON-RPC 传输，DSH 侧的 provider 认证仍由 profile/cordis 或环境变量提供。模型名和 provider 要能在 DSH profile 里解析；`OpenAIModel` 默认映射到 `openai` provider，profile 里要挂对应的 provider 插件和凭据。

**在 profile 里开发插件。**

用户可以直接在 profile 目录里加自己的 DSH 插件：

```text
$DSH_HOME/profiles/my-agent/
├── cordis.patch.yml
└── plugins/
    └── tenant-tool.js
```

```yaml
# cordis.patch.yml
- insert:
    - id: tenant-tool
      name: './plugins/tenant-tool.js'
```

插件可以通过 `trpcRequestContext` 拿到 trpc-agent 传入的 `custom_data`：

```js
// 在 tool 的 execute(args, exec) 里
const sessionId = String(exec.agent?.session.id ?? '')
const metadata = ctx.get('trpcRequestContext')?.get(sessionId)?.metadata
```

完整的插件写法和 policy 例子见 `### trpc-request-context`。

**启动、运行和退出。**

```python
import asyncio

from trpc_agent_sdk.agents import DSHAgent
from trpc_agent_sdk.configs import DSHConfig
from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


async def main():
    agent = DSHAgent(
        name="my_agent",
        dsh_config=DSHConfig(
            profile_dir="dsh-home/profiles/my-agent",
            env={"OPENAI_API_KEY": "..."},
        ),
        model=OpenAIModel(model_name="deepseek-chat"),
    )

    # 进程启动：拉起 dsh、完成 initialize
    await agent.init()
    try:
        runner = Runner(
            app_name="demo",
            agent=agent,
            session_service=InMemorySessionService(),
        )

        async for event in runner.run_async(
            user_id="u1",
            session_id="s1",
            new_message=Content(parts=[Part(text="读一下 README 并总结")]),
            run_config=RunConfig(
                custom_data={
                    "tenant_id": "tenant-a",
                    "trace_id": "trace-123",
                },
            ),
        ):
            print(event)
    finally:
        # 进程退出：shutdown、回收 dsh 子进程
        await agent.destroy()


asyncio.run(main())
```

`init()` / `destroy()` 是幂等的；一个 DSHAgent 对应一个 DSHRuntime 和一个 dsh 子进程，应用启动时 `init()`，退出前 `destroy()`，不要在每个 `run_async` 里反复调用。

`runner.run_async()` 拿到的就是普通的 tRPC-Agent Event；DSHAgent 已经把 DSH 的 `session.assistant.delta` / `session.event` / `session.status` 转换过了。

## 3. 设计细节

### DSHAgent

DSHAgent 把一次 tRPC invocation 转成一轮 DSH run，再把 DSH 的输出转成 tRPC Event。它只做编排，协议细节在 `DSHRuntime`。

```python
class DSHAgent(BaseAgent):
    name: str
    description: str
    dsh_config: DSHConfig
    model: LLMModel | ModelFactory | None
    _runtime: DSHRuntime
    _state_store: DshSessionStateStore

    def __init__(
        self,
        name: str,
        dsh_config: DSHConfig | None = None,
        model: LLMModel | ModelFactory | None = None,
        *,
        description: str = '',
    ) -> None:
        super().__init__(name=name, description=description)
        self.dsh_config = dsh_config or DSHConfig()
        self.model = model
        self._runtime = DSHRuntime(self.dsh_config)
        self._state_store = DshSessionStateStore()

    async def init(self) -> None:
        # 拉起 dsh 子进程，完成 initialize 握手
        # 静态 LLMModel 转成默认 route；ModelFactory 等每轮再解析
        await self._runtime.init(default_model=...)

    async def destroy(self) -> None:
        # shutdown，回收子进程
        await self._runtime.destroy()

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        state = self._state_store.load(ctx.session)  # 上一轮 JSONL
        req = ...  # TrpcPromptRequest：本轮输入 + JSONL + model + custom_data

        cancel_event = await ctx.get_cancel_event()
        try:
            async for frame in self._runtime.run(req, cancel_event=cancel_event):
                if frame.kind == 'event':
                    self._state_store.append_event(state, frame.data)  # session.event → 本地 JSONL
                event = to_trpc_event(frame)
                if event is not None:
                    yield event  # delta / event → tRPC Event
        finally:
            # 正常结束和被取消都写回；取消时包含 partial 输出
            ctx.actions.state_delta[DSH_SESSION_STATE_KEY] = self._state_store.state_delta(state)

        await ctx.raise_if_cancelled()

    # 不定义 cancel：取消统一走 Runner.cancel_run_async()，
    # _run_async_impl 把 ctx 的 cancel event 交给 DSHRuntime。
```

`init()` / `destroy()` 是应用生命周期，不是每轮调用。`_run_async_impl` 只做编排：从 `SessionService` 取上一轮 JSONL，组装本轮 `TrpcPromptRequest`，消费 `DSHRuntime.run()` 的 frame，再把新的 JSONL 写回 `state_delta`。`session.event` 的 JSONL append 由 `DshSessionStateStore` 负责，模型解析和 `custom_data` 填充都在组装请求时完成。

取消走 `Runner.cancel_run_async()`：它置位当前 invocation 的 cancel event，`_run_async_impl` 通过 `ctx.get_cancel_event()` 拿到后交给 `DSHRuntime.run()`。runtime 发现置位就发 `session/trpc-cancel`，并继续读到 `status idle`，所以中断产生的 partial 输出仍会通过 `session.event` 回来。写回 state 之后 `ctx.raise_if_cancelled()` 抛出 `RunCancelledException`，Runner 按正常取消流程补发 `AgentCancelledEvent` 并做 session 清理。

**整体架构。**

```text
┌──────────────────────────┐      ┌────────────────────────────────────────────┐      ┌────────────────────────────────────────────────────────────────────┐
│ DSH Profile              │      │ DSHRuntime (Python)                        │      │ DSHProcess                                                         │
│                          │      │                                            │      │              ┌────────────────────────────────┐                    │
│ profiles/<name>/         │      │                                            │      │              │ sdk-jsonrpc-server             │                    │
│ ├── package.json         │      │                                            │      │              │ DSH 官方插件                   │                    │
│ │   dsh.profile.bundles  │      │                                            │      │              └────────────────────────────────┘                    │
│ │   ├── DSH 官方插件     │      │                                            │      │                               ▼ 扩展                               │
│ │   └── 用户插件         │      │                                            │      │     ┌────────────────────────────────────────────────────┐         │
│ ├── cordis.patch.yml     │ 读取 │ ┌────────────────────────────────────────┐ │      │     │ trpc-jsonrpc-server 插件                           │         │
│ ├── node_modules/        ├──────┼►│ init                                   ├─┼──────┼────►│ ├── initialize        ← DSHRuntime.init            │         │
│ └── plugins/             │      │ │ 用户 Profile + 框架 patch              │ │      │     │ ├── session.assistant.delta                        │         │
│                          │      │ │ 构造 trpc-cordis.patch.yaml（覆盖）    │ │      │     │ │   ← agent/assistant-stream                       │         │
└──────────────────────────┘      │ └────────────────────────────────────────┘ │      │     │                                                    │         │
                                  │ run(req)      ── session/trpc-prompt       ├──────┼────►│ ├── session/trpc-prompt ← DSHRuntime.run           │         │
                                  │ cancel(sessionId) ── session/trpc-cancel   ├──────┼────►│ ├── session/trpc-cancel ← DSHRuntime.cancel        │         │
                                  │ destroy()     ── shutdown                  ├──────┼────►│ └── shutdown          ← DSHRuntime.destroy         │         │
                                  │                                            │      │     └────────────────────────────────────────────────────┘         │
                                  │ 内部：                                     │      │                   ▲ 被使用                       ▲ 被使用          │
                                  │ ├── dsh 子进程         spawn / stop        │      │     ┌────────────────────────────┐ ┌────────────────────────────┐  │
                                  │ ├── JsonRpcClient request() / subscribe()  │      │     │ trpc-session-backend 插件  │ │ trpc-request-context 插件  │  │
                                  │ └── sessionId → Queue（client 内部）       │      │     │                            │ │                            │  │
                                  │                                            │      │     └────────────────────────────┘ └────────────────────────────┘  │
                                  └────────────────────────────────────────────┘      │                                                  ▼ metadata        │
                                                                                      │                                    ┌────────────────────────────┐  │
                                                                                      │                                    │ 用户插件                   │  │
                                                                                      │                                    │ business tools             │  │
                                                                                      │                                    └────────────────────────────┘  │
                                                                                      │                                                                    │
                                                                                      │     ┌────────────────────────────────────────────────────┐         │
                                                                                      │     │ DSH AgentLoop / tools / skills                     │         │
                                                                                      │     └────────────────────────────────────────────────────┘         │
                                                                                      │                                                                    │
                                                                                      └────────────────────────────────────────────────────────────────────┘
```

Python 侧的 `DSHRuntime` 是 DSHAgent 内部的运行时层，只负责和 JS 侧的 `DSHProcess` 对接；tRPC Event 的转换和 `SessionService.state` 的读写留在 DSHAgent 层。两边的方法对应关系：

| Python `DSHRuntime` | JS handle | 语义 |
|---|---|---|
| `init()` | `initialize` | 拉起 runtime，握手 |
| `destroy()` | `shutdown` | 停止 runtime，回收进程 |
| `run(req, cancel_event)` | `session/trpc-prompt` / `session/trpc-cancel` | 提交一轮请求；cancel_event 置位时发取消通知，读到 idle 再返回 |
| `cancel(sessionId)` | `session/trpc-cancel` | 单向通知，`run()` 内部在取消时调用 |

`session.assistant.delta` 是 server → client 的推送，不是 Python 方法；它是唯一新增的通知，`session.event` / `session.status` 复用官方。

**调用时序。**

正常一轮：

```text
DSHRuntime (Python)            DSHProcess                          DSH AgentLoop
   │                              │                                      │
   │ rpc.subscribe(sessionId)     │                                      │
   │ session/trpc-prompt          │                                      │
   │ { sessionId, contentBlocks,  │                                      │
   │   jsonl, version, model,     │                                      │
   │   metadata }                 │                                      │
   ├─────────────────────────────►│                                      │
   │                              │ RunManager.start(sessionId)          │
   │                              │ backend.seed(jsonl, version)         │
   │                              │ agents.resume(agentOptions)          │
   │                              │ subscribe assistant-stream           │
   │                              │ agent.followup(userMessage)          │
   │◄── receipt {messageId} ──────┤                                      │
   │                              │◄── assistant-stream chunk ───────────┤
   │◄── session.assistant.delta ──┤                                      │
   │                              │◄── session/event ────────────────────┤
   │◄── session.event(tool/call) ─┤                                      │
   │                              │◄── session/event ────────────────────┤
   │◄── session.event(tool/result)┤                                      │
   │                              │◄── turn/end ─────────────────────────┤
   │◄── session.event(turn/end) ──┤                                      │
   │◄── session.status(idle) ─────┤                                      │
   │                              │                                      │
   ├── append events → jsonl      │                                      │
```

`run()` 先 `rpc.subscribe(sessionId)` 再发 prompt，收到 `status idle` 后退出订阅；这样 receipt 之后紧接着到达的通知不会落在没有队列的 session 上。`session.event` 的 `event` 直接 append 到本地 JSONL，`turn/end` 之后用 `status idle` 收尾。

取消：

```text
Runner.cancel_run_async         DSHRuntime (Python)           DSHProcess                    DSH AgentLoop
   │                            │                             │                             │
   │ set cancel event           │                             │                             │
   ├───────────────────────────►│                             │                             │
   │                            │ session/trpc-cancel         │                             │
   │                            ├────────────────────────────►│                             │
   │                            │                             │ RunManager.cancel(sessionId)│
   │                            │                             │ RunSession.cancel           │
   │                            │                             │ agent.cancel(user)          │
   │                            │                             │      turn/end (interrupted) │
   │                            │                             │◄────────────────────────────┤
   │                            │ session.event(turn/end)     │                             │
   │                            │                             │◄────────────────────────────┤
   │                            │ session.status(idle)        │                             │
   │                            │                             │◄────────────────────────────┤
   ▼                            ▼                             ▼                             ▼
```

`session/trpc-cancel` 之后 runtime 不立刻返回，而是继续消费通知直到 `status idle`，把中断产生的 `session.event` 一并 append 到本地 JSONL，然后再写回 state。之后 `ctx.raise_if_cancelled()` 让 Runner 走正常的取消收尾。

一个 session 同一时刻只允许一轮：`RunManager` 维护 `sessionId → RunSession`，重复的 `session/trpc-prompt` 直接回 `session busy`，一轮结束（`status idle`）后再从 map 里删掉。不同 session 各自有 `RunSession`，可以并发；`JsonRpcClient` 内部按 `sessionId` 把通知分发到各自的队列。这样 `sessionId` 本身就是 run 的路由键，不需要额外的 `runId`。

**类与接口。**

`DSHAgent` 对外只有构造、`init`、`destroy`，以及继承自 `BaseAgent` 的 `run_async`；真正的 DSH 适配拆给 `DSHRuntime`。类关系：

```text
DSHAgent
├── DSHRuntime
│   └── JsonRpcClient
├── DshSessionStateStore
└── to_trpc_event()
```

`DSHAgent` 的类定义见本节开头；下面是它依赖的类型和 runtime 接口。

```python
ModelFactory = Callable[[dict[str, Any]], Awaitable[LLMModel]]

@dataclass(frozen=True)
class DSHConfig:
    profile_dir: str = 'profiles/sdk'
    dsh_home: str | None = None
    cwd: str | None = None
    dsh_bin: str = 'dsh'
    env: dict[str, str] = field(default_factory=dict)
    patches: tuple[str, ...] = ()

@dataclass(frozen=True)
class DSHModelSpec:
    provider: str
    model: str
    reasoning_effort: str | None = None
    max_tokens: int | None = None

@dataclass
class DshSessionState:
    jsonl: str
    version: int

@dataclass(frozen=True)
class Notification:
    method: str
    params: dict[str, Any]

@dataclass(frozen=True)
class Frame:
    kind: Literal['delta', 'event', 'status']
    data: dict[str, Any]

class DSHRuntime:
    def __init__(self, config: DSHConfig) -> None: ...
    async def init(self, default_model: DSHModelSpec | None = None) -> None: ...
    async def destroy(self) -> None: ...
    async def run(
        self,
        req: TrpcPromptRequest,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[Frame]: ...
    async def cancel(self, session_id: str) -> None: ...

class JsonRpcClient:
    def __init__(self, stdin, stdout) -> None: ...
    async def request(self, method: str, params: dict) -> dict: ...
    @asynccontextmanager
    async def subscribe(self, session_id: str) -> AsyncIterator[asyncio.Queue[Notification]]: ...

class DshSessionStateStore:
    def load(self, session: Session) -> DshSessionState: ...
    def append_event(self, state: DshSessionState, event: dict) -> None: ...
    def state_delta(self, state: DshSessionState) -> dict: ...

def to_trpc_event(frame: Frame) -> Event | None: ...
```

`DSHAgent` 的构造参数：

| 参数 | 类型 | 说明 |
|---|---|---|
| `name` | `str` | Agent 名称，传给 `BaseAgent` |
| `dsh_config` | `DSHConfig \| None` | DSH runtime 配置；不传时用 `DSHConfig()` |
| `model` | `LLMModel \| ModelFactory \| None` | 模型选择；`None` 表示用 profile 默认 |
| `description` | `str` | Agent 描述，可选 |

`DSHConfig` 的字段：

| 字段 | 默认值 | 说明 |
|---|---|---|
| `profile_dir` | `"profiles/sdk"` | profile 目录；支持绝对路径和相对路径 |
| `dsh_home` | `None` | 不传时从 `profile_dir` 推导；支持绝对/相对路径 |
| `cwd` | `None` | 写进 DSH session header 的工作目录；不传时用 Python 进程 cwd |
| `dsh_bin` | `"dsh"` | dsh 可执行文件 |
| `env` | `{}` | 传给 dsh 子进程的环境变量，例如 provider 的 API key |
| `patches` | `()` | 额外的 DSH patch 文件，按 cwd 解析 |

`profile_dir`、`dsh_home`、`cwd` 的相对路径都按 **Python 进程的 cwd** 解析；`profile_dir` 默认 `"profiles/sdk"`，即默认使用相对路径。`dsh_home` 不传时取 `profile_dir` 的 `parent.parent`，所以 `profiles/sdk` 对应的 `dsh_home` 就是 Python 进程 cwd。

对应 DSH SDK 的 `DeepSeekHarnessConfig`：`dsh_home` / `cwd` / `dsh_bin` / `env` / `patches` 透传；`profile_dir` 拆成 `profile` 名和 `DSH_HOME`；`model` 不放进 config，由 DSHAgent 每轮通过 `session/trpc-prompt` 传。

`DSHAgent` 的方法职责：

| 方法 | 职责 |
|---|---|
| `__init__` | 保存 `dsh_config` / `model`，创建 `DSHRuntime` 和 `DshSessionStateStore` |
| `init()` | 转调 `DSHRuntime.init()`，拉起 dsh、完成握手；幂等 |
| `destroy()` | 转调 `DSHRuntime.destroy()`，shutdown、回收子进程；幂等 |
| `_run_async_impl(ctx)` | 组装 `TrpcPromptRequest`、消费 `Frame`、yield tRPC Event、写回 JSONL state |

`DSHAgent.init()` / `destroy()` 分别转调 `DSHRuntime.init()` / `destroy()`，并且是幂等的；应用进程启动时调 `init()`，退出前调 `destroy()`。

`DSHRuntime.init(default_model=None)` 负责发 `initialize`：`default_model` 是启动时的默认 route；传 `None` 时由 DSH profile / cordis 的默认模型决定。`DSHAgent` 只有在 `model` 是静态 `LLMModel` 时才把它转成 `DSHModelSpec` 传给 `init()`；`ModelFactory` 要等每轮拿到 `custom_data` 才能解析，所以 init 阶段不解析它。

`model` 支持三种形式：

| 传入 | 行为 |
|---|---|
| `None` | 返回 `None`，`session/trpc-prompt` 不带 `model`，使用 DSH profile / cordis 里的默认模型 |
| `LLMModel`（例如 `OpenAIModel`） | 转成 `DSHModelSpec`，随本轮 prompt 发送，DSH 侧按轮选择模型 |
| `ModelFactory` | 每次 run 用 `ctx.run_config.custom_data` 调用回调，拿到 `LLMModel` 后再转成 `DSHModelSpec` |

v1 的 `OpenAIModel` 转换只映射 `provider='openai'` 和 `model=model.name`；`api_key` / `base_url` 不通过 JSON-RPC 传，仍由 DSH profile 的 provider 配置或 `DSHConfig.env` 提供（`env` 会注入 dsh 子进程）。

`Notification` 是 `{ method, params }`；`JsonRpcClient` 从 `params.sessionId` 取路由键。`Frame` 是内部归一化类型：`delta`（助手流）、`event`（`session.event`）、`status`（`session.status`）；不再有 `done` / `error` frame。`DshSessionStateStore.append_event()` 把 `session.event` 里的 `event` 按 `seq` append 到本地 JSONL，`state_delta()` 输出 `{ jsonl, version }` 写回 `SessionService`。

关系：`DSHAgent` 持有 `DSHRuntime` 和 `DshSessionStateStore`；`DSHRuntime` 自己负责 spawn/stop dsh 子进程，并持有 `JsonRpcClient`。`JsonRpcClient.subscribe(session_id)` 是唯一的长驻通知入口：内部按 `sessionId` 维护队列，进入时注册、退出时注销。`DSHRuntime.run()` 用 `async with rpc.subscribe(session_id) as queue` 订阅本 session，再发 `session/trpc-prompt`，之后从 `queue` 持续取 `Notification` 并 yield `Frame`，直到 `session.status idle`。`to_trpc_event()` 和 `DshSessionStateStore` 放在 Agent 层，不塞进 `DSHRuntime`。

### trpc-jsonrpc-server

**为什么要新增 trpc-prompt / trpc-cancel。**

官方 server 的接口不够用，主要有三个缺口：

| 缺口 | 官方接口 | 问题 |
|---|---|---|
| 流式返回 | `session/prompt` 只回 receipt；官方 Python SDK 的 `Session.run()` 同步阻塞，只返回 `RunResult`；`on_notification` 是等待 response 期间的回调，不是长驻订阅；server 也没有转发 `agent/assistant-stream` | 拿不到逐 token 的 delta |
| 请求定制 | `session/prompt` / `session/query` 只接收 `contentBlocks`；模型参数只在 `initialize` 设一次；也没有入参/出参承载 JSONL 快照 | 每轮的 metadata、模型、session 快照都没地方传 |
| 取消 | 官方没有取消当前轮的 handle | 只能等它跑完，或者杀进程 |

另外，官方接口 `session/query` 只返回 receipt，结束要再调 `session/wait`，JSONL 还得从本地文件读，所以也拿不到快照。举个例子：用户问“读一下 README 并总结”。

1. 客户端调 `session/query`，server 立刻回 `{kind: "prompt", messageId}`。这只是“消息收下了”，turn 才刚开始跑。
2. 真正的输出走 `session.event` notification（tool/call、assistant/message）；客户端不订阅就看不到。`assistant/message` 是每个 LLM step 结束后的完整消息，不是 token 级 delta。
3. 客户端想知道跑完没有，再调 `session/wait`；它等到 agent idle 后只回 `{}`，没有回答文本，也没有 JSONL。
4. 客户端可以一直订阅 `session.event`，按 `seq` 把事件 append 到本地 JSONL，自己维护快照；但官方 server 没有转发 `agent/assistant-stream`，token 级输出仍然拿不到。

所以需要我们自己扩展接口：

```text
session/trpc-prompt   带 metadata + model + jsonl 进来，同步返回 receipt；
                      输出走通知：session.assistant.delta / session.event / session.status
session/trpc-cancel   单向通知，中断当前轮
```

**扩展官方实现。**

`trpc-jsonrpc-server` 在官方 `@deepseek-ai/dsh-sdk-jsonrpc-server` 的实现上扩展，但不继承它的 server 类：

- transport 复用 `JsonRpcLineTransport` 的 stdio 按行分帧
- 不复用 `HarnessSdkJsonRpcServer`。它的构造函数绑定 notification 语义和一次性 `initialize` 的 session 管理；我们需要每轮指定模型参数、由请求携带 JSONL 快照，继承会把这些不需要的行为一起带进来，还会让同一个 session 同时挂在官方的 `sessions` map 和我们的 `RunManager` 上
- 按官方 server 的 handle 分发方式写出 `TrpcJsonRpcServer`，只保留自己的 `initialize` / `shutdown`，新增 `session/trpc-prompt` / `session/trpc-cancel`
- 复用官方 `session.event` / `session.status` 通知；额外订阅 `agent/assistant-stream`，转成 `session.assistant.delta`
- response 保持“一个 id 一个 response”：`session/trpc-prompt` 只回 `{ messageId }`，一轮的输出和结束都走通知，不再扩展出多帧 response

DSHAgent 走 `session/trpc-prompt` / `session/trpc-cancel`；`session.event` / `session.status` 继续使用。官方其余方法不暴露。

整体架构、调用时序和 Python 侧设计见上一节 `### DSHAgent`；这一节只讲 JS 侧的插件实现和协议。

**JS 侧类与接口。**

`trpc-jsonrpc-server` 插件入口只暴露 `apply(ctx)`，内部类负责分发、run 生命周期和 delta 写出：

```ts
export function apply(ctx: Context): void

class TrpcJsonRpcServer {
  handleRequest('session/trpc-prompt', params): Promise<TrpcPromptResult>
  handleNotification('session/trpc-cancel', params): void
}

class RunManager {
  start(req: TrpcPromptRequest): Promise<RunSession>
  cancel(sessionId: string): void
}

class RunSession {
  start(): Promise<{ messageId: string }>
  cancel(): void
  dispose(): void
}

interface TrpcSessionBackend {
  seed(sessionId: string, jsonl: string, version: number): void
  snapshot(sessionId: string): { jsonl: string; version: number }
  drop(sessionId: string): void
}

interface RunContext {
  sessionId: string
  metadata: Record<string, unknown>
}

class TrpcRequestContext {
  set(context: RunContext): void
  get(sessionId: string): RunContext | undefined
  clear(sessionId: string): void
}
```

关系：`TrpcJsonRpcServer` 持有 `RunManager`；`RunManager` 的 map 是 `sessionId → RunSession`，重复 prompt 直接抛 `session busy`。`RunSession.start()` 先 `backend.seed(sessionId, jsonl, version)`，再带 `agentOptions` 调 `ctx.agents.resume({ resumeSessionId: sessionId, agentOptions })`；AgentLoop 通过 `sessionPersistence.open()` 拿到内存 handle。拿到 agent 后订阅 `agent/assistant-stream`、调用 `agent.followup()`，然后返回 `{ messageId }`。assistant-stream 的 chunk 由 `RunSession` 过滤到当前 agent，维护 `seq`，直接写 `transport.notify('session.assistant.delta', ...)`；`session.event` / `session.status` 仍由 `TrpcJsonRpcServer` 的全局监听转发。`TrpcJsonRpcServer` 收到 `status idle` 后调 `RunSession.dispose()`，`RunManager` 再把它从 map 里移除。

`RunSession` 开始时把 `{ sessionId, metadata }` 写进 `TrpcRequestContext`，结束时清掉；业务工具和 policy 插件通过 `ctx.get('trpcRequestContext')` 按 `sessionId` 读取。`TrpcRequestContext` 单独做成插件，工具只依赖这个 service，不依赖 `trpc-jsonrpc-server`。

协议侧的类型：

```ts
interface TrpcPromptRequest {
  sessionId: string
  contentBlocks: ContentBlock[]
  jsonl: string
  version: number
  model?: { provider: string; model: string; reasoningEffort?: string; maxTokens?: number }
  metadata: Record<string, unknown>
}

interface TrpcPromptResult {
  messageId: string
}

interface AssistantDelta {
  sessionId: string
  seq: number
  attemptId: string
  revision: number
  index: number
  kind: 'text' | 'reasoning' | 'tool-call'
  text?: string
  toolCall?: { id: string; name?: string; argumentsDelta: string }
}
```

`session/trpc-cancel` 的 params 只有 `{ sessionId: string }`。`seq` 是每个 session 内递增的 delta 通知序号；`attemptId` / `revision` 在模型重试时会变，client 据此丢弃上一个 attempt 的 partial 文本；`index` 是文本 block 的序号。`session.event` / `session.status` 直接复用官方通知，没有新增字段。

### trpc-session-backend

**为什么不用官方 JSONL backend。**

dsh 的 AgentLoop 在 resume 时走 `ctx.sessionPersistence`：

```text
agents.resume({ resumeSessionId })
        │
        ▼
agent-loop.resume()
        │
        ├── sessionPersistence.open(id, 'write')   ← 拿 SessionHandle
        ├── handle.read(0)                         ← 读已有事件
        ├── session.append(...)                    ← 运行期写
        └── handle.flush()                         ← 持久化
```

官方 `session-persistence-jsonl` 把 handle 落到 `$DSH_HOME` 的 `.jsonl.zstd` 文件。我们的会话状态在 tRPC `SessionService.state[sessionId]`，每轮 `session/trpc-prompt` 都会带完整 JSONL 进来；如果继续用官方 backend，就会多一份本地文件作为第二 source of truth，还会引入 lease、清理、跨副本一致性问题。

`trpc-session-backend` 实现同一个 `SessionPersistence` seam（继承 `@deepseek-ai/dsh-session-persistence` 的 `SessionPersistence`），存储换成请求级内存：

- 不写磁盘，不依赖 `$DSH_HOME`
- `seed()` 从请求 JSONL 建内存 session
- AgentLoop 照常 `ctx.agents.resume()`
- 一轮结束 `drop()`，下一轮再从客户端快照重建

```text
┌──────────────────────────────────────────────────────────────┐
│ trpc-session-backend 插件                                    │
│                                                              │
│  ctx.sessionPersistence   ← AgentLoop 的 resume/open         │
│  ctx.trpcSessionBackend   ← trpc-jsonrpc-server 的 seed/drop │
│                                                              │
│  Map<SessionId, MemorySession>                               │
│  ├── header: SessionHeader                                   │
│  ├── events: SessionEvent[]                                  │
│  └── inheritedEventCount                                     │
└──────────────────────────────────────────────────────────────┘
```

`trpc-jsonrpc-server` 只依赖 `seed/snapshot/drop` 三个方法；`open/create/read/append/flush/stat/list` 是 `SessionPersistence` 的接口，由 AgentLoop 和 dsh 内部使用。插件加载时 `new TrpcSessionBackend(ctx)`，构造器里的 `super(ctx)` 把它注册成 `ctx.sessionPersistence`；插件再把同一实例挂成 `ctx.trpcSessionBackend`。

**profile patch。** 官方 profile 默认挂 `session-persistence-jsonl`；框架 patch 里要先把它 disable，再插入 `trpc-session-backend`：

```yaml
- id: session-persistence-jsonl
  disabled: true

- insert:
    - id: trpc-session-backend
      name: 'file:///.../trpc-session-backend.mjs'
```

否则两个 `SessionPersistence` provider 会冲突。

**一轮的生命周期。**

```text
RunSession.start()
   │
   ├── backend.seed(sessionId, jsonl, version)
   │      ├── jsonl 为空 → 新建 header
   │      └── jsonl 非空 → scanLog(jsonl) → header + events
   │
   ├── ctx.agents.resume({ resumeSessionId: sessionId })
   │      └── AgentLoop → sessionPersistence.open(id, 'write')
   │             └── MemorySessionHandle
   │
   ├── agent.followup(...)
   └── status idle
          ├── RunSession.dispose()          （退订 + 关 agent handle）
          ├── backend.snapshot(sessionId)   （自检/诊断）
          └── backend.drop(sessionId)
```

**seed。**

```text
seed(sessionId, jsonl, version):
  if jsonl == "":
      header = {
        id: sessionId, version, createdAt: now,
        isSeeded: false, delegationDepth: 0,
        ...cwd 可选
      }
      events = []
      inheritedEventCount = 0
  else:
      scan = scanLog(Buffer.from(jsonl))
      if scan.meta.version != version: throw
      header = scan.meta
      events = scan.events
      inheritedEventCount = scan.inheritedEventCount

  sessions.set(sessionId, { header, events, inheritedEventCount })
```

- 直接复用官方 JSONL format 的 `scanLog()`，不再写一套 JSONL parser。`scanLog` / `toHeaderLine` / `eventLines` 都来自 `@deepseek-ai/dsh-session-persistence-jsonl/format`。
- `version` 必须和 header 里的版本一致；不一致直接拒绝，不做隐式迁移。
- `inheritedEventCount` 原样保留，fork / subagent 的 lineage 不会丢。
- 空 JSONL 表示第一轮，新建 header；非空表示 resume，事件必须从 seq 0 连续。
- `seed` 时如果这个 session 还有未关闭的 writer，直接拒绝，不覆盖。

**SessionHandle。**

`AgentLoop` 拿到的还是标准 `SessionHandle`，只是底层数组在内存里：

| 方法 | 行为 |
|---|---|
| `read(offset, length)` | 返回 `{ eventState, events }`，`events` 是连续前缀 |
| `append(events)` | 校验 `events[0].seq == session.events.length`，然后 push |
| `flush()` | 用 `toHeaderLine()` + `eventLines()` 序列化成 JSONL，存到 `snapshot` |
| `close()` | 释放 writer，保留快照直到 `drop()` |

`open(id, 'write')` 时如果已经有 writer，抛 `SessionAlreadyOwnedError`。`RunManager` 已经保证一个 session 同时只有一轮，这里是第二道保险。

**snapshot 和 drop。**

- `snapshot(sessionId)` 返回 `{ jsonl, version }`，主要给 `RunSession` 做自检、日志和测试对账。
- 客户端状态仍然由 `session.event` 增量重建，不依赖 `snapshot` 回传，所以主链路没有大 payload。
- `drop(sessionId)` 在 `status idle` 或异常路径的 `finally` 里调用，避免内存 Map 越积越大。

**边界。**

| | 官方 JSONL backend | trpc-session-backend |
|---|---|---|
| 输入 | 从 `$DSH_HOME` 读 | 请求里的 `jsonl` |
| 存储 | 本地文件 | 内存 Map |
| 输出 | 文件 | `snapshot()` |
| 生命周期 | 跨进程 | 一轮 |
| 多副本 | 共享 home / lease | 无共享状态 |

第一版刻意不做磁盘、压缩、迁移和跨进程 lease。JSONL 每轮全量传一次，简单但 payload 会随会话变长；后面要做增量，可以在 `session/trpc-prompt` 上加 `baseSeq` + 增量事件，backend 和 client 一起演进。

和 `trpc-request-context` 一样，这个插件由框架 patch 注入，用户 profile 不需要手动挂。

### trpc-request-context

`trpc-request-context` 解决的是“这一轮请求的业务上下文，怎么让用户 JS 插件拿到”。数据源是 `runner.run_async()` 的 `RunConfig.custom_data`。

```text
┌──────────────────────────────────────────────┐
│ Python 业务                                  │
│                                              │
│ runner.run_async(                            │
│   run_config=RunConfig(                      │
│     custom_data={                            │
│       "tenant_id": "tenant-a",               │
│       "trace_id": "trace-123",               │
│     }))                                      │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│ DSHAgent                                     │
│                                              │
│ metadata = ctx.run_config.custom_data        │
└──────────────────────┬───────────────────────┘
                       │ session/trpc-prompt
                       │ {sessionId, metadata, ...}
                       ▼
┌──────────────────────────────────────────────┐
│ RunSession.start()                           │
│                                              │
│ trpcRequestContext.set({                     │
│   sessionId, metadata                        │
│ })                                           │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│ 用户 JS 插件 / tool                          │
│                                              │
│ sessionId = exec.agent.session.id            │
│ ctx.get('trpcRequestContext')                │
│    .get(sessionId).metadata                  │
└──────────────────────────────────────────────┘
```

**Python 侧怎么传。**

```python
from trpc_agent_sdk.configs import RunConfig

run_config = RunConfig(
    custom_data={
        "tenant_id": "tenant-a",
        "trace_id": "trace-123",
        "user_token": "token-...",
    },
)

async for event in runner.run_async(
    user_id="u1",
    session_id="s1",
    new_message=Content(parts=[Part(text="...")]),
    run_config=run_config,
):
    ...
```

DSHAgent 在 `_run_async_impl(ctx)` 里把 `ctx.run_config.custom_data` 原样放进 `session/trpc-prompt` 的 `metadata`，不解释 key，只负责搬运。只要求它是可 JSON 序列化的值。

**JS 侧插件怎么提供。**

`trpc-request-context` 单独做成插件，内部就是一个 `Map<sessionId, RequestContext>`：

```js
export const name = 'trpc-request-context'

export function apply(ctx) {
  const contexts = new Map()

  ctx.provide('trpcRequestContext', {
    set({ sessionId, metadata }) {
      contexts.set(sessionId, { sessionId, metadata })
    },
    get(sessionId) {
      return contexts.get(sessionId)
    },
    clear(sessionId) {
      contexts.delete(sessionId)
    },
  })
}
```

`RunSession.start()` 在 `agent.followup()` 之前 `set`，在 `dispose()` 里 `clear`。所以工具执行期间一定拿得到，不会读到一个已经结束的 run。

`trpc-request-context` 由框架 patch 注入，用户插件不用自己安装；声明 `inject` 后直接 `ctx.get('trpcRequestContext')`。

**用户 JS 插件怎么用。**

工具插件声明依赖，然后在 `execute(args, exec)` 里用 `exec.agent.session.id` 拿 sessionId：

```js
import { defineTool } from '@deepseek-ai/dsh-tools'

export const name = 'tenant-tool'
export const inject = ['tools', 'trpcRequestContext']

export function apply(ctx) {
  ctx.tools.register(defineTool({
    name: 'tenant_lookup',
    description: 'Look up a tenant-scoped value.',
    parameters: {
      key: { type: 'string', required: true },
    },
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: value }],
    },
    async execute(args, exec) {
      const sessionId = String(exec.agent?.session.id ?? '')
      const request = ctx.get('trpcRequestContext')?.get(sessionId)
      if (!request) throw new Error(`missing request context for ${sessionId}`)

      const { tenant_id, trace_id } = request.metadata
      return `${tenant_id}/${trace_id}: ${args.key}`
    },
  }))
}
```

policy 插件也按同样的方式读：

```js
export const name = 'tenant-policy'
export const inject = ['tools', 'trpcRequestContext']

export function apply(ctx) {
  ctx.on('tools/pre-execute', (exec) => {
    const sessionId = String(exec.agent?.session.id ?? '')
    const metadata = ctx.get('trpcRequestContext')?.get(sessionId)?.metadata

    if (metadata?.tenant_id !== 'tenant-a') {
      return { kind: 'deny', reason: 'tenant mismatch' }
    }
    return { kind: 'allow' }
  })
}
```

**并发和隔离。**

```text
RunSession("s1") ── set ──► contexts["s1"] = {tenant_id: "a"}
RunSession("s2") ── set ──► contexts["s2"] = {tenant_id: "b"}

tool("s1") ── get("s1") ──► "a"
tool("s2") ── get("s2") ──► "b"
```

- 不要用全局变量或 `process.env` 存 per-session 信息；同一个 dsh 进程里可能同时跑多个 session。
- `TrpcRequestContext` 只保存 `{sessionId, metadata}`，不保存 JSONL、模型、agent 等 runtime 状态；runtime 状态由 `RunSession` 自己持有。
- 一个 session 同时只有一轮，`set/clear` 不会和同一 session 的另一轮打架；不同 session 的 key 不同，互不覆盖。
- 缺 context 时用户插件应该 fail closed；尤其是 policy 插件，不要在拿不到 metadata 时默认放行。
- metadata 可能包含 token 等敏感信息，不要打日志；它只存在内存里，run 结束就 `clear`。

这个机制对应第 5 章的通道 C/D：不是把 metadata 塞进 prompt 或 sessionId，而是用自定义 server 把 per-run `custom_data` 带到 dsh，再按 `sessionId` 放进框架侧的 side store。区别是这里的 store 只在内存里活一轮，不跨进程。

# DeepSeek Harness 调研

## 1. DSH Runtime 生命周期

DSH Python SDK 通过 stdio JSON-RPC 控制一个 dsh 子进程；一个 client 对应一个 runtime，启动和卸载都绑定在这个进程上。`DSH_HOME` 必须显式传入，第一次 `run` / `start_session` / `resume_session` 才真正拉起进程。

```text
┌────────────────────────────── Python 进程 ──────────────────────────────┐
│  HarnessClient / DeepSeekHarness                                        │
│                                                                         │
│   request / response ──┐                                                │
│   notification ────────┤                                                │
│                        ▼                                                │
│                 waiter / subscription registry                          │
│                        ▲                                                │
│   reader thread ◄──────┘  stdout: JSON-RPC                              │
│   stderr thread ◄──────── stderr: 诊断日志                              │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │ stdio
┌───────────────────────────────────▼─────────────────────────────────────┐
│  dsh runtime：dsh --profile sdk                                         │
│                                                                         │
│   profile / bundles / patches ──► plugin tree                           │
│                                    ├─ agent loop                        │
│                                    ├─ tools / skills / plan / goal      │
│                                    ├─ subagent                          │
│                                    └─ provider adapter / model route    │
│                                                                         │
│   $DSH_HOME/sessions/*.jsonl.zstd                                       │
└─────────────────────────────────────────────────────────────────────────┘
```

`initialize` 和 `close` 放在同一条时间线上：

```text
Python SDK                                      dsh runtime
    │                                                │
    │ 1. Popen(dsh --profile sdk)                    │
    │───────────────────────────────────────────────►│
    │                                                │ 加载 profile / bundles / patches
    │                                                │ 构建 plugin tree
    │ 2. initialize(cwd, provider, model,            │
    │    effort, maxTokens, approvals, userQuestions)│
    │───────────────────────────────────────────────►│
    │                                                │ 校验 adapter / model / effort
    │                                                │ 等 plugin tree settle
    │◄──────────────── serverInfo ───────────────────│
    │                                                │
    │ 3. session/query / prompt / resume             │
    │◄──────────────── session.event ────────────────│
    │                                                │
    │ 4. close()                                     │
    │    ├─ shutdown                                 │
    │    │──────────────────────────────────────────►│ abort lifetime
    │    │                                          │ 关闭 userQuestions
    │    │                                          │ 等待 pending creation / commands
    │    │                                          │ 移除 subscriptions
    │    │                                          │ drain agents / descendants
    │    │                                          │ dispose + flush persistence
    │    │◄──────────────── {} ──────────────────────│ exit 0
    │    ├─ close stdin                              │
    │    ├─ wait(shutdown_timeout)                   │
    │    ├─ terminate() / kill()                     │
    │    └─ fail pending waiters                     │
```

`initialize` 一次生效，决定整个进程的配置边界：

```text
initialize(cwd, provider, model, effort, maxTokens,
           approvalRequests, capabilities.userQuestions)
    │
    ├─ cwd ──────────────────────────────► 新 session 的 header.cwd
    │                                         └─► sandbox workspaceRoot
    │
    ├─ provider / model / effort / maxTokens ──► 进程级 model route
    │                                            （所有 session 共用）
    │
    ├─ approvalRequests ─────────────────► 是否接收 approval/request
    │
    ├─ capabilities.userQuestions ───────► 是否接收 user-question/ask
    │
    └─ profile / patches ────────────────► plugin tree / tools / skills / policy
```

进程级状态和 session 级状态：

```text
┌─────────────────────────── dsh runtime（进程级） ────────────────────────────┐
│                                                                              │
│  DSH_HOME   profile   credentials   settings   env / API key                 │
│  provider / model / effort / maxTokens                                       │
│  initialize.cwd ──► session.header.cwd ──► sandbox workspaceRoot             │
│  plugin / tool / skill / sandbox policy                                      │
│                                                                              │
│   ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐         │
│   │ Session A        │   │ Session B        │   │ Session C        │         │
│   │ event log        │   │ event log        │   │ event log        │         │
│   │ working dir      │   │ working dir      │   │ working dir      │         │
│   │ permission       │   │ permission       │   │ permission       │         │
│   │ goal / schedule  │   │ goal / schedule  │   │ goal / schedule  │         │
│   └──────────────────┘   └──────────────────┘   └──────────────────┘         │
│                                                                              │
│  进程级共享 ──► session 隔离 ≠ 租户隔离                                      │
└──────────────────────────────────────────────────────────────────────────────┘
```

`close()` 的降级路径和崩溃路径：

```text
close()
  │
  ├─ shutdown RPC ──► 成功 ──► close stdin ──► wait ──► exit 0
  │
  └─ 超时 / 失败 ──► close stdin
                     │
                     ├─ wait(shutdown_timeout)
                     ├─ terminate()
                     ├─ wait(shutdown_timeout)
                     └─ kill()

进程崩溃 / stdout EOF / reader exception
  │
  ▼
_fail_waiters(TransportClosedError)
  ├─ pending request / subscription 全部失败
  └─ diagnostics: exit code + stderr tail
```

Runtime 生命周期的关键事实：

| 事实 | 说明 |
|---|---|
| 一个进程一份 `DSH_HOME`/model/env/cwd | 所有 session 共享 |
| `initialize` 默认 30s | readiness 和进程级配置边界；失败 close runtime |
| shutdown drain + flush | 在途 turn 会被中断 |
| 没有自动重启 / per-session cancel | 生命周期由调用方管理 |
| 没有 protocol version negotiation | 版本兼容性由部署方保证 |
| stdout 只走 JSON-RPC | stderr 是诊断日志 |
| runtime wheel 平台受限 | Linux/macOS/Windows x64 |

SDK 没有 per-session close/cancel；关 runtime 会影响该 runtime 下所有 session。`shutdown_timeout_seconds` 默认 1s，先 `terminate()`，再超时才 `kill()`。

## 2. DSH Session 机制

dsh 的 Session 是执行和持久化的基本单位：一个 append-only 的 `SessionEvent` log，模型历史由 `deriveMessages()` 推导，不是单独存一份 messages。

```text
用户输入 / 工具调用 / 模型输出
              │
              ▼
┌────────────────────────────────────────────────────┐
│ SessionEvent log（append-only）                    │
│                                                    │
│  turn/start                                        │
│  user/message ──► step/start                       │
│  assistant/message ──► tool/call ──► tool/result   │
│  ... ──► turn/end                                  │
└──────────────────────────┬─────────────────────────┘
                           │ deriveMessages()
                           ▼
                  模型历史 / 工具上下文
```

log 里不只有聊天文本：

```text
SessionEvent
  ├─ surface events ──► 可直接映射成对话事件
  │    system/message, user/message,
  │    assistant/message, tool/result
  │
  └─ log-only events ─► 执行语义，不能丢
       turn/start, step/start, step/end, turn/end,
       assistant/attempt, request/header,
       compaction / plan / goal / permission / todo ...
```

- `assistant/message` 是 settled 结果，不是逐 token 的 delta；`stream` 是事件里的完整流，不是实时推送。
- `user/message` 区分真实用户输入和 dsh 注入的上下文。
- `tool/call` 和 `tool/result` 按 callId 配对；subagent 是 child session，带 `parentSession`。

**结论**：dsh 的执行状态不等于可读聊天记录；只靠消息文本，重建不了工具结构、compaction 边界和 plan/goal 状态。

生命周期：

```text
session/query(sessionId)
        │
        ▼
┌──────────────┐   首次        ┌──────────────────┐
│ no live      │──────────────►│ live agent       │
│ agent        │  create+log   │ + SessionEvent   │
└──────────────┘               └────────┬─────────┘
                                        │ process shutdown / crash
                                        ▼
                                 ┌──────────────────┐
                                 │ persisted log    │
                                 │ $DSH_HOME/...    │
                                 └────────┬─────────┘
                                          │ session/resume
                                          ▼
                                 ┌──────────────────┐
                                 │ live agent       │
                                 │ + repaired turn  │
                                 └──────────────────┘

resume 失败：missing / unreadable / incompatible / occupied
```

创建和恢复不是同一条路：

```text
创建                                     恢复
session/query(sessionId)                 session/resume(sessionId)
        │                                        │
        ▼                                        ▼
getOrCreateSession                       读 persistence log
        │                                        │
        ▼                                        ▼
agents.create(meta.cwd)                  修复未闭合 turn
        │                                        │
        ▼                                        ▼
空 log + 新 agent                        agents.resume(...)
                                                 │
                                                 ▼
                                         不重放历史通知
```

- `session/query` / `session/prompt` 带 `sessionId`，server 懒创建 agent；创建时 `meta.cwd = initialize.cwd`。
- `session/working-directory/get/set` 对未知 session 也会创建 agent，但不会跑模型 turn。
- `session/resume` 需要原 `DSH_HOME`/persistence store；不接受 subagent session，要 resume 它的 root。
- `start_session()` / `run(session_id=...)` 不会 resume；要恢复历史必须显式 `session/resume`。
- sessionId 是任意非空字符串；JSONL 会编码成路径安全的段。
- 没有 session list/stat/delete；没有 per-session close；agent 活到进程 shutdown。

持久化：

```text
session/event ──► append（best-effort）
                       │
                       ▼
                  handle.flush()
                       │
                       ▼
               $DSH_HOME/sessions/*.jsonl.zstd

crash / kill
  │
  ▼
session/resume
  ├─ 截断 torn tail
  ├─ 补 synthetic closers
  │    （缺失的 tool result、step/end、turn/end interrupted）
  └─ 重建 agent
```

- 默认 backend `dsh-session-persistence-jsonl`，root `$DSH_HOME/sessions`，JSONL + zstd。
- `zstd` 是 Zstandard 无损压缩：backend 把 header 和每个 append batch 分别压成独立 zstd frame，拼成 `.jsonl.zstd`，所以 append 不用重压整个文件。`compression: 'none'` 输出纯 `.jsonl`；同一 root 不能混用两种压缩模式。
- 持久化不是 Session 内建：`ctx.sessions` 只存内存，`ctx.sessionPersistence` 是 seam；`dsh-session-persistence-jsonl` 是默认 backend 插件。没有 backend 时 session 只在内存，进程退出就没了，`session/resume` 也不可用。
- 三层的可替换性不同：`ctx.sessions` 是 core，不是 seam，其他插件依赖它的 `SessionEvent` 语义；`ctx.sessionPersistence` 是 seam，可以换 backend；`dsh-session-persistence-jsonl` 是默认 provider，可以替换或 disable。
- 仓库里 shipped 的 `SessionPersistence` backend 只有 `dsh-session-persistence-jsonl`；没有 Redis / PostgreSQL / MySQL 实现。`session-query-sqlite` 是 SQLite FTS5 查询索引，不是 session 存储 backend。
- 旧版曾有 `@deepseek-ai/dsh-session-persistence-sqlite`，已在 v0.1.2-alpha.3 的 JSONL-only 简化中移除；当前版本不会打开或迁移旧 SQLite DB，需要旧版本导出。
- 第三方 backend：`@morlay/session-persistence-rdb` 是真正的 `SessionPersistence` backend，支持 SQLite 和 PostgreSQL（drizzle，发布在 GitHub Packages）；`@amaster.ai/dsh-storage` 是把 session 事件镜像到 MySQL/PostgreSQL/SQLite/SQL Server，JSONL 仍是 authoritative。
- 截至这次搜索，没有找到 Redis 的 `SessionPersistence` backend；`dsh-redis-plugin` 是通用 Redis 服务/工具，不是 session 存储。
- `base`/`sdk` profile 默认挂 `session-persistence-jsonl`，所以正常 SDK 启动会落盘；自定义 profile 可以 disable 或替换这个 row。
- `SessionHandle` 提供 read/append/flush/close；同一 session 单 writer。
- `append` best-effort；`flush` 是 durability barrier。
- `session/stat` / `session/list` 在 persistence seam 有，但 SDK wire 没暴露。

通知：

```text
dsh runtime
   │
   ├─ session.event ───────────► durable event，带 sessionId
   ├─ session.status ──────────► running / idle
   ├─ session.query.completed ─► 命令完成（commandId / finishReason）
   └─ subagent.started/finished► child session 生命周期

client subscription（按 session tree）
   ├─ root events
   ├─ child events
   └─ session/wait：root + managed descendants
```

高层 `Session.run()` 的边界：

```text
session/query ──► prompt receipt（messageId）
                       │
                       ▼
            等 root session.status=idle
                       │
                       ▼
                  RunResult
                       ├─ final_response
                       ├─ finish_reason
                       ├─ events
                       └─ notifications

命令：session/query.completed → RunResult
```

- `messageId` 只是入队 receipt，不是 per-prompt result。
- `turn/end.reason.kind`：`completed` / `max-tokens` / `aborted` / `blocked` / `error` / `interrupted` / `forked`。
- `session.status=idle` 是 root agent 的 idle；`session/wait` 才覆盖 root + descendants。

输入路由：

```text
session/query(input)
        │
        ├─ 第一个 text block 是 /compact|/permission|/plan|/goal
        │        └─► native command
        │              └─► session.query.completed
        │
        └─ 其他 ──► prompt
                     └─► prompt queue ──► turn ──► status idle

content blocks: text + encoded image (png/jpeg/webp/gif)
图片：attachment store → durable reference
客户端不能传任意 file reference
```

- `session/prompt` 是低层入队，不做 slash command 路由。
- `/plan` review 需要 user question answerer；没有 answerer 会报 `NO_PROVIDER`。
- prompt 进队列；同一 session 的多个输入可以排队。

并发：

```text
dsh process
  ├─ Session A: prompt queue ──► turn ──► turn
  ├─ Session B: prompt queue ──► turn
  └─ Session C: idle

不同 session 可以并发
同一 session 按 turn 串行
没有 per-session cancel/close
```

- `session.event` 带 `sessionId`；订阅按 session/session tree 过滤。
- 要停止单个 session，只能关整个 runtime 或停止消费事件。
- `session/wait` 等 descendants；高层 `Session.run()` 可能在 root idle 时返回。
- SDK server 没有全局 session 并发上限；`maxParallelToolCalls` 默认 10，是单个 step 的工具并发，不是 session 上限。

Session 自己拥有什么：

```text
┌──────────────── Session ────────────────┐
│ event log / 模型历史                    │
│ 有效工作目录（working-directory/set）   │
│ permission preset / approval policy     │
│ goal / schedule                         │
│ child sessions（subagent）              │
└─────────────────────────────────────────┘

其余资源是进程级：DSH_HOME / model route / env /
cwd→header.cwd→sandbox root / tools / skills
```

- `working-directory/set` 只改有效目录，不改 `header.cwd`，也不改 sandbox root。
- 单进程多 session 只做 session 隔离；租户隔离要拆 runtime。

Session 的标识只有 `SessionId`，没有用户/租户字段：

```text
SessionHeader
  ├─ id
  ├─ createdAt
  ├─ cwd
  ├─ parentSession
  ├─ origin
  ├─ delegationDepth
  └─ agentPreset
  ✗ user / tenant / owner

SessionId 的生成方
  ├─ Python SDK: start_session() / run() ──► session-<uuid4hex>
  ├─ headless: --session-id 或 ────────────► session-<randomUUID()>
  ├─ SessionStore.create(id?) ─────────────► 省略 id 时 session-<counter>（插件/内部）
  └─ SDK server / HarnessClient ───────────► 调用方给什么就用什么（非空字符串）
```

- `SessionId` 是任意非空字符串；JSONL 路径用 `encodeSegment` 转义，防路径穿越，但这不是授权。
- 同一进程内 live store 以 id 为唯一键，重复 id 会拒绝；`ctx.sessions.get/list` 按 id 访问，没有 ACL/owner 检查。
- SDK wire 只要求非空 `sessionId`，不校验调用者身份。
- `user/message` 的 `source.kind='user'` 只是消息来源，不是账号。
- 要用户/租户隔离，只能拆进程/`DSH_HOME`/env/cwd；`SessionId` 只是查找键。

清理与保留：

```text
外部删除操作 ──✗──► dsh session 还在
runtime shutdown ─────────► 释放所有 live agent
out-of-band janitor ──────► 删 $DSH_HOME/sessions
subagent ─────────────────► 不能直接 resume，要 resume root
```

- SDK 没有 delete；没有 per-session close；runtime 重启才释放所有 session。
- dsh 没有 delete API；要删持久化日志只能 out-of-band janitor，或按租户回收整个 `DSH_HOME`。
- 内部有 `AgentHandle.dispose()`：停 loop、unregister agent、移除 session、关 persistence handle；但 handle 是创建方 capability，SDK server 持有，SDK wire 不暴露。
- 所以标准 SDK 不能回收单个 live session；只能 recycle runtime，或自己 patch SDK server/实现自定义 runtime 管理。
- schedule/goal 可以在没有外部请求时驱动 session；没有订阅方时事件可能不会被消费。

看一个实际插件。下面的 `session-admin` 把增删改查挂在一个 Cordis service 上，依赖 live store、persistence 和 query 三个服务；对应的包是 `@deepseek-ai/dsh-session`、`@deepseek-ai/dsh-session-persistence`、`@deepseek-ai/dsh-session-query`。

```yaml
# profile 的 cordis.patch.yml
- insert:
    - id: session-admin
      name: '@acme/dsh-session-admin'
```

```ts
import { Context, Service } from '@deepseek-ai/cordis'
import { SessionId, type Session } from '@deepseek-ai/dsh-session'
import type {} from '@deepseek-ai/dsh-session-persistence'
import type {} from '@deepseek-ai/dsh-session-query'
import { rm } from 'node:fs/promises'

export const name = 'session-admin'
export const inject = ['sessions', 'sessionPersistence', 'sessionQuery']

export default class SessionAdmin extends Service {
  static inject = ['sessions', 'sessionPersistence', 'sessionQuery']

  private readonly detachById = new Map<string, () => void>()

  constructor(ctx: Context) {
    super(ctx, 'sessionAdmin')
  }

  // 增：prepare → enter → announce，和 ctx.sessions.create 内部是同一套事务。
  // 拿 detach 是为了后面能单独删；普通场景直接 ctx.sessions.create 就行。
  create(id: string, cwd: string): Session {
    const sessionId = SessionId(id)
    const session = this.ctx.sessions.prepare(sessionId, { meta: { cwd } })
    const detach = this.ctx.sessions.enter(session)
    try {
      this.ctx.sessions.announce(session)
    } catch (error) {
      detach()
      throw error
    }
    this.ctx.effect(() => detach, `sessionAdmin.create(${id})`)
    this.detachById.set(id, detach)
    return session
  }

  // 查：live store
  getLive(id: string): Session | undefined {
    return this.ctx.sessions.get(SessionId(id))
  }

  listLive(): Session[] {
    return this.ctx.sessions.list()
  }

  // 查：live + persisted，newest first
  async listAll() {
    return this.ctx.sessionQuery.listSessions()
  }

  // 查：完整 event log，不要求 session 是 live
  async readLog(id: string) {
    return this.ctx.sessionQuery.readSession(SessionId(id))
  }

  // 查：持久化元数据
  async stat(id: string) {
    return this.ctx.sessionPersistence.stat(SessionId(id))
  }

  // 改：事件溯源只能 append，不能改历史
  async appendUserMessage(id: string, text: string): Promise<void> {
    const session = this.getLive(id)
    if (session === undefined) throw new Error(`session ${id} is not live`)
    session.append('user/message', {
      role: 'user',
      content: [{ type: 'text', text }],
      source: { kind: 'user' },
    }, { surfaceOp: 'append' })
    await this.ctx.sessions.flush(session)
  }

  // 删：只删本插件创建的 live session
  deleteLive(id: string): boolean {
    const detach = this.detachById.get(id)
    if (detach === undefined) return false
    detach()
    this.detachById.delete(id)
    return true
  }

  // 删持久化：dsh 没有 delete API。这里只是示意 out-of-band 删除产物；
  // 生产环境要确认没有 writer，并清理 backend 的 lease/index，
  // 更稳的做法是实现自定义 SessionPersistence 或外部 janitor。
  async deleteStoredArtifact(id: string): Promise<boolean> {
    const sessionId = SessionId(id)
    if (this.ctx.sessions.get(sessionId) !== undefined) {
      throw new Error('detach the live session first')
    }
    const snapshot = await this.ctx.sessionPersistence.stat(sessionId)
    if (snapshot?.location === undefined) return false
    await rm(snapshot.location.path, { force: true })
    return true
  }
}
```

```text
              live session                         persisted log
增   prepare → enter → announce            session/event → backend write handle
查   ctx.sessions.get/list                 ctx.sessionPersistence.stat/list
                                           ctx.sessionQuery.listSessions/readSession
改   session.append(...) + flush           live append → durable
删   detach disposer                       ✗ 没有 API
                                           out-of-band / 自定义 backend
```

- `prepare` / `enter` / `announce` 是 `ctx.sessions.create` 内部的同一套事务；普通场景直接用 `create`，需要单独 `detach` 时才走低级路径。
- `detach` 只从 live store 移除并触发 `session/disposed`，持久化日志还在；`deleteStoredArtifact` 是 out-of-band 示例，不是受支持的删除 API。
- 不要 `detach` 正在被 agent 驱动的 session；先停 turn/agent，否则它可能拿着已 disposed 的 session 继续跑。
- 普通插件创建 live session 不要调用 `sessionPersistence.create`；持久化 backend 会订阅 `session/event` 自动写。`sessionPersistence.create` 是给自定义 live write path 用的。
- `sessionQuery` 需要挂 `session-query-sqlite` 之类的 backend；没有就退回 `sessionPersistence.stat/list`。
- `append` 不能改历史；surface event 需要合法的 `surfaceOp` 和完整结构，不要手写半成品。
- 冷 session 的写入走 `sessionPersistence.open(id, 'write')` + `handle.append(events)`，事件要连续 `seq`；普通插件不要直接做，交给 agent-loop/resume。
- 只能 `detach` 自己创建的 session；其他插件/agent 创建的 session，公开 API 拿不到它的 detach 句柄。
- 这些是插件层 API；SDK wire 仍然没有 `session/list`、`session/stat`、`delete`。

**调研结论**：dsh session 以 `SessionId` 为唯一标识，event log 是执行真值；持久化和恢复依赖 `DSH_HOME`/persistence store。session 没有用户/租户字段，也没有 per-session cancel/close/delete；单进程多 session 只做 session 隔离，不构成租户隔离。`SessionId` 本身不是授权边界：任何拿到 `ctx.sessions` 的插件都能按 id 读写 live session，SDK wire 也不校验调用者身份。

## 3. DSH Interrupt 机制

先给结论：`DeepSeekHarness` 没有 per-session / per-turn interrupt。`Session` / `HarnessClient` 没有 `cancel()`，SDK wire 也没有 `session/cancel`、`session/close` 或 prompt cancel；客户端唯一的停止手段是 `close()` 整个 runtime。dsh 内部有真正的 `Agent.cancel()`，但它不在 SDK wire 上。

| 控制方 | 能中断 | 不能中断 |
|---|---|---|
| Python `DeepSeekHarness` | 整个 runtime（`close()`） | 单个 session / turn |
| SDK wire | — | 没有 cancel / close 方法 |
| `Agent.cancel(cause, options)` | 当前 turn（可保留/清空 inbox） | 只中断一个 step；回滚副作用 |
| `interrupt_agent` tool | 子 agent 当前 turn | 从 SDK 直接调用 |
| session-controller `cancel` | 当前 turn（`kind:'user'`, keepInbox） | 不是 SDK wire |

要在 SDK 侧做到 per-turn interrupt，只能自己加一层。最轻的做法是注册一个 `/stop` command，SDK 的 `session/query` 会走 command registry：

```ts
import type { Context } from '@deepseek-ai/cordis'

export const name = 'stop-command'
export const inject = ['commands']

export function apply(ctx: Context) {
  ctx.commands.register({
    name: 'stop',
    description: 'Stop the current turn',
    handler: (invocation) => {
      invocation.agent.cancel({ kind: 'user' }, { keepInbox: true })
      return { kind: 'success', text: 'stop requested' }
    },
  })
}
```

然后 `DeepSeekHarness.start_session(id).run('/stop')` 就会走 `session/query` → command registry → `Agent.cancel()`。这不是 SDK 内置 cancel，而是你把 cancel 包成了一个 command。自定义 JSON-RPC 方法要走 `packages/sdk/server/src/server.ts` 的 dispatch，普通插件加不进去。

从代码看，SDK 把进程当作生命周期边界：session/agent 活到 process shutdown，wire 没有 per-session close。`Agent.cancel()` 是进程内协作式取消，调用立即返回，真正停稳要 `whenIdle()`，而且不能回滚已经发生的 tool 副作用。远程 wire 上回一个“cancel 成功”会掩盖这些竞态。需要真正 cancel 的宿主（Web/session-controller）直接拿 Agent handle 调 `agent.cancel({ kind: 'user' }, { keepInbox: true })`；SDK 作为单客户端 stdio runtime，选择用关闭进程来结束所有工作。

dsh 内部的 interrupt 走 `Agent.cancel(cause, options)`（`packages/core/agent/src/runtime-types.ts:212`）：

```text
caller / plugin / controller
        │ agent.cancel(cause, { keepInbox? })
        ▼
AgentLoop phase.abort.abort(cause)
        │
        ├─ model stream aborted
        │    ├─ 有已交付内容 → assistant/message { interrupted: true, 已交付 prefix }
        │    └─ 无内容       → assistant/attempt { stream }
        │
        ├─ pending tool calls → tool/result（error，结果可能 unknown）
        ├─ step/end
        ├─ turn/end { kind: 'aborted', reason: cause }
        │
        └─ inbox
             ├─ 默认：clear queued / steering
             └─ keepInbox: 保留，下一 turn 继续
```

`cause` 的取值（`packages/core/session/src/types.ts:194`）：

| cause | 来源 | 语义 |
|---|---|---|
| `user` | 用户/宿主的 stop | 停止当前 turn |
| `parent` | 父 agent / subagent 管理 | 祖先中断 |
| `hook` | 插件 hook | 带 `reason` |
| `disposed` | agent/runtime teardown | 进程关闭 |
| `legacy` | 历史持久化导入 | 旧记录没有 cause |

`cancel()` 立即返回；真正停稳要等 `whenIdle()`；同一个 activity 的第一次 cause 生效。

一次 live cancel 的 event 序列：

```text
turn/start
  user/message
  step/start
  assistant/message { interrupted: true }   ← 有已交付内容
  或 assistant/attempt { stream }           ← 无内容
  tool/result { isError: true }             ← 未完成的 tool call
  step/end
  turn/end { reason: { kind: 'aborted', reason: { kind: 'user' } } }
```

取消是协作式的，不是回滚：

```text
interrupt ≠ rollback
  ├─ 已执行的 tool：副作用可能已经发生
  ├─ 已交付的 assistant 文本：保留为 interrupted prefix
  ├─ 未完成的 tool call：记 error result，结果可能 unknown
  └─ session log：只追加，不删除历史
```

`DeepSeekHarness.close()` 走的是 runtime teardown，不是 per-turn interrupt：

```text
DeepSeekHarness.close()
        │ shutdown RPC
        ▼
SDK server
  ├─ abort lifetime
  ├─ close userQuestions（pending question → user-question.cancelled）
  ├─ await pending session creation / command tasks
  ├─ disable query invocation
  ├─ drain descendants
  ├─ dispose goals / agents / llm adapter
  │    └─ agent.cancel({ kind: 'disposed' })
  │         ├─ 当前 turn → turn/end { aborted, reason: { kind: 'disposed' } }
  │         └─ flush session write handle
  └─ exit 0
        │
        ▼
client: pending waiter → TransportClosedError
```

live cancel 和 crash 要分开：

```text
live cancel
  turn/end { kind: 'aborted', reason: { kind: 'user' | 'parent' | 'hook' | 'disposed' } }

crash / kill -9
  没有 turn/end
  resume:
    ├─ tool/result errors
    ├─ step/end
    └─ turn/end { kind: 'interrupted' }
```

Python `Session.run()` 看到的三种结局：

```text
Session.run()
  ├─ in-process cancel，runtime 还活着
  │    └─ root idle → RunResult(finish_reason='aborted')
  ├─ close() / shutdown
  │    └─ TransportClosedError（也可能先看到 idle）
  └─ crash
       └─ TransportClosedError；resume 后看到 turn/end { interrupted }
```

Subagent 的 interrupt 是另一条链路：

```text
parent agent
   │ interrupt_agent(name)
   ▼
ctx.subagents.interrupt(target, { kind: 'ancestor', agent: caller })
   ├─ 目标当前 turn abort
   ├─ 目标自己的 descendants 继续跑
   ├─ local subagent 之后可以 send_message 继续
   └─ external execution 停止后不可继续
```

依据：`packages/sdk/protocol/README.md:141` 明确写了 “No cancel or session-close methods”；`packages/sdk/server/README.md:216` 也写了 SDK agent 活到进程 shutdown。`interrupt_agent` 的定义在 `packages/subagent/tool-subagent-control/src/index.ts:120`。

## 4. DSH 插件机制

先讲结论：框架插件和用户插件不是两套运行时，它们都只是 dsh 的 plugin；通过 profile + patch 组合到同一个 dsh 进程里，然后 Python SDK 用 `profile` + `patches` 启动这个进程。

### 一张总图

```text
用户目录 $DSH_HOME/profiles/user-a/
  ├─ package.json
  ├─ cordis.patch.yml
  └─ plugins/user-audit.js

框架目录 /framework/dsh/
  ├─ framework.patch.yml
  └─ plugins/framework-bridge.js

        │ 组合
        ▼
dsh --profile user-a --patch /framework/dsh/framework.patch.yml
        │
        ▼
dsh runtime
  ├─ user-audit
  └─ framework-bridge
        ▲
        │ stdio JSON-RPC
DeepSeekHarness(profile="user-a", patches=(...))
```

### 插件就是一个模块

用户插件：

```js
// $DSH_HOME/profiles/user-a/plugins/user-audit.js
export const name = 'user-audit'
export const inject = ['sessions']

export function apply(ctx) {
  ctx.on('session/event', (session, event) => {
    // 用户自己的逻辑
  })
}
```

框架插件：

```js
// /framework/dsh/plugins/framework-bridge.js
export const name = 'framework-bridge'
export const inject = ['sessions']

export function apply(ctx) {
  ctx.on('session/event', (session, event) => {
    // 转给框架
  })
}
```

插件代码放哪都行，关键是 patch 里要有一行指向它。

### 用户插件怎么配

```text
$DSH_HOME/profiles/user-a/
├─ package.json
├─ cordis.patch.yml
└─ plugins/
   └─ user-audit.js
```

`package.json` 只声明这个 profile 用哪些 bundle：

```json
{
  "name": "dsh-profile-user-a",
  "private": true,
  "dsh": {
    "profile": {
      "bundles": [
        "@deepseek-ai/dsh-base",
        "@deepseek-ai/dsh-sdk-app"
      ]
    }
  }
}
```

`cordis.patch.yml` 插入用户插件：

```yaml
# $DSH_HOME/profiles/user-a/cordis.patch.yml
- insert:
    - id: user-audit
      name: './plugins/user-audit.js'
```

`./plugins/user-audit.js` 是相对这个 patch 文件解析的，所以用户插件留在用户目录。

这个目录里其实不止这两个文件：

```text
$DSH_HOME/profiles/user-a/
├─ package.json          # profile manifest + dependencies
├─ cordis.patch.yml      # 用户 patch 层
├─ cordis.yml            # 空 root，启动时生成
├─ pnpm-workspace.yaml   # pnpm 设置
├─ pnpm-lock.yaml        # dsh plugin add 后生成
├─ compatibility.json    # 可选：插件版本豁免
├─ node_modules/         # dsh plugin add 安装的包
└─ plugins/
   └─ user-audit.js
```

| 文件 | 作用 | 谁维护 |
|---|---|---|
| `package.json` | `dsh.profile.bundles` 有序列表 + `dependencies` | `dsh plugin` 维护 |
| `cordis.patch.yml` | 用户自己的 patch 层 | 用户 |
| `cordis.yml` | 空 root entry list `[]`，所有 patch 从这里开始叠 | 启动器生成，不要手改 |
| `pnpm-workspace.yaml` | `nodeLinker: hoisted`、`autoInstallPeers: false` | 初始化时生成 |
| `pnpm-lock.yaml` | pnpm 依赖锁 | pnpm |
| `compatibility.json` | 精确版本兼容豁免（可选） | Plugin Manager / 手工 |
| `node_modules/` | out-of-tree bundle / plugin 包 | pnpm |
| `plugins/` | 用户自己的插件代码（可选） | 用户 |

`package.json` 初始化时大概是这样：

```json
{
  "name": "dsh-profile-user-a",
  "private": true,
  "dependencies": {},
  "dsh": {
    "profile": {
      "bundles": [
        "@deepseek-ai/dsh-base",
        "@deepseek-ai/dsh-sdk-app"
      ]
    }
  }
}
```

`dependencies` 由 `dsh plugin --profile user-a add <package>` 维护；bundle 包会自动进 `dsh.profile.bundles`，普通依赖只留在 `dependencies`。

`cordis.patch.yml` 初始化时只有：

```yaml
# Your patch layer for this dsh profile...
[]
```

`cordis.yml` 是启动器写的空 root，不要编辑；用户只改 `cordis.patch.yml`。

### 框架插件怎么配

```text
/framework/dsh/
├─ framework.patch.yml
└─ plugins/
   └─ framework-bridge.js
```

```yaml
# /framework/dsh/framework.patch.yml
- insert:
    - id: framework-bridge
      name: './plugins/framework-bridge.js'
      config:
        endpoint: 'http://127.0.0.1:8000'
```

`./plugins/framework-bridge.js` 相对 `framework.patch.yml` 解析，所以框架插件留在框架目录，用户的 profile 不用改。

### 启动时怎么组合

```sh
dsh --profile user-a --patch /framework/dsh/framework.patch.yml
```

```text
bundles: base → sdk-app
  │
  ├─ user-a/cordis.patch.yml       → user-audit
  ├─ $DSH_HOME/cordis.patch.yml    → 无
  └─ --patch framework.patch.yml   → framework-bridge
  ▼
runtime plugins
  ├─ user-audit
  └─ framework-bridge
```

规则很简单：**后面的层覆盖前面的同名 row**。框架用 `--patch`，就是最后一层。

### Python SDK 怎么启动

```python
from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig

config = DeepSeekHarnessConfig(
    dsh_home="/data/dsh-home",
    profile="user-a",
    patches=("/framework/dsh/framework.patch.yml",),
    cwd="/workspace/user-a",
)

with DeepSeekHarness(config) as harness:
    session = harness.start_session("user-a-main")
    print(session.run("hello").final_response)
```

对应关系：

```text
DeepSeekHarnessConfig(
    profile="user-a",
    patches=("/framework/dsh/framework.patch.yml",),
)
        │
        ▼
dsh --profile user-a --patch /framework/dsh/framework.patch.yml
        │
        ▼
runtime plugins: user-audit + framework-bridge
        │
        ▼
这个 runtime 里的所有 session 共享同一套插件
```

- `dsh_home` 或环境变量 `DSH_HOME` 至少要有一个；建议显式传。
- `profile="user-a"` 对应 `$DSH_HOME/profiles/user-a`。
- `patches` 可以传多个，按顺序展开成重复的 `--patch`。
- `start_session()` 本身不创建 Agent；第一次 `run()` 才懒创建（第 2 章）。
- 自定义 profile 不存在时不会自动建；先用 `dsh plugin --profile user-a add <package>` 初始化，或用 `--from-default-profile` 复制模板。

### DSH_HOME 是什么

`dsh_home` 是 dsh 的数据根目录。Python SDK 把它设成子进程的 `DSH_HOME`。

```text
$DSH_HOME/
├─ cordis.patch.yml     # home-level patch，所有 profile 共享
├─ profiles/<name>/     # profile 插件组合
├─ sessions/            # session 的 JSONL.zstd
├─ storages/            # 插件 storage 根目录
├─ cache/               # 附件、模型等缓存
├─ logs/                # startup 诊断日志
├─ .credentials.yaml    # 凭据
└─ .anonymous-user-id   # 匿名身份
```

- 换 `dsh_home` 就换了一整套 profile、session、credentials。
- 同一个 `dsh_home` 的多个 runtime 共享这些数据；不同租户/测试要隔离就传不同目录。
- Python SDK 不会默认用 `~/.dsh`；`dsh_home` 或环境变量 `DSH_HOME` 至少要有一个。

### 如果用户要能覆盖框架默认值

把框架做成 bundle，而不是 `--patch`。

```text
/framework/dsh-bundle/
├─ package.json
├─ cordis.patch.yml
└─ plugins/framework-bridge.js
```

```json
{
  "name": "dsh-framework-bundle",
  "version": "1.0.0",
  "dsh": {
    "bundle": {
      "patch": "./cordis.patch.yml"
    }
  }
}
```

安装到用户 profile：

```sh
dsh plugin --profile user-a add /framework/dsh-bundle
```

SDK 启动就不需要 `patches` 了：

```python
config = DeepSeekHarnessConfig(
    dsh_home="/data/dsh-home",
    profile="user-a",
)
```

两种方式的区别：

```text
--patch:
  bundles → user profile patch → framework patch
                                      ▲
                                  框架最后，框架赢

bundle:
  bundles（base → sdk-app → framework-bundle）→ user profile patch
                                                    ▲
                                                用户最后，用户赢
```

| 框架形态 | 用户能否覆盖 | SDK 启动 |
|---|---|---|
| framework bundle | 能，用户 profile patch 在后面 | `profile="user-a"` |
| framework `--patch` | 不能，框架 patch 在最后 | `profile="user-a", patches=(...)` |

### 一个 runtime 一套插件

```text
runtime A: profile user-a + framework patch
  ├─ user-audit
  └─ framework-bridge

runtime B: profile user-b + framework patch
  ├─ user-b-audit
  └─ framework-bridge
```

- 一个 dsh 进程只能 boot 一个 profile。
- 同一个 runtime 里的所有 session 共享同一套插件。
- 不同用户需要不同用户插件，就起多个 runtime，各自带自己的 profile。

### 只需要记住

| 问题 | 答案 |
|---|---|
| 插件怎么加载 | patch 里 `insert` 一行，指向插件模块 |
| 用户插件放哪 | 用户 profile 目录，`cordis.patch.yml` 自己维护 |
| 框架插件放哪 | 框架目录，`--patch` 注入 |
| 框架不改用户文件 | `patches=("/framework/dsh/framework.patch.yml",)` |
| 用户要能覆盖框架 | 框架做成 bundle，`dsh plugin add` |
| 多个 `--patch` | 可以，重复写，后面的赢 |
| 多个 bundle | 可以，`dsh.profile.bundles` 有序列表 |
| 一个进程几个 profile | 一个 |
| 插件目录自动扫描 | 不会，必须有 patch row |

## 5. DSH 传入自定义信息

结论：**标准 DSH SDK 没有通用 metadata 字段。** `initialize`、`session/prompt`、`session/query` 都只接受固定字段。plugin 能直接拿到的自定义信息只有三类：进程环境、sessionId，以及 session header/cwd。想要真正的 per-session metadata，要么编码进 sessionId，要么在框架侧按 sessionId 存一份 side store，要么改 SDK server。

### 参数面

```text
initialize(cwd, provider, model, reasoningEffort?, maxTokens?,
           approvalRequests?, capabilities.userQuestions?)
  └─ runtime 级

session/query(sessionId, contentBlocks)
  └─ session 级：text + encoded image

session/prompt(sessionId, contentBlocks)
  └─ session 级：text + encoded image

session/resume(sessionId)
session/wait(sessionId)
session/working-directory/get|set(sessionId, path?)
```

| 调用 | 能传的字段 | 作用域 |
|---|---|---|
| `initialize` | cwd、provider、model、effort、maxTokens、approvals、userQuestions | runtime |
| `session/query` / `session/prompt` | sessionId、text/image blocks | session |
| `session/resume` / `session/wait` | sessionId | session |
| `working-directory` | sessionId、path | session |

没有 `metadata`、`user_id`、`tenant_id`、`trace_id` 这类字段。`session/query` 会拒绝除 `sessionId` / `contentBlocks` 之外的 key；`initialize` 的额外字段即使发过去，service 也只读已知字段，不会转给 plugin。

注意 `DeepSeekHarnessConfig.cwd` 和 `runtime_cwd` 不是一回事：前者作为 `initialize.cwd` 写进 `session.header.cwd`，后者只改 dsh 子进程的工作目录。

### 通道 A：环境变量 / profile（runtime 级）

```python
from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig

config = DeepSeekHarnessConfig(
    dsh_home="/data/dsh-home",
    profile="user-a",
    env={
        "TENANT_ID": "acme",
        "FRAMEWORK_ENDPOINT": "http://127.0.0.1:8000",
    },
)

with DeepSeekHarness(config) as harness:
    harness.start_session("main").run("hello")
```

plugin 里：

```js
export function apply(ctx) {
  const tenant = process.env.TENANT_ID
  const endpoint = process.env.FRAMEWORK_ENDPOINT
  // 注册工具、监听 session/event，或把值放进 plugin 自己的状态
}
```

```text
Python SDK                           dsh runtime
  │                                      │
  │  env { TENANT_ID: "acme" }           │
  ├─────────────────────────────────────►│ process.env.TENANT_ID
  │                                      │
  │  profile / patches                   │
  ├─────────────────────────────────────►│ plugin Config
  │                                      │
  │  cwd                                 │
  ├─────────────────────────────────────►│ session.header.cwd
```

- 作用域是 **runtime**：这个 dsh 进程里所有 session 共享。
- 适合 tenant、框架 endpoint、部署环境这类进程级配置。
- 需要 per-session 不同值时，要么拆多个 runtime，要么用下面的 session 级通道。

### 通道 B：把信息编码进 sessionId（session 级）

```python
session_id = f"{tenant}:{user}:{conversation}"
session = harness.start_session(session_id)
session.run("hello")
```

plugin 里：

```js
ctx.on('session/event', (session, event) => {
  const [tenant, user, conversation] = session.id.split(':')
  // 用这些值做路由、审计、持久化命名
})
```

```text
sessionId = "acme:alice:conv-42"
                │
                ├─ tenant       = "acme"
                ├─ user         = "alice"
                └─ conversation = "conv-42"
```

- 作用域是 **session**：每个 session 可以不一样。
- `SessionId` 在 dsh 里是不解析语义的字符串；plugin 自己约定格式。
- 它不是授权边界：用户可以随便传一个 id 冒充别人。**不要拿 sessionId 做鉴权**。
- 持久化时 JSONL backend 会对 id 做转义；非安全字符会膨胀成 `~XXXX`，长度别太夸张。

### 通道 C：框架侧 side store（session 级）

metadata 不经过 SDK，框架自己按 sessionId 存一份，plugin 用 `session.id` 去查。

```text
┌──────────────────┐
│  tRPC framework  │
│  metadata store  │
│  (Redis/MySQL)   │
└────────┬─────────┘
         │ 1. set(sessionId, metadata)
         │ 2. start_session(sessionId)
         ▼
┌──────────────────┐
│   dsh runtime    │
│   plugin         │
│   get(session.id)│
└──────────────────┘
```

框架侧：

```python
await metadata_store.set(
    "acme:alice:conv-42",
    {"tenant": "acme", "user": "alice", "trace_id": "abc-123"},
)
session = harness.start_session("acme:alice:conv-42")
```

plugin 侧：

```js
async function metaOf(session) {
  return metadataStore.get(session.id)
}

ctx.on('session/event', (session, event) => {
  // 需要时 await metaOf(session)；建议按 sessionId 缓存
})
```

- 作用域是 **session**，而且可以跨 `session/resume`、跨进程保留。
- metadata 本身不经过 SDK wire，所以不需要改 protocol；只需要框架和 plugin 都能访问同一个 store。
- 适合 Redis、MySQL、PostgreSQL 这类已有存储；plugin 的 endpoint 通过 profile patch 或 env 注入。

### 通道 D：改 SDK server（需要 fork/patch）

如果一定要在标准 SDK 调用里带一个真正的 `metadata` 字段，只能改 server。两条路：

```text
路线 1：加 JSON-RPC 方法
  Python client ── session/metadata/set ──► server patch ──► plugin 可读的 store/event

路线 2：让自定义命令可路由
  Python client ── session/query("/meta ...") ──► query-input patch ──► command handler
```

但要注意：**标准 SDK 的 `session/query` 不会路由自定义命令。** `queryCommand()` 只路由四个内置命令：

```ts
// packages/sdk/server/src/query-input.ts
if (builtin === 'compact' && separator === -1) return `/${descriptor.name}`
if (builtin === 'permission' && separator !== -1) return `/${descriptor.name}${rawInput}`
if (builtin === 'plan' || builtin === 'goal') return `/${descriptor.name}${rawInput}`
return undefined   // 其他命令原样当 prompt
```

所以框架注册一个 `/meta` command 之后，SDK 发 `session/query("/meta {...}")` 并不会执行 handler，而是把它当成普通用户消息送给模型。要走 command 路线，得 patch `query-input.ts`，让目标命令也返回一行；或者直接在 `server.ts` 的 `handleRequest` 里加自定义方法，同时改 protocol 类型和 Python 客户端。这不是标准 wire，维护成本要算进去。

### 不可行 / 不推荐

- **`session/query` 加 `metadata` 字段**：server 只认 `sessionId` 和 `contentBlocks`，其余 key 会被拒绝。
- **`initialize` 加自定义字段**：即使 JSON-RPC 不报错，service 也只读已知字段，plugin 拿不到。
- **自定义 `/meta` command 走标准 SDK**：`queryCommand` 只认 compact/permission/plan/goal，其他命令会被当成 prompt 送给模型。
- **把 metadata 拼进 prompt 文本**：默认模型会看到，而且会污染对话历史；除非你接受这个代价，或者再写 message projection 去剥离，成本都不低。
- **拿 sessionId 做鉴权**：id 是调用方自己传的，可以伪造。

### 选型

| 需求 | 推荐通道 | 作用域 |
|---|---|---|
| tenant / endpoint / 部署配置 | `DeepSeekHarnessConfig.env` 或 profile patch | runtime |
| 每个 session 的业务标签 | 编码进 sessionId | session |
| 跨重启保留、字段较多 | 框架 side store，按 sessionId 关联 | session |
| 一定要走 SDK wire 的 metadata | patch SDK server | session |
| 每条消息的 traceId | 标准 SDK 没有；只能 prompt 文本或 patch server | message/turn |
| 安全/鉴权 | 不要依赖 metadata；用独立 runtime + 外部 auth | runtime |

## 6. DSH 定制

用户拿到 Python SDK 后，先要有一个能跑的 profile 目录，SDK 才能拉起 runtime。这一章按“初始化 → 加插件 → 开发插件 → 加第三方依赖 → SDK 拉起”的顺序讲。

### 第一步：初始化 profile

只使用默认 `sdk`、不改配置时，SDK 首次启动会自动生成 profile，不需要手动初始化：

```python
DeepSeekHarness(profile="sdk", dsh_home="/data/dsh-home")
# 首次 start() 时生成 /data/dsh-home/profiles/sdk/
```

要定制的话，先显式初始化，再改文件：

```sh
export DSH_HOME=/data/dsh-home
dsh --profile sdk --dump-default-config >/dev/null
```

生成：

```text
$DSH_HOME/profiles/sdk/
├── package.json          dsh.profile.bundles = [dsh-base, dsh-sdk-app]
├── cordis.yml            空根入口，不要改
├── cordis.patch.yml      用户 patch 层，日常改这个
└── pnpm-workspace.yaml   pnpm 配置，一般不动
```

这一步只写配置，不装包。内置插件都在 runtime exe 里，启动时从安装包解析，不需要进 profile：

```text
runtime exe（302 MB）
  └── /snapshot/node/node_modules/
        ├── @deepseek-ai/dsh-base
        ├── @deepseek-ai/dsh-sdk-app
        ├── @deepseek-ai/dsh-session
        ├── @deepseek-ai/dsh-tool-fs
        └── ...

$DSH_HOME/profiles/sdk/
  └── package.json 只记录 bundle 名字，不复制这些包
```

自定义 profile 用模板创建：

```sh
dsh --profile user-a --from-default-profile sdk --dump-default-config >/dev/null
```

> `--from-default-profile` 是破坏性重置。目标已存在时，`cordis.patch.yml` 和 `node_modules` 都会被删掉。目标不能是内置名字（`sdk`、`sdk-minimal` 等）。

### 第二步：分清三类“包”

```text
① dsh 内置插件
   位置：runtime exe 内部
   使用：不用装，profile 写 bundle 名字或 patch 行即可

② 外部 dsh 插件（官方 / 社区 / 自研）
   位置：profiles/<name>/node_modules/
   安装：dsh plugin add
   挂载：普通插件要 patch insert；bundle 自动进 dsh.profile.bundles

③ 第三方依赖（不是 dsh 插件）
   位置：profiles/<name>/node_modules/
   安装：dsh plugin add
   用途：只给你的插件 import，不会被 dsh 挂载
```

三类的安装和挂载方式不同，下面分开讲。

### 第三步：引入 dsh 已有插件

**内置插件**已经随 runtime 提供，不需要安装。想知道有哪些，直接看组合结果：

```sh
dsh --profile sdk --dump-default-config
```

**外部插件包**用 `dsh plugin add` 安装：

```sh
dsh plugin --profile sdk add @acme/dsh-tool-tenant
```

装完在 `cordis.patch.yml` 里挂载：

```yaml
- insert:
    - id: tenant-tool
      name: '@acme/dsh-tool-tenant'
```

**外部 bundle** 也用它安装，但不需要手写 patch 行：

```sh
dsh plugin --profile sdk add @acme/dsh-team-bundle
```

因为 bundle 声明了 `dsh.bundle.patch`，安装器会自动把它追加到 `dsh.profile.bundles`，它的 patch 层自动生效。

安装后的目录：

```text
$DSH_HOME/profiles/sdk/
├── package.json          dependencies 多了 @acme/...
├── cordis.patch.yml
├── pnpm-lock.yaml        ← 新增
├── node_modules/         ← 新增
│   └── @acme/dsh-tool-tenant/
└── pnpm-workspace.yaml
```

### 第四步：开发自己的插件

**本地文件插件**，最快，适合开发和调试：

```text
$DSH_HOME/profiles/sdk/
├── cordis.patch.yml
└── plugins/
    └── hello-tool.js
```

```js
import { defineTool } from '@deepseek-ai/dsh-tools'

export const name = 'hello-tool'
export const inject = ['tools']

export function apply(ctx) {
  ctx.tools.register(defineTool({
    name: 'hello',
    description: 'Say hello to someone',
    parameters: {
      who: { type: 'string', required: true },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          message: { type: 'string', required: true },
        },
      },
      render: (_args, value) => [{ type: 'text', text: value.message }],
    },
    async execute(args) {
      return { message: `Hello, ${args.who}!` }
    },
  }))
}
```

```yaml
# $DSH_HOME/profiles/sdk/cordis.patch.yml
- insert:
    - id: hello-tool
      name: './plugins/hello-tool.js'
```

`./plugins/hello-tool.js` 相对 `cordis.patch.yml` 解析。不需要 `dsh plugin add`，也不需要 `node_modules`。

插件里 `import '@deepseek-ai/dsh-tools'` 能解析，是因为 runtime resolution 从 exe 提供内置包。

验证：

```sh
dsh --profile sdk --dump-config | grep -A2 hello-tool
```

**npm 包插件**，适合复用和分发：

```text
@acme/dsh-tool-tenant/
├── package.json
├── src/index.ts
└── lib/index.js
```

```json
{
  "name": "@acme/dsh-tool-tenant",
  "version": "1.0.0",
  "type": "module",
  "main": "lib/index.js",
  "peerDependencies": {
    "@deepseek-ai/cordis": "*",
    "@deepseek-ai/dsh-tools": "*"
  },
  "devDependencies": {
    "@deepseek-ai/cordis": "*",
    "@deepseek-ai/dsh-tools": "*"
  }
}
```

安装：

```sh
dsh plugin --profile sdk add file:/abs/path/to/dsh-tool-tenant
# 已发布到 registry 时
dsh plugin --profile sdk add @acme/dsh-tool-tenant
```

普通插件包还要在 `cordis.patch.yml` 里加一行：

```yaml
- insert:
    - id: tenant-tool
      name: '@acme/dsh-tool-tenant'
```

**bundle** 是“插件 + 默认配置”，包声明了 `dsh.bundle.patch`，安装后自动生效，不需要手写 patch 行。

### 第五步：引入非 dsh 插件的依赖

插件里要用 `axios`、`zod`、`lodash` 这类普通 Node.js 包，直接用 `dsh plugin add` 装到 profile：

```sh
dsh plugin --profile sdk add axios
```

```js
// profiles/sdk/plugins/weather-tool.js
import axios from 'axios'
import { defineTool } from '@deepseek-ai/dsh-tools'
```

安装后：

```text
$DSH_HOME/profiles/sdk/
├── package.json          dependencies 多了 axios
├── pnpm-lock.yaml
└── node_modules/
    ├── axios/            ← 你的插件可以 import
    └── ...
```

如果插件本身是 npm 包，把第三方依赖写进它的 `dependencies`，安装插件时 pnpm 会递归安装：

```json
{
  "name": "@acme/dsh-tool-weather",
  "dependencies": {
    "axios": "^1.7.0"
  },
  "peerDependencies": {
    "@deepseek-ai/cordis": "*",
    "@deepseek-ai/dsh-tools": "*"
  }
}
```

依赖放在哪个字段：

| 依赖类型 | 放哪 | 谁提供 |
|---|---|---|
| 第三方库（`axios`、`zod`） | `dependencies` | pnpm 装到 profile 的 `node_modules` |
| dsh 包（`@deepseek-ai/dsh-tools`） | `peerDependencies` | runtime exe 的 runtime resolution |
| dsh 包的编译/类型 | `devDependencies` | 本地开发时装，运行时不使用 |

不要把 `@deepseek-ai/*` 放进 `dependencies`，否则 profile 里会装第二份，和 exe 里的实例冲突。

### 第六步：通过 SDK 拉起

```python
from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig

config = DeepSeekHarnessConfig(
    dsh_home="/data/dsh-home",
    profile="sdk",
    patches=("/data/overrides/tenant-a.yml",),
    cwd="/workspace/user-a",
)

with DeepSeekHarness(config) as harness:
    session = harness.start_session("user-a-main")
    print(session.run("hello").final_response)
```

对应命令行：

```text
dsh --profile sdk --patch /data/overrides/tenant-a.yml
```

- `profile` 原样传给 dsh CLI；不传时默认 `"sdk"`。
- `patches` 会转成绝对路径，按顺序展开成多个 `--patch`。
- `patches` 是调用期 overlay，只影响这一次 runtime，不改 profile 文件。
- 自定义 profile 必须先存在；SDK 不会自动创建非内置 profile。

### 组合顺序

```text
空根
  → profile 的 bundles（base → sdk-app → ...）
  → profiles/user-a/cordis.patch.yml       用户持久 patch
  → $DSH_HOME/cordis.patch.yml             home 级 patch
  → --patch（SDK 的 patches 参数）          调用期 overlay
  ▼
plugin tree
```

后面的层覆盖前面的同名 row。

### 目录全貌

```text
$DSH_HOME/profiles/sdk/
├── package.json          bundles + dependencies        dsh plugin / 用户
├── cordis.yml            空根入口                      启动器生成
├── cordis.patch.yml      用户 patch 层                 用户
├── pnpm-workspace.yaml   hoisted / autoInstallPeers   初始化器
├── pnpm-lock.yaml        依赖锁                         pnpm
├── node_modules/         外部插件和第三方依赖            pnpm
├── .plugin-manager/      plugin manager 日志/状态       plugin manager
├── plugins/              本地插件代码（可选）            用户
└── compatibility.json    版本兼容豁免（可选）            plugin manager / 手工
```

初始化时只有前四个文件。装了外部包才会出现 `pnpm-lock.yaml`、`node_modules/`、`.plugin-manager/`。`plugins/` 是用户自己建的，不是初始化文件。

### `$DSH_HOME` 下哪些目录会自动创建

`initProfile` 只创建 profile 目录。home 下的其他目录要么按需创建，要么由用户手动创建：

| 路径 | 谁创建 | 何时创建 | 不存在时 |
|---|---|---|---|
| `profiles/<name>/` | `initProfile` | 首次启动或 `--from-default-profile` | 自动初始化 |
| `skills/` | 用户 | 手动建目录 | 没有用户技能，启动正常 |
| `attachments/v1/` | `attachment-local` | 第一次存附件 | 不影响启动 |
| `.credentials.yaml` | `credentials-local` | 第一次写凭据 | 视为无凭据 |
| `cache/` | 各插件 | 第一次用缓存 | 不影响启动 |
| session 存储目录 | JSONL 后端 | 第一次持久化 session | 不影响启动 |
| `storages/` | storage 插件 | 第一次写 storage | 不影响启动 |
| `logs/` | 启动器 | 第一次写日志 | 不影响启动 |
| `cordis.patch.yml` | 用户 | 手动 | 跳过 home patch 层 |

全新的 `dsh_home` 启动后，目录大概只有：

```text
$DSH_HOME/
└── profiles/
    └── sdk/
        ├── package.json
        ├── cordis.yml
        ├── cordis.patch.yml
        └── pnpm-workspace.yaml
```

其他目录用到才出现。唯一需要手动建的是 `skills/`：

```sh
mkdir -p "$DSH_HOME/skills/my-skill"
cat > "$DSH_HOME/skills/my-skill/SKILL.md" <<'EOF'
---
name: my-skill
description: ...
---
...
EOF
```

项目级技能放在 `<cwd>/.dsh/skills/`，不需要预先创建 `$DSH_HOME/skills`。

### 常见问题

| 问题 | 说明 |
|---|---|
| SDK 不传 `profile` | 默认 `sdk` |
| 自定义 profile 不存在 | SDK 启动报错，先 `--from-default-profile` 创建 |
| 只用 `dsh plugin add` 创建 profile | 默认只挂 `dsh-base`，没有 `dsh-sdk-app`，SDK 拉不起来 |
| `--from-default-profile` | 破坏性重置，会删掉 `cordis.patch.yml` 和 `node_modules` |
| `--from-default-profile` 目标 | 不能是内置名字（`sdk`、`sdk-minimal` 等） |
| 装了插件但没生效 | 普通插件要 patch insert；只有 bundle 自动挂载 |
| 第三方包不是 dsh 插件 | 正常，`dsh plugin add` 也能装，只是不会被挂载 |
| `patches` 相对路径 | 按当前进程 cwd 解析，建议绝对路径 |
| 一个 runtime 几个 profile | 一个 |
| 同一 `dsh_home` 多 profile | `skills/`、`attachments/`、`.credentials.yaml` 共享，profile 目录独立 |
