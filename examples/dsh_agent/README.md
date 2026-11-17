# DSH Agent 示例

这个示例把 DeepSeek Harness（DSH）作为 agent runtime，通过 tRPC-Agent 的 `Runner` 调用。
`DSHAgent` 负责把一轮 tRPC invocation 转成 `session/trpc-prompt`，把 DSH 的
`session.assistant.delta` / `session.event` 转成 tRPC Event，并把会话 JSONL 存回
`SessionService.state`。

## 关键特性

- DSH 子进程由 `deepseek_harness.HarnessClient` 启动，Python 侧不手写 stdio transport。
- 一个 notification 分发线程把 `HarnessClient.next_notification()` 桥接到 asyncio 队列。
- 首轮用空 JSONL 调 `session/trpc-prompt`，JS 插件走 `ctx.agents.create()`，由 DSH 构造
  session header；后续轮把 `SessionService.state` 里的 JSONL 快照 seed 回内存 backend，
  再走 `ctx.agents.resume()`。
- 每轮结束后用 `session/trpc-snapshot` 对账，保证客户端 JSONL 和 DSH 会话日志一致。
- 同一个 tRPC session 跑两轮：第一轮让 agent 写 `hello.txt`，第二轮读回文件里的数字，
  同时验证 JSONL 跨轮恢复和 workspace 写入。
- 业务 `RunConfig.custom_data` 会原样进入 `session/trpc-prompt.meta`，用户 JS 插件可通过
  `ctx.get('trpcRequestContext')?.get(sessionId)?.meta` 读取。
- `custom_dsh_input` 回调可以在每轮开始前调整 `DSHRunInput`：给会话单独建目录、
  换 `jsonl`、换模型；text / inline image / inline file 已经由框架转成 DSH block。
- workspace 按 `dsh-workspace/<user_id>` 隔离：同一用户的多个 session 共享目录，
  不同用户各写各的目录。

## 目录

```text
examples/dsh_agent/
├── .env                         # 网关配置，已提供
├── agent/
│   ├── agent.py                 # create_agent() / root_agent / custom_dsh_input
│   └── config.py                # 读 TRPC_AGENT_*，构造 DSHConfig
├── run_agent.py                 # 两轮对话入口
├── dsh-home/                    # DSH runtime home（.gitignore）
│   └── profiles/
│       └── custom/              # 用户可自由定制的 DSH profile
│           ├── package.json     # bundles: dsh-base + dsh-sdk-app
│           ├── cordis.patch.yml # 业务自己的 DSH patch
│           └── ...
└── dsh-workspace/               # 每个 user 的 DSH cwd（.gitignore）
```

`agent/config.py` 里 `DSHConfig(dsh_home=..., profile="custom")` 会定位到
`dsh-home/profiles/custom/`。`profile` 只是 profile 名字，不要写成路径。

框架会在 profile 目录下临时生成 `.trpc-agent-*/plugins/`，放入三个 JS 插件：

```text
trpc-session-backend.mjs   内存 SessionPersistence
trpc-request-context.mjs   per-run custom_data side store
trpc-jsonrpc-server.mjs    session/trpc-prompt + delta/cancel/status
```

DSH 启动时会禁用官方的 `sdk-jsonrpc-server` 和 `session-persistence-jsonl`，改用这三个插件。
进程退出后临时目录会被清理。

`custom` 是给业务改的模板：要加 DSH 插件、调整 sandbox / approval，
直接改 `cordis.patch.yml` 或 `package.json` 即可。`DSHConfig.model` 的 provider 是
`openai` / `anthropic` 时，框架会生成对应 route，不需要写在这里；其他 provider
或想自己控制 route 时，在 profile 里配置 `llm-pi-ai`。

## 环境要求

- Python 3.10+
- 项目根目录的 `venv` 已经 `pip install -e .`，并且安装了 DSH SDK：

```sh
venv/bin/python -c "from deepseek_harness import HarnessClient"
```

如果缺少 DSH SDK：

```sh
venv/bin/pip install "deepseek-harness-sdk==0.2.1rc2"
```

示例默认使用 `deepseek-harness-sdk` 自带的 runtime（`DSHConfig.dsh_bin=None`），
不需要系统安装 `dsh`，也不需要 `examples/dsh_agent/.venv`。如果要换成别的 `dsh` 可执行文件，
在 `agent/config.py` 里给 `DSHConfig` 传 `dsh_bin="/path/to/dsh"` 即可；也支持传 `PATH` 里的命令名。

## 运行

```sh
cd examples/dsh_agent
/workspace/work/trpc-agent-python/venv/bin/python run_agent.py
```

或者先激活项目 venv：

```sh
source /workspace/work/trpc-agent-python/venv/bin/activate
cd examples/dsh_agent
python run_agent.py
```

### 取消示例

`run_agent_with_cancel.py` 会发一个长文请求，收到第一段 partial 文本后调用
`runner.cancel_run_async()`，验证：

- `cancel_run_async` 返回 `True`；
- 收到 `AgentCancelledEvent`；
- 取消时已经有 JSONL 事件写回 `SessionService`；
- 同一个 session 在取消后还能继续下一轮。

```sh
cd examples/dsh_agent
/workspace/work/trpc-agent-python/venv/bin/python run_agent_with_cancel.py
```

`.env` 里的变量：

```dotenv
TRPC_AGENT_API_KEY=...
TRPC_AGENT_BASE_URL=http://03-llm.woa.com/gateway/v1/individual
TRPC_AGENT_MODEL_NAME=deepseek-v4-flash
```

`agent/config.py` 把这些值放进 `DSHConfig.model`：

```python
DSHConfig(
    profile="custom",
    dsh_home="dsh-home",
    model=DSHModel(
        provider="openai",
        model=model_name,
        base_url=url,
        api_key=api_key,
    ),
)
```

框架会从 `DSHConfig.model` 生成 `llm-pi-ai` 的 `openai` route，并把 `base_url` /
`api_key` 注入 DSH 子进程的 `DEEPSEEK_BASE_URL` / `DEEPSEEK_API_KEY`。换成
Anthropic 只需把 `provider` 改成 `"anthropic"`，框架会生成 `anthropic-messages`
route。`DSHConfig.model=None` 时由 DSH profile 决定模型；单轮要换模型可以在
`custom_dsh_input` 里改 `run_input.model`。

## 单轮定制：custom_dsh_input

框架会先把 `Content` 里的内容转成 DSH block，再调用 `custom_dsh_input`：

| Content | 转换结果 |
|---|---|
| `Part.text` | `{"type": "text", "text": "..."}` |
| `Part.inline_data` + image mime | `{"type": "image", "data": "base64", "mimeType": "image/png"}` |
| `Part.inline_data` + 其他 mime | `{"type": "file", "data": "base64"}` |
| `Part.file_data`（URI） | 不自动读，callback 自己处理 |

image/file 的 base64 block 会在 JS 插件里通过 DSH attachment store admission，变成 durable 的
`attachment` block；业务侧不用自己存文件。

默认 `model` 来自 `DSHConfig.model`；callback 主要用来改 cwd、单轮 model、jsonl、meta：

```python
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.server.agents.dsh import DSHRunInput


async def custom_dsh_input(
    ctx: InvocationContext,
    run_input: DSHRunInput,
) -> DSHRunInput:
    cwd = WORKSPACE_ROOT / ctx.session.id
    cwd.mkdir(parents=True, exist_ok=True)
    run_input.cwd = str(cwd)
    return run_input
```

callback 必须返回一个 `DSHRunInput`；即使只改字段，也要 `return run_input`。

callback 拿到 `run_input` 时，`jsonl` / `version` 已经从 `SessionService` 填好：
首轮 `jsonl` 是空字符串，框架把 `cwd` 一起发给 JS 插件，由 DSH 的
`ctx.agents.create()` 构造 session header；后续轮 `jsonl` 是上一轮存下来的快照，
`cwd` 不再参与。`cwd` 必须是绝对路径；DSH 会把它写进 session header。
业务想完全接管会话状态，可以直接给 `run_input.jsonl` 赋值（header 的 `id` 要和
`session_id` 一致），框架会按新 JSONL 的 header 对齐 `version` 和本地 state。

`file_data` 是 URI，不是字节；框架不自动读。需要它时，在 callback 里从
`ctx.user_content` 读出 `file_uri`，自己构造 block 后写回 `run_input.content_blocks`。

如果业务手里已经有 durable attachment，也可以直接 append JSON：

```python
run_input.content_blocks.append({
    "type": "image",
    "attachment": {
        "attachmentId": "sha256:...",
        "mediaType": "image/png",
        "bytes": 1234,
        "width": 64,
        "height": 64,
    },
})
```

JS 插件看到 `attachment` 会直接透传，不再 admission。

## 预期输出

```text
===== user: alice =====
🆔 Session ID: xxxxxxxx...
📝 User: 请在当前工作目录下创建 hello.txt，文件内容只写 42。完成后只回复 done。
🤖 Assistant:
🔧 [Tool Call: write({'file_path': '.../dsh-workspace/alice/hello.txt', 'content': '42'})]
📊 [Tool Result: ...]
done

📝 User: 读取 hello.txt，只回答文件里的数字。
🤖 Assistant:
🔧 [Tool Call: read({'file_path': '.../dsh-workspace/alice/hello.txt'})]
📊 [Tool Result: ...]
42

📁 User workspace: .../dsh-workspace/alice
✅ hello.txt = 42

===== user: bob =====
🆔 Session ID: yyyyyyyy...
📝 User: 请在当前工作目录下创建 hello.txt，文件内容只写 7。完成后只回复 done。
🤖 Assistant:
🔧 [Tool Call: write({'file_path': '.../dsh-workspace/bob/hello.txt', 'content': '7'})]
📊 [Tool Result: ...]
done

📝 User: 读取 hello.txt，只回答文件里的数字。
🤖 Assistant:
🔧 [Tool Call: read({'file_path': '.../dsh-workspace/bob/hello.txt'})]
📊 [Tool Result: ...]
7

📁 User workspace: .../dsh-workspace/bob
✅ hello.txt = 7
```

两个用户各自写自己的 `hello.txt`，路径分别在 `dsh-workspace/alice/` 和
`dsh-workspace/bob/`，互不影响。

## 说明

- 示例跑 `alice` / `bob` 两个用户，workspace 分别是
  `dsh-workspace/<user_id>`，验证用户级隔离。
- 示例第一轮用 `write` 工具写文件，第二轮用 `read` 工具读回；profile 的
  `sandbox-policy` / `approval` 需要允许 workspace 写入。
- `dsh-home/` 和 `dsh-workspace/` 都是运行期目录，已经在 `.gitignore` 里。
- `dsh-home/storages/session_projcache/` 是 DSH 自己的 projection cache（标题等派生数据），
  不是我们的 session 存储；session 的权威快照始终在 `SessionService.state["_trpc_dsh_session"]`。
- 一个 `DSHAgent` 对应一个 DSH 子进程。进程启动后调用 `await agent.init()`，
  退出前调用 `await agent.destroy()`；不要每轮重复调用。
