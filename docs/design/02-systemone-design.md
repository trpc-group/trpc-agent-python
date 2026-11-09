# System One 接入设计

> **结论摘要**：System One 对一份内容做结构化判断。调用方提交 `state`（待判断的内容）和一组带类型的问题，按问题名取回答案。`SystemOneClient` 用同一套问题对接两个后端：`typesafe` 调官方 API，必须带 API key，并且总会带上模型名；`laya` 调本地 `laya-serve`，key 和模型都可以省略，响应里多一份 checkpoint 路由，并且可以一次提交 1 到 64 条 `state`。目前它是独立客户端，不进入 `LlmAgent` 的对话循环。

## 1. 背景与目标

System One 对一份内容做结构化判断。调用方提交 `state`（待判断的内容）和一组带类型的问题，按问题名取回答案。

仓库需要同时接到两种部署：

- 官方 TypeSafe API。模型缺省是 `jev-latest`，地址缺省是 `https://api.typesafe.ai`。
- 本地 Laya HTTP 服务（`laya-serve`）。不指定模型时，由服务端按内容选择 checkpoint（一份已发布的模型检查点）。

问题类型、答案读取和失败处理如果各写一套，两边会慢慢对不齐。所以对外只有一个 `SystemOneClient`，传输上的差别留在内部后端。

可运行的调用示例在 [examples/systemone_model/README.md](../../examples/systemone_model/README.md)。本文只说明接入为什么这样拆、一次调用经过哪些步骤。

### 1.1 目标

- **同一组问题打两个后端**：`Choice`、`Score`、`Noul` 的构造和读取方式不变，只换客户端的创建方法。
- **类型跟官方 SDK 对齐**：问题和答案使用 `typesafe-sdk` 的对象，不再维护一套平行结构。
- **发出去之前就拦住坏参数**：空内容、空问题、非法地址、带换行的 key 不会变成一次 HTTP 请求。
- **拿回来之后核对答案**：缺答案、类型不对、选项不在候选里、分数档位数对不上，都视为这次调用失败。
- **失败可以用同一个异常基类接住**：本地问题和 HTTP 失败都是 `TypeSafeError` 的子类。
- **依赖可选**：`pip install 'trpc-agent-py[typesafe]'`，对应 `typesafe-sdk>=0.7.5`。未安装时，导入 `trpc_agent_sdk.models.systemone` 会提示安装这个 extra。

### 1.2 非目标

- 不做流式对话，也不实现成 `OpenAIModel` 那种可放进 `LlmAgent.model` 的对话模型。
- 不替 Laya 选择 checkpoint，也不重试 Laya 请求。
- 不跟随 HTTP 重定向。
- 不给官方 API 补它没有的批量接口。`system_one_batch` 只存在于 Laya。
- 不把结果自动接进 agent 的工具循环。需要放进 agent 时，由调用方把问题构造和答案读取包进自己的工具。

## 2. 方案设计

### 2.1 设计原则与总体架构

**设计原则**

1. **一个入口，两个后端**。调用方写一次 `state` 和 `questions`。`SystemOneClient.typesafe` 和 `SystemOneClient.laya` 返回同一种客户端，单次调用都是 `system_one`。
2. **问题和答案跟官方 SDK 对齐**。公开导出的 `Noul`、`Choice`、`Score` 以及对应答案，就是 SDK 里的那些类。
3. **检查放在客户端，传输放在后端**。参数校验和答案核对两个后端都走。谁来组 HTTP、要不要重试、有没有批量，由后端决定。
4. **官方已经做好的传输留给官方客户端**。TypeSafe 的鉴权、模型名、zstd 和重试策略由 `AsyncTypeSafeClient` 处理。Laya 的协议和官方不完全相同，所以单独发 JSON。
5. **公开名字只从包入口导出**。实现放在 `_` 开头的模块里。原始 SDK 仍可从 `trpc_agent_sdk.models.systemone._typesafe_sdk` 使用，日常调用走 `SystemOneClient`。

**总体架构**

```text
调用方
  state + questions
        │
        ▼
┌──────────────────────────────────────────────────────────┐
│ SystemOneClient                                          │
│   1. 校验 state / questions / timeout / url / api key    │
│   2. 交给当前后端                                         │
│   3. check_answers：答案类型和取值必须对得上问题          │
│   4. timeout > 0 时用 wait_for 包住整次调用               │
└───────────────┬──────────────────────┬───────────────────┘
                │                      │
                ▼                      ▼
   TypeSafeBackend                 LayaHttpBackend
   AsyncTypeSafeClient             纯 JSON，httpx2
   POST {base}/v1/systemone        POST {base}/v1/systemone
   无批量                          POST {base}/v1/systemone/batch
                │                      │
                ▼                      ▼
        SystemOneResponse      LayaSystemOneResponse
        官方答案对象            官方答案对象 + routing
```

`base` 是去掉末尾斜杠后的服务根地址。根地址上的路径前缀会保留，例如 `https://host/gateway` 会请求 `https://host/gateway/v1/systemone`。

### 2.2 一次调用怎么走

单次判断：

```text
system_one(state, questions, model=...)
  ├─ state 不能是 None
  ├─ questions 至少有一个，问题 id 不能为空白
  ├─ Choice / Score 的 criteria 不能为空
  ├─ backend.system_one(...)
  ├─ 每个问题都要有同类型的答案
  └─ 返回 SystemOneResponse
```

Laya 批量：

```text
system_one_batch(states, questions, model=...)
  ├─ questions 的检查与单次相同
  ├─ states 是 1 到 64 条，每条都不能是 None
  ├─ 当前后端 supports_batch 为 False 时直接失败
  ├─ POST /v1/systemone/batch
  ├─ results 的条数必须等于提交的条数
  ├─ 每一条都做答案核对
  └─ 按提交顺序返回响应列表
```

批量 JSON 里的 `total_usage` 不单独返回。调用方使用列表里每一条响应自己的 `usage`。`results` 里多出来的字段仍留在该条响应的原始 HTTP 正文中。

超时包在后端调用和答案核对之外。`timeout > 0` 时，`asyncio.wait_for` 到点就抛出 `TypeSafeAPITimeoutError`；HTTP 层使用同一个秒数。调用被取消时仍是 `CancelledError`。`timeout == 0` 表示这次调用没有截止时间，HTTP 层也不设期限。官方 SDK 不接受 `0`，所以这条「没有期限」会写成不设截止时间的 HTTP timeout，再交给它。默认是 30 秒。

### 2.3 问题与答案

一次请求有两部分：

- **`state`**：要判断的内容。字符串、字典和 Pydantic 模型都可以。Laya 会把 Pydantic 模型 `model_dump` 成 JSON 再发送。TypeSafe 把原始对象交给官方客户端，由官方客户端序列化，因此图片等 SDK 支持的内容也可以走这条路径。`None` 会在发请求前被拒绝。
- **`questions`**：问题名到问题对象的映射。答案用同一个名字取回。

三种常用问题：

| 类型 | 问的是什么 | 请求里要给什么 | 答案里读什么 |
|---|---|---|---|
| `Choice` | 从若干标签里选一个 | `criteria` 是「标签 → 说明」。说明可以是 `None` | `choice`：选中的标签；`probabilities`：各标签的概率 |
| `Score` | 按有序标准打分 | `criteria` 是从 0 分开始的说明列表 | `score`：分数；`probabilities` 的个数必须等于标准条数 |
| `Noul` | 是否题 | `instructions` 写出要判断的事。也可以用 `criteria` 说明怎样算「是」、怎样算「否」 | `noul`：0 到 1 的概率。靠近 1 表示是，靠近 0 表示否 |

同一条工单可以同时问这三件事：

```python
from trpc_agent_sdk.models.systemone import Choice
from trpc_agent_sdk.models.systemone import Noul
from trpc_agent_sdk.models.systemone import Score
from trpc_agent_sdk.models.systemone import choice_answer
from trpc_agent_sdk.models.systemone import noul_answer
from trpc_agent_sdk.models.systemone import score_answer

questions = {
    "department": Choice(
        instructions="Which team should handle this?",
        criteria={
            "billing": "invoices, payments, refunds",
            "technical": "bugs, outages",
            "other": "everything else",
        },
    ),
    "urgency": Score(
        instructions="How urgent is this?",
        criteria=["not urgent", "soon", "blocking"],
    ),
    "refund": Noul(instructions="Does the user request a refund?"),
}

department = choice_answer(response, "department").choice
urgency = score_answer(response, "urgency").score
refund = noul_answer(response, "refund").noul
```

`choice_answer`、`score_answer`、`noul_answer` 按问题名取出对应类型。名字不存在、值是 `None`、或实际类型不对，都会抛出 `SystemOneError`，错误信息里带上这个问题名。响应对象上也可以直接用 `response.choices`、`response.scores`、`response.nouls`，这三个字典只包含对应类型的答案。

官方 SDK 还有 `Box`，用来在图片里定位一块区域。校验层认识这种问题和它的答案类型。包的日常导出以 `Choice`、`Score`、`Noul` 为主；要用 `Box` 时，从 `_typesafe_sdk` 取类型。

问题也可以写成带 `type` 字段的字典，例如 `{"type": "noul", "instructions": "..."}`。客户端接受字典，也接受上面的问题对象。

Laya 的成功响应是 `LayaSystemOneResponse`，在官方字段之外多一个 `routing`：

- `routing.model`：这次选中的 checkpoint 名称，例如 `english`、`multilingual`。
- `routing.repo`：提供该 checkpoint 的仓库。服务端没给时是 `None`。
- 服务端省略整个 `routing` 时，这个字段是 `None`。

Laya 正文里还有官方模型未声明的字段，例如每个答案上的 `action`，以及 `usage.truncated`。这些字段留在 `response.raw_http_response` 里，不提升成单独属性，避免客户端跟着服务端的扩展字段一起变。

### 2.4 两个后端

两个后端都提供 `system_one`。`system_one_batch` 只由 Laya 实现。差别如下。

|  | TypeSafe | Laya |
|---|---|---|
| 创建方式 | `SystemOneClient.typesafe(...)` | `SystemOneClient.laya(base_url, ...)` |
| 传输 | `AsyncTypeSafeClient` | 自己发 JSON，`Content-Type: application/json` |
| 地址 | 参数、`TYPESAFE_BASE_URL`，否则 SDK 默认地址 | 调用时必须传入 `base_url` |
| API key | 参数或 `TYPESAFE_API_KEY`，官方客户端要求有效 key | 有 key 才发送 `Authorization: Bearer ...`；空 key 不发送 |
| 模型 | 单次 `model` 优先，否则用 `default_model`、`TYPESAFE_DEFAULT_MODEL` 或 `jev-latest` | 单次 `model` 优先于 `default_model`。两者都空时省略 `model`，由 Laya 选 checkpoint |
| 压缩 | SDK 在装了 zstd 时自行压缩请求体 | 明文 JSON，不设置 `Content-Encoding` |
| 重试 | 默认不重试。需要时传入 `RetryPolicy` | 任何失败都不重试 |
| 批量 | `supports_batch = False`，调用即 `SystemOneError` | `POST /v1/systemone/batch`，1 到 64 条 |
| 响应类型 | `SystemOneResponse` | `LayaSystemOneResponse` |
| 请求 ID | 响应头 `x-typesafe-request-id` | 响应头 `X-Request-ID`，写入 `response.request_id` |

模型名的空值在 Laya 上特意收成「不发送」。单次参数和默认值里，`None` 和 `""` 都省略字段。非字符串会在发请求前失败。

TypeSafe 不重试是有意覆盖 SDK 的默认行为。SDK 自己的 `RetryPolicy` 默认会重试；本客户端在调用方没有传入 `retry` 时，传给 SDK 的是 `RetryPolicy(max_retries=0)`。要重试时显式传入策略，例如限流后再试一次。

两边都不跟随重定向。客户端自己创建 HTTP 连接时使用 `follow_redirects=False`；调用方传入的 `httpx2.AsyncClient` 在单次请求上也会再次指定不跟随。因此 `307` 会变成对应状态码的 API 错误，不会改去访问 `Location`。

HTTP 连接的关闭规则：没传入 `http_client` 时，由本客户端创建，`aclose()` 或离开 `async with` 时关闭。传入的客户端保持打开，方便多个 `SystemOneClient` 共用连接池。

### 2.5 校验、超时与错误

**发请求之前**

- `timeout` 必须是非负数字。`bool` 不算合法数字。`0` 表示没有截止时间。
- `base_url` 必须是带主机名的 `http` 或 `https`。不能带用户名、密码、查询串或片段。路径前缀保留，末尾斜杠去掉。百分号编码不完整的地址会被拒绝。
- Laya 的 API key 可以空。非空时必须是字符串，且不能包含换行，避免一个 key 变成两行请求头。
- `state` 不能是 `None`。批量里的每一条同样不能是 `None`。
- `questions` 不能是空映射。问题名必须是非空白字符串。`instructions` 如果给了，不能是空白字符串。
- `Choice` 的 `criteria` 必须是非空映射，`Score` 的 `criteria` 必须是非空列表。

这些检查失败都是 `SystemOneError`，请求不会发出。

**拿到响应之后**

`check_answers` 按本次提交的问题逐个核对：

- 每个问题名都要有答案，而且答案类型要一致。`Choice` 不能拿回 `Noul`。
- `Choice` 的选中标签必须在 `criteria` 里；`probabilities` 里的每个标签也必须在 `criteria` 里。
- `Score` 的 `probabilities` 个数必须等于 `criteria` 的条数。
- `Noul` 和 `Box` 核对答案类型。`noul` 的概率范围、框的坐标范围留给服务端返回的数据，客户端不再加一层数值检查。

核对失败同样是 `SystemOneError`。这一步在超时计时之内：响应已经返回，但答案对不上，仍算这次调用失败。

**HTTP 失败**

Laya 把非 2xx 状态映射成官方 SDK 的异常，这样调用方不用为两个后端写两套 `except`：

| 状态码 | 异常 |
|---|---|
| 400 | `TypeSafeBadRequestError` |
| 401 | `TypeSafeAuthenticationError` |
| 403 | `TypeSafePermissionDeniedError` |
| 404 | `TypeSafeNotFoundError` |
| 422 | `TypeSafeUnprocessableEntityError` |
| 429 | `TypeSafeRateLimitError` |
| 500 及以上 | `TypeSafeInternalServerError` |
| 其他非 2xx | `TypeSafeAPIError` |

Laya 构造这些异常时，传入的说明只有状态码。`str(error)` 里是状态码和请求地址，响应正文留在 `error.body`。正文超过 4 MiB 时截断后再放进 `error.body`。TypeSafe 路径的 HTTP 错误由官方 SDK 生成，说明文字沿用 SDK 的规则。

连接失败是 `TypeSafeAPIConnectionError`。HTTP 层超时和 `wait_for` 超时都是 `TypeSafeAPITimeoutError`。

`SystemOneError` 继承 `TypeSafeError`，表示请求还没有得到可用结果：参数不合法、响应过大、答案对不上、或者在官方后端上调用了批量。因此：

```python
from trpc_agent_sdk.models.systemone import SystemOneError
from trpc_agent_sdk.models.systemone import TypeSafeError

try:
    response = await client.system_one(state, questions)
except SystemOneError:
    # 参数、批量或答案核对失败
    ...
except TypeSafeError:
    # 超时、连接失败、HTTP 状态错误
    ...
```

`except TypeSafeError` 单独使用时，也能同时接住 `SystemOneError` 和 HTTP 失败。

Laya 读响应时最多读取 4 MiB 加 1 字节。多出来的那 1 字节用来区分「刚好等于上限」和「已经超出上限」。成功正文超出 4 MiB 时抛出 `SystemOneError`。这个上限只加在 Laya 这条自己读取字节的路径上；TypeSafe 的正文由官方客户端读取。

### 2.6 包结构

```text
trpc_agent_sdk/models/systemone/
├── __init__.py         # 公开导出：客户端、三种问题、答案读取、Laya 路由、错误
├── _client.py          # SystemOneClient：创建后端、校验、超时、批量入口
├── _backends.py        # TypeSafeBackend 与 LayaHttpBackend
├── _laya.py            # Laya 的请求编码、响应解码、routing、批量拆分
├── _validate.py        # 地址、key、问题、答案核对
├── _answers.py         # 按问题名读取 Noul / Choice / Score
├── _errors.py          # SystemOneError，以及 Laya 的 HTTP 状态映射
└── _typesafe_sdk.py    # 官方 SDK 的再导出；未安装时给出安装提示
```

公开导出集中在 `__init__.py`。调用方从 `trpc_agent_sdk.models.systemone` 导入 `SystemOneClient`、问题类型、答案函数和错误类型。`_typesafe_sdk` 保留给需要官方客户端全部类型的场景，例如 `Box`、同步 `TypeSafeClient`，或直接使用 `AsyncTypeSafeClient`。

安装：

```bash
pip install 'trpc-agent-py[typesafe]'
```

## 3. 采用什么、自己做什么

官方 SDK 负责 TypeSafe 那一侧已经定义好的协议。本仓库负责把 Laya 接进同一个调用方式，并让两个后端共用参数检查和答案核对。

| 事项 | 采用官方 SDK | 本仓库实现 |
|---|---|---|
| `Noul` / `Choice` / `Score` / `Box` 及答案对象 | 是 | 从包入口再导出常用类型 |
| TypeSafe 的 URL、key、模型名、zstd、重试 | 是 | 未传 `retry` 时改成不重试 |
| Laya 的 JSON 请求、省略 key、省略模型 |  | `LayaHttpBackend` 与 `_laya.py` |
| Laya 的 `routing` 和批量 |  | `LayaSystemOneResponse`、`system_one_batch` |
| 发请求前的参数检查、返回后的答案核对 |  | `_validate.py`，两个后端都经过 |
| 按问题名读取一种答案 |  | `noul_answer`、`choice_answer`、`score_answer` |
| Laya 响应大小上限、错误正文不进入 `str(error)` |  | `_errors.py` 与 Laya 的限长读取 |
| 调用方传入的 HTTP client 保持打开 |  | 只有客户端自己创建的连接会在 `aclose` 时关闭 |

这样，换后端时改的是创建客户端的那一行。问题怎么写、答案怎么读、失败怎么接，保持同一套。
