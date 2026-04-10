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
5. `load_mode=session` 在 temp-only 设计下与 `turn` 读取语义一致
6. 根据 `tool_result_mode` 分流：
   - `False`：直接向 system instruction 注入 `[Loaded]`、`Docs loaded`、`[Doc]`
   - `True`：跳过注入，交给 `SkillsToolResultRequestProcessor` 处理
7. `load_mode=once` 时清理 loaded/docs/tools/order 状态（offload）

### 2.3 状态语义

- `turn`
  - 每次 invocation 开始清理一次技能状态
  - 对应：`_maybe_clear_skill_state_for_turn`
- `once`
  - 本轮用完后清理，避免持续占用上下文
  - 对应：`_maybe_offload_loaded_skills`
- `session`
  - 在 temp-only 状态模型下，不再维护 `user:skill:*` 双键
  - 对应：`_maybe_promote_skill_state_for_session`（当前为 no-op）

读取策略为单一 temp key 读取（`session_state + state_delta` 视图）。

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
