# trpc_agent/agents/core 说明

本文档面向读者解释 [`trpc_agent/agents/core`](./) 中与 Skills 相关的请求处理逻辑，重点覆盖三个处理器：

- `SkillsRequestProcessor`（[`_skill_processor.py`](./_skill_processor.py)）
- `WorkspaceExecRequestProcessor`（[`_workspace_exec_processor.py`](./_workspace_exec_processor.py)）
- `SkillsToolResultRequestProcessor`（[`_skills_tool_result_processor.py`](./_skills_tool_result_processor.py)）

文档目标是回答三个问题：

1. 每个处理器解决什么问题
2. 处理器在请求流水线中的位置与协作关系
3. 如何在运行时判断“功能已生效”

## 1. 请求流水线中的职责划分

在 `RequestProcessor` 的技能相关路径中（简化）：

1. 组装基础 instruction
2. 注入 tools
3. `SkillsRequestProcessor`：注入 skills 总览与（可选）已加载内容
4. `WorkspaceExecRequestProcessor`：注入 `workspace_exec` guidance
5. 注入会话历史
6. `SkillsToolResultRequestProcessor`：在 post-history 阶段做 tool result 物化
7. 其他能力（planning/output schema 等）

可理解为：

- `SkillsRequestProcessor` 负责“技能上下文主编排”
- `WorkspaceExecRequestProcessor` 负责“执行器工具选择引导”
- `SkillsToolResultRequestProcessor` 负责“tool_result_mode 下的内容物化补强”

## 2. SkillsRequestProcessor（核心入口）

主入口：

- `SkillsRequestProcessor.process_llm_request(ctx, request)`

### 2.1 解决的问题

模型在多技能场景需要两类信息：

- 可用 skill 总览（有哪些技能、各自做什么）
- 已加载 skill 的正文/文档/工具选择（当前上下文真正可用什么）

`SkillsRequestProcessor` 提供统一策略来管理这些信息，并在 `turn/once/session` 三种加载模式下保持行为一致。

### 2.2 核心行为

一次调用中，典型流程如下：

1. 获取 skill repo（支持 `repo_resolver(ctx)` 动态仓库）
2. 执行旧状态迁移（兼容历史 key）与 turn 模式清理
3. 注入 skill 概览（始终执行）
4. 读取 loaded skills 并按 `max_loaded_skills` 裁剪
5. 按 `load_mode` 解析状态键并读取（见下文 **temp-only**）
6. 根据 `tool_result_mode` 分流：
   - `False`：直接向 system instruction 注入 `[Loaded]`、`Docs loaded`、`[Doc]`
   - `True`：跳过注入，交给 `SkillsToolResultRequestProcessor` 处理
7. `load_mode=once` 时清理 loaded/docs/tools/order 状态（offload）

### 2.3 状态语义

**temp-only 状态模型**：技能 loaded/docs/tools 状态不再维护 `temp:skill:*` 与 `user:skill:*` 两套键，也不再做 temp → user 的 promote（`_maybe_promote_skill_state_for_session` 为 no-op）。统一由 `loaded_state_key()` 等函数（[`_state_keys.py`](../../skills/_state_keys.py)）按 `load_mode` 决定键名：

| `load_mode` | 键名 | 生命周期 |
|-------------|------|----------|
| `turn` / `once` | 带 `temp:` 前缀，如 `temp:skill:loaded_by_agent:<agent>/<skill>` | `turn` 每轮 invocation 清空；`once` 注入后 offload |
| `session` | 去掉 `temp:` 前缀，如 `skill:loaded_by_agent:<agent>/<skill>` | 整个 session 内保留 |

读取一律走 `session_state + state_delta` 合并视图（`_snapshot_state`），`turn` 与 `session` 共用同一套解析函数，仅键前缀与清理策略不同。

各模式清理策略：

- `turn`
  - 每次 invocation 开始清理一次技能状态
  - 对应：`_maybe_clear_skill_state_for_turn`
- `once`
  - 本轮用完后清理，避免持续占用上下文
  - 对应：`_maybe_offload_loaded_skills`
- `session`
  - 不做 turn 级清空，也不做 once 级 offload；状态写入无 `temp:` 前缀的键

### 2.4 关键参数

- `load_mode`: `turn` / `once` / `session`
- `tool_result_mode`: 是否改为 tool result materialization 路径
- `tool_profile` / `allowed_skill_tools` / `tool_flags`: 限制可用技能工具能力面
- `exec_tools_disabled`: 关闭交互执行 guidance
- `repo_resolver`: invocation 级仓库解析
- `max_loaded_skills`: loaded 上限（超限按顺序淘汰）

参数入口：

- `set_skill_processor_parameters(agent_context, parameters)`

## 3. WorkspaceExecRequestProcessor（workspace_exec guidance）

对应实现：

- [`trpc_agent/agents/core/_workspace_exec_processor.py`](./_workspace_exec_processor.py)

### 3.1 解决的问题

在多工具场景下，模型容易混淆：

- 什么时候使用 `workspace_exec`（通用 shell）
- 什么时候使用 `skill_run`（技能内部执行）
- `workspace_exec` 的路径边界、会话工具、artifact 保存边界

处理器通过注入统一 guidance，降低误用和误判。

### 3.2 主要行为

`process_llm_request(ctx, request)` 典型步骤：

1. 判断是否启用 guidance
   - 默认按 request tools 是否包含 `workspace_exec`
   - 支持 `enabled_resolver` 动态开关
2. 生成 guidance 主体
   - 通用 `workspace_exec` 使用建议
   - `work/out/runs` 路径建议
   - “先用小命令验证环境限制”的原则
3. 按能力追加段落
   - 有 `workspace_save_artifact`：追加 artifact 保存边界说明
   - 有 skills repo：提示 `skills/` 目录并非自动 stage
   - 有会话工具：追加 `workspace_write_stdin` / `workspace_kill_session` 生命周期提示
4. 去重注入
   - 若已存在 `Executor workspace guidance:` header，则不重复追加

### 3.3 行为示例

当工具列表包含：

- `workspace_exec`
- `workspace_write_stdin`
- `workspace_kill_session`
- `workspace_save_artifact`

且 agent 绑定 skill repository 时，system instruction 会引导模型：

- 通用 shell 优先走 `workspace_exec`
- 路径优先 `work/`、`out/`、`runs/`
- 限制不先假设，先验证
- 仅在需要稳定引用时再调用 `workspace_save_artifact`

### 3.4 常见误区

- 误区：`workspace_exec` 会自动准备 `skills/` 内容
  实际：是否存在 `skills/...` 取决于是否有其他工具先 stage

- 误区：遇到限制直接下结论“环境不支持”
  实际：应先做有界验证

- 误区：所有输出都必须保存 artifact
  实际：应按稳定引用需求再保存

### 3.5 如何验证生效

优先看“发给模型前的请求”而非仅看终端事件：

1. `request.config.system_instruction` 是否包含 `Executor workspace guidance:`
2. 是否只注入一次（无重复 header）
3. 工具选择行为是否符合预期（通用 shell 走 `workspace_exec`）

## 4. SkillsToolResultRequestProcessor（tool result 物化）

对应实现：

- [`trpc_agent/agents/core/_skills_tool_result_processor.py`](./_skills_tool_result_processor.py)

### 4.1 解决的问题

仅靠 `skill_load` 的短回包（例如 `"skill 'python-math' loaded"`），模型往往拿不到可执行细节。
这个处理器负责把“已加载 skill 的实质内容”物化到模型当前请求上下文。

### 4.2 主要行为

处理器会：

1. 从 `session_state + state_delta` 读取已加载 skill
2. 在 `LlmRequest.contents` 中定位最近的 `skill_load` / `skill_select_docs` response
3. 条件满足时改写 response，注入：
   - `[Loaded] <skill_name>`
   - `Docs loaded: ...`
   - `[Doc] <doc_path> ...`
4. 若本轮没有可改写 response，fallback 到 system instruction 追加 `Loaded skill context:`
5. `load_mode=once` 时按策略清理 loaded/docs 状态

### 4.3 与 SkillsRequestProcessor 的分工

- `tool_result_mode=False`
  - 由 `SkillsRequestProcessor` 直接注入 loaded 内容
- `tool_result_mode=True`
  - `SkillsRequestProcessor` 不注入 loaded 内容
  - `SkillsToolResultRequestProcessor` 在 post-history 做物化

### 4.4 最小示例

进入处理器前：

- function call: `skill_load(demo-skill)`
- function response: `{"result":"skill 'demo-skill' loaded"}`

处理器后（示意）：

```text
{
  "result": "[Loaded] demo-skill\n\n<SKILL_BODY>\n\nDocs loaded: docs/guide.md\n\n[Doc] docs/guide.md\n\n<GUIDE_CONTENT>"
}
```

即使没有对应 tool response 可改写，也会通过 system instruction fallback 注入已加载上下文。

### 4.5 什么时候会被误判为“没生效”

最常见误区：只看终端工具即时回包。
该处理器真实生效点是“发给模型前的请求内容”，与外层事件流并不总是 1:1。

建议观察：

- `request.config.system_instruction`
- `request.contents` 里的 function response 是否已被改写

### 4.6 参数入口

- `load_mode`
- `skip_fallback_on_session_summary`
- `repo_resolver`

通过：

- `set_skill_tool_result_processor_parameters(agent_context, {...})`

注入请求构建链路。

## 5. 测试语义映射（与 examples 对齐）

可配合 [`examples/skills/run_agent.py`](../../../examples/skills/run_agent.py) 与
[`examples/skills/README.md`](../../../examples/skills/README.md) 观察实际行为。

- `workspace_exec_guidance` 类测试
  - 关注工具选择行为是否被 guidance 纠偏
  - 核心断言是 `workspace_exec` 与 `skill_run` 调用分布

- `skills_tool_result_mode` 类测试
  - 关注 materialization 信号是否出现（`[Loaded]` / `Docs loaded` / `[Doc]`）
  - 允许“请求层可见但终端不完全回显”的情况

## 6. 给读者的排障建议

1. 先确认模式参数：`load_mode`、`tool_result_mode`
2. 再确认状态读写：`state_delta` 与 `session_state` 是否符合预期
3. 最后看请求最终形态：
   - 是否注入了 guidance
   - 是否注入了 loaded context
   - 是否发生了 offload/clear

## 附录

### A. `turn` 与 `once` 的区别

二者都属于**临时状态**（键名带 `temp:` 前缀），差异在于**何时清空 loaded 状态**，以及**同一 invocation 内是否会反复注入 skill 正文**。

> **术语（避免与后文「轮」混淆）**
>
> | 术语 | 含义 |
> |------|------|
> | **invocation** | 用户发**一条消息**后，Runner 从开始处理到结束的整段流程（其间可有多次 LLM 调用、多次 tool 调用） |
> | **LLM 调用** | 每次调用模型前执行一次 `process_llm_request`（下文记为 LLM #1、#2、#3 …） |
> | **用户消息 #N** | 第 N 条用户输入，通常对应第 N 个 invocation |
>
> `turn` 的清空发生在**新 invocation 开始时**（仅一次），不是每次 LLM 调用前。因此：
> - **同一 invocation 内**（同一条用户消息、多次 LLM）：`skill_load` 写入的 state **会保留**，故 LLM #2、#3 都能读到并重复注入；
> - **跨 invocation**（用户消息 #1 → 用户消息 #2）：消息 #2 开始时 **会清空** 消息 #1 留下的 state。
>
> 下文「同一轮内」= 同一 invocation；「下一轮」= 下一条用户消息（新 invocation）。二者不矛盾。

#### 对比一览

| | `turn` | `once` |
|---|--------|--------|
| **清空时机** | 每次新 invocation（用户新发一条消息）**开始时**清空 | 每次将 skill 内容注入请求**之后**清空（offload） |
| **同一 invocation 内多步 Agent 循环** | `skill_load` 后的状态会保留，**每一步 LLM 调用都会重新注入** skill 正文 | 注入 loaded 后 offload；**后续 LLM 不再从 state 注入**（除非再次 `skill_load`） |
| **跨用户消息** | 下一条用户消息（新 invocation）开始时清空 | 不在 invocation 开始时统一清空；靠「注入后 offload」释放 state |
| **典型用途** | 一轮内多步工具调用都需要完整 skill 上下文 | 控制 token：skill 正文只进一次 prompt，之后靠历史 / tool result |
| **对应函数** | `_maybe_clear_skill_state_for_turn` | `_maybe_offload_loaded_skills` |

#### 清空边界（常见误解）

下文「清」均指清除 **skill loaded/docs/tools 的 session state**，不是清除聊天历史。

**`turn`：跨 invocation（用户消息）清，同一 invocation 内不清**

- **一次请求** = 用户发一条消息 → 一次 `run_async(..., new_message=...)` → 一个 invocation。
- **一次请求里的多轮** = 同一条消息内 Agent 循环（LLM #1 → 工具 → LLM #2 → …，多次 `process_llm_request`）。
- `turn` 只在**新 invocation 的第一次** `process_llm_request` 时清空（`processor:skills:turn_init` 保证同一条消息内后续 LLM 不再清）。

```text
用户消息 #1（invocation A）
  开始 → [清] 只清「更早 invocation」留下的 state
  LLM #1 → skill_load → LLM #2 → skill_run → LLM #3   ← 中间不再清

用户消息 #2（invocation B）
  开始 → [清] 清掉消息 #1 结束时残留的 loaded 标记
```

归纳：**同一条用户消息内的多次 LLM 不清；下一条用户消息（新 invocation）开始时清。**

**`once`：注入 loaded 之后清，不是每个 LLM 轮次都清**

- offload 仅在 `_maybe_offload_loaded_skills` 中触发，且要求本次 `process_llm_request` 读到的 `loaded` **非空**（见 `_skill_processor.py`）。
- 尚未 `skill_load` 的 LLM 调用（`loaded` 为空）**不会**触发 offload。

```text
用户消息 #1（一个 invocation）
  LLM #1：尚无 loaded → 不 offload
  skill_load → 写入 state
  LLM #2：注入 [Loaded] ... → [offload 清 state]
  skill_run
  LLM #3：state 已空 → 不再从 state 注入（除非再次 skill_load）
```

归纳：**不是「一次请求里每一轮 LLM 都清」，而是「有 loaded 且本次请求完成注入后清」。**

**一句话对比**

| 模式 | 清空边界 | 同一条用户消息内多次 LLM |
|------|----------|--------------------------|
| `turn` | **新用户消息**开始时清 | state **保留**，每步 LLM 都可能重复注入 skill 正文 |
| `once` | **每次注入 loaded 内容之后**清 | 通常只在 `skill_load` 后的那一次 LLM 从 state 注入正文 |
| `session` | 不做 turn 级开头清、不做 once 级注入后清 | 键名去掉 `temp:`，可跨多条用户消息保留 |

#### 举例：一轮内先 `skill_load` 再 `skill_run`

用户问：「用 data-analysis 分析 CSV」。Agent 在同一 invocation 内通常会经历多次 LLM 调用：

1. 第 1 次 LLM → 决定调用 `skill_load`
2. 执行 `skill_load` → 写入 state
3. 第 2 次 LLM → 构建请求、注入 skill 内容
4. 决定调用 `skill_run`
5. 第 3 次 LLM → 继续推理

**`turn` 模式**

```text
用户消息 #1 开始
  → [清空] 上一轮 skill 状态
  → LLM #1：只有 skill 概览，尚无 loaded 正文
  → skill_load("data-analysis")          // 写入 state
  → LLM #2：system 里注入 [Loaded] ...   // 同一 invocation 内 state 仍在
  → skill_run(...)
  → LLM #3：再次注入 [Loaded] ...        // 同一 invocation 内重复注入（尚未跨用户消息）
```

特点：同一轮内每一步 LLM 请求都能看到完整 skill 正文，适合多步推理时上下文需持续「在线」；代价是 token 重复消耗。

**`once` 模式**

```text
用户消息 #1
  → LLM #1：概览
  → skill_load("data-analysis")
  → LLM #2：注入 [Loaded] ... → [offload 清空 state]
  → skill_run(...)
  → LLM #3：state 已空，不再从 state 注入 skill 正文
        （若开启 tool_result_mode，正文可能在 history 的 function_response 里）
```

特点：skill 正文只在 `skill_load` 后的**那一次**请求里注入，随后从 session state 删除，避免后续每步 LLM 重复塞入 SKILL.md；适合省 token，后续步骤依赖对话历史或 `SkillsToolResultRequestProcessor` 物化结果。

#### 举例：连续两条用户消息

**`turn`**

```text
用户消息 #1：skill_load + 完成任务
  → 结束时 state 里可能仍有 loaded 标记

用户消息 #2 开始（新 invocation）
  → [清空] 消息 #1 的 skill 状态
  → 若本条消息要再用 skill，需重新 skill_load
```

**跨 invocation**（用户消息 #1 → 用户消息 #2）时，消息 #1 的 skill state **不会**带到消息 #2；这与消息 #1 **内部** LLM #2、#3 之间 state 仍保留并不冲突。

**`once`**

```text
用户消息 #1
  → 每次注入后 offload，通常不会长期保留 loaded state

用户消息 #2
  → 不会在 invocation 开始时主动统一 wipe
  → 一般仍需重新 skill_load；重点在于「单次注入后立即释放 state」
```

#### 与 `session` 对比（选型参考）

| 场景 | 建议 |
|------|------|
| 一轮内多次 LLM + 工具，每步都要完整 skill 文档 | `turn`（默认） |
| skill 正文很大，只想注入一次，后续靠历史 | `once` |
| 同一会话多轮对话都要复用已加载 skill | `session` |

`examples/skills` 未显式配置 `load_mode` 时一般为 **`turn`**。若 skill 文档较大且一轮内会多次调 LLM，可尝试 `once` 配合 `tool_result_mode=True`，将正文物化进 tool result，而不是每步重复写入 system instruction。
