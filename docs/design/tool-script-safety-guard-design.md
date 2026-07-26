# Tool Script Safety Guard 终版设计

## 1. 目标与非目标

在执行型 Tool 真正运行 Python 脚本或 Bash 命令前，通过现有 Tool Filter
完成静态扫描和策略判断，输出 `allow`、`deny`、`needs_human_review`，并生成
结构化报告、JSONL 审计事件和 OpenTelemetry attributes。

目标：

- 输入覆盖脚本内容、argv、cwd、env、tool 元数据、timeout 和输出上限。
- 覆盖危险文件、网络外连、进程/系统命令、依赖安装、资源滥用和敏感信息泄漏。
- Python 和 Bash 使用同一报告、决策、策略和审计协议。
- 策略 YAML 修改后，无需改代码即可改变白名单域名、允许命令、禁止路径和限额。
- `deny` 与未获人工批准的 `needs_human_review` 都在 handler 前阻断。
- 新增模块语句覆盖率不低于 90%，硬门槛 85%。

非目标：

- 不实现完整 shell parser、跨过程数据流分析或自动审批服务。
- 不替代容器、用户权限、文件系统/网络隔离、seccomp 或运行时资源配额。
- 不承诺检测动态下载代码、反射、编码混淆、符号链接跳转和未知解释器漏洞。

Guard 提供静态防线和审计证据；沙箱负责执行期强制边界。两者必须同时使用。

## 2. 仓库复用点

- `trpc_agent_sdk/tools/_base_tool.py`：`BaseTool.run_async()` 已在
  `_run_async_impl()` 前执行 Tool Filter。
- `trpc_agent_sdk/filter/_base_filter.py`：`BaseFilter._before()` 可通过
  `FilterResult.is_continue = False` 阻断 handler，`_after()` 可处理返回值。
- `trpc_agent_sdk/tools/_context_var.py`：Filter 可通过 `get_tool_var()` 取得
  当前 tool。
- `trpc_agent_sdk/telemetry/_trace.py`：复用 OpenTelemetry 当前 span。
- Pydantic、PyYAML、项目 logger 均已是现有依赖。

因此不修改 `BaseTool`、`FilterRunner`、现有 Tool 或 CodeExecutor 抽象。
Guard 以 Filter 接入 Tool，以显式 wrapper 和 adapter 接入
`CodeExecutionInput`。调用方应把安全 Filter 配置在所有参数改写 Filter
之后，使其成为执行前最后一道检查；否则后续参数改写仍可能形成 TOCTOU 绕过。

## 3. 请求处理与执行流

```text
User request
  -> Runner 调用模型
  -> 模型生成 Tool function_call / executable code
  -> 按入口路由
       Tool / Skill / MCP Tool -> BaseTool.run_async()
                                -> 普通 filters/callbacks
                                -> ToolSafetyFilter（handler 前最后执行）
       CodeExecutor            -> SafetyGuardedCodeExecutor.execute_code()
  -> adapter 提取 command/code/script、argv、cwd、env keys、timeout、tool metadata
  -> 规范化 ScriptScanRequest（含嵌套解释器和多个 code block）
  -> Python AST + Bash/common rules + policy constraints
  -> evidence 先脱敏、后截断
  -> 按 deny > needs_human_review > allow 聚合 SafetyReport
  -> 必须写 audit event；尽力写 span attributes
  -> 决策分支
       allow  -> 注入有效 timeout -> 真正 handler/executor
              -> 限制 output bytes -> Tool result 返回模型
       review -> handler 不运行 -> 结构化报告返回模型
       deny   -> handler 不运行 -> 结构化报告返回模型
       audit failure -> handler 不运行
                     -> Tool Filter 返回 TOOL_SAFETY_AUDIT_FAILED
                     -> CodeExecutor wrapper 抛出脱敏 SafetyAuditError
```

`needs_human_review` 表示需要外部批准。本交付不实现审批服务，故默认阻断并返回
报告。调用方批准后可用新的、可审计请求重试；不能原地静默放行。

### 3.1 各入口的实际调用位置

| 入口 | 模型看到的能力 | Guard 位置 | allow 后真正执行 |
|---|---|---|---|
| Tool | `Bash` 等 Tool | `BaseTool.run_async()` 的最终前置 Filter | `_run_async_impl()` |
| Skill | `skill_run`、`skill_exec`、`workspace_exec` | 对执行型 Skill Tool 挂载同一 Filter | workspace runtime |
| MCP Tool | MCP 暴露的 `execute_command`/`execute_code`/`run_script` | `MCPToolset(filters=[...])` 传给每个 MCP Tool | `session.call_tool()` |
| CodeExecutor | `CodeExecutionInput` | `SafetyGuardedCodeExecutor` 组合 wrapper | delegate executor |

非执行型 Tool 不应盲目挂载 Guard。MCP Tool 名称必须使用已注册的执行协议
（`execute_command`、`execute_code`、`run_script`），否则 adapter 将其视为
不适用，避免仅凭存在 `command` 字段误判普通业务 Tool。

### 3.2 不同危险程度的处理结果

风险等级和决策是两个字段，不能只按等级硬编码。每条规则同时声明 risk level
和 decision，最终 decision 按 `deny > needs_human_review > allow` 聚合。

| 情况 | 典型命令 | 报告 | handler 是否运行 | 返回 Agent 的结果 |
|---|---|---|---|---|
| 无风险命中 | `echo safety-ok` | `allow/none` | 是 | 真实 stdout/result |
| 不确定或中风险 | `echo ok \| cat`、`pip install x` | `needs_human_review/medium` | 否 | 含 rule id、evidence、recommendation 的报告 |
| 明确高危 | `rm -rf safety-demo-trash`、读取 `~/.ssh` | `deny/high|critical` | 否 | 结构化拒绝报告 |
| 多规则混合 | 同时命中 review 和 deny | 最高风险；decision=`deny` | 否 | 合并去重后的 findings |
| 扫描/适配异常 | 非法输入、解析失败 | fail-closed 报告 | 否 | 脱敏错误摘要 |
| 审计失败 | audit sink 不可写 | fail closed | 否 | Tool 返回 `TOOL_SAFETY_AUDIT_FAILED`；CodeExecutor 抛脱敏异常 |
| allow 后超时 | 合法但执行超过 deadline | 原扫描为 allow | 已启动，随后取消 | Tool 使用注入的 timeout；CodeExecutor wrapper 返回超时结果 |
| allow 后业务失败 | 合法命令返回非零 | 原扫描为 allow | 是 | 原业务错误语义，output 仍受限额 |

`needs_human_review` 当前没有内置“批准后继续”开关。审批系统应保存原报告和审批
人，生成新的审计请求后重试；不能修改当前 `FilterResult` 原地绕过。

## 4. 数据契约

使用 Pydantic 模型：

- `SafetyDecision`：`allow | deny | needs_human_review`。
- `RiskLevel`：`none | low | medium | high | critical`。
- `RiskCategory`：`file | network | process | dependency | resource |
  secret | policy`。
- `ScriptLanguage`：`python | bash`。
- `ToolMetadata`：name、description、可选 tags。
- `ScriptPayload`：language、content、source、argv、stdin。
- `ScriptScanRequest`：payloads、cwd、可选 `execution_home`/`execution_root`、
  env keys、metadata、requested/effective timeout、max output。
- `SafetyFinding`：category、risk level、rule id、evidence、recommendation、
  decision。
- `SafetyReport`：decision、risk level、findings、duration、redacted、
  security summary、limits。
- `SafetyAuditEvent`：tool name、decision、risk level、rule ids、耗时、
  redacted、execution blocked、timestamp。

聚合优先级为 `deny > needs_human_review > allow`，风险等级取最高值。无命中时
为 `allow/none`。

所有证据和错误走同一 `SafetySanitizer`：

1. env 只保留 key，永不复制 value。
2. 私钥块整体替换为 `[REDACTED_PRIVATE_KEY]`。
3. token、password、API key、Authorization 值替换为固定占位符。
4. argv、脚本文本、Pydantic 错误、日志、report、audit、telemetry 共用规则。
5. 先脱敏再按命名常量截断，禁止截断后残留 secret 片段。

## 5. 输入适配与绕过防护

定义小型 `SafetyInputAdapter` 协议和注册表，不在 Filter 中堆积 tool 特判。

内置适配器：

- `BashTool`：`command`、`cwd`、`timeout`。
- `WorkspaceExecTool`：`command`、`cwd`、`env`、`stdin`、`timeout_sec`。
- `SkillRunTool`：`command`、`cwd`、`env`、`stdin`、`timeout`。
- `SkillExecTool`：`command`、`cwd`、`env`、`stdin`、`timeout`。
- `CodeExecutionInput`：扫描 `code` 和每个 `code_blocks`，保留各自 language。
- CLI：文件内容或命令文本、argv、cwd、env key 和 tool metadata。

递归提取：

- 对 `python -c CODE`、`bash -c COMMAND`、`sh -c COMMAND` 再生成嵌套 payload。
- 解释器从 stdin 执行时扫描 stdin。
- argv、cwd、env key 即使无脚本文本也执行通用路径/secret/策略检查。
- 只给出脚本文件路径但无法从目标执行环境读取内容时，返回
  `needs_human_review`；Filter 不擅自读取宿主同名文件。
- adapter 仅在执行实现能提供可靠值时设置 `execution_home`/`execution_root`：
  本地 Tool 使用其明确配置，workspace/远端执行器使用 runtime metadata。
  无可靠值时保持 `None`，依赖 home/root 才能判定的路径进入人工复核。
- 已知执行型 Tool 无法提取有效 payload 时返回 `needs_human_review`，不默认
  allow。
- Filter 挂到非执行型 Tool 时返回 `not_applicable/allow`；报告明确“未扫描
  脚本”，不能记为安全脚本。

`ToolSafetyFilter.__init__()` 显式调用父类初始化，并设置
`FilterType.TOOL`/稳定 name，保证 `add_one_filter()` 去重语义正确。

## 6. 策略语义

示例：

```yaml
version: 1
allowed_domains:
  - api.example.com
allowed_commands:
  - python
  - pytest
forbidden_paths:
  - ~/.ssh
  - .env
  - /etc/shadow
max_timeout_seconds: 300
max_output_bytes: 1048576
long_sleep_seconds: 60
large_write_bytes: 10485760
max_concurrency: 32
```

加载失败、未知 version、非法类型、非正阈值均在 Guard 构造时抛出脱敏配置错误，
执行链不启动。

### 6.1 域名

- 用 `urllib.parse` 解析并规范化 host，移除尾点、转小写。
- host 必须精确等于白名单项，或以 `.` + 白名单项结尾。
- `example.com.attacker.test` 不匹配 `example.com`。
- IP literal 必须在白名单显式声明。
- 动态目标、无法解析目标进入 `needs_human_review`。

### 6.2 命令

- 取规范化 basename，拒绝空命令、NUL 和路径伪装。
- Bash 管道/连接符的每一段分别校验；`sudo`、嵌套 `sh -c`、`python -c`
  递归校验。
- Python `subprocess` literal argv 使用同一校验；动态 argv 进入人工复核。
- 不在 `allowed_commands` 的命令默认为 `needs_human_review`；提权、fork bomb
  等独立高危规则仍为 `deny`。
- 命令在允许列表只豁免“命令身份”，不豁免危险参数、禁止路径、网络、安装、
  secret 和资源规则。

因此修改 `allowed_commands` 会直接改变命令身份规则结果，满足策略驱动要求。

### 6.3 路径

- 使用请求 cwd 和 adapter 提供的目标执行环境 home/root 做词法解析，不使用
  扫描宿主机的 `Path.expanduser()`。
- 处理 `..`、`.`、POSIX/Windows 分隔符、驱动器和 Windows 大小写。
- 动态路径进入人工复核。
- 符号链接真实目标只能由运行时/沙箱校验；静态 Guard 在文档和报告中明确该
  限制。

### 6.4 timeout 与 output

- adapter 根据具体 Tool 语义计算有效 timeout。
- 缺省、`0`、负值或无限值统一改为策略 `max_timeout_seconds`；显式超限值产生
  `POLICY001/needs_human_review`，未批准不执行。
- allow 时 Filter 将有效 timeout 写回对应 Tool args，由 Tool 自身执行超时；
  `SafetyGuardedCodeExecutor` 使用 `asyncio.wait_for` 对 delegate 应用协作式
  deadline。
  不响应取消的第三方代码仍需由进程/容器硬终止，Guard 不作硬实时保证。
- `max_output_bytes` 限制报告/audit evidence 和 Tool 返回给 Agent 的
  stdout/stderr/output；`_after()` 按 UTF-8 bytes 安全截断并标记
  `truncated=true`。
- 该输出限制不能阻止子进程在被读取前产生大量内核输出或占用内存。真实执行期
  stdout 配额必须由容器/runner 实现；Guard 不作虚假保证。

## 7. 扫描规则

### 7.1 Python

使用 `ast.parse()`，建立最低限度的 import alias、`from ... import ...`
alias 和模块/调用名映射。折叠字符串常量、相邻字面量和简单常量拼接。无法解析
的动态调用/目标进入人工复核。通用文本规则补充 Python 内嵌 shell。

### 7.2 Bash

使用 `shlex` 处理基础 token，预编译正则识别管道、重定向、后台、命令替换、
危险路径、URL、安装命令和 fork bomb。`sh -c`、`bash -c`、`python -c`
递归扫描，并设置最大递归深度命名常量，超深进入人工复核。

### 7.3 默认规则

| 类别 | Rule ID | 命中 | 默认结果 |
|---|---|---|---|
| 文件 | `FILE001` | 递归删除、覆盖根/系统目录 | `deny` |
| 文件 | `FILE002` | 禁止路径、`.env`、凭据/私钥文件 | `deny` |
| 网络 | `NET001` | literal host 不在白名单 | `deny` |
| 网络 | `NET002` | requests/aiohttp/socket/curl/wget 动态目标 | `needs_human_review` |
| 进程 | `PROC001` | subprocess/os.system/后台进程 | `needs_human_review` |
| 进程 | `PROC002` | 提权、明确 shell injection | `deny` |
| 依赖 | `DEP001` | pip/npm/apt 等安装 | `needs_human_review`；提权安装为 `deny` |
| 资源 | `RES001` | `while True`、fork bomb | `deny` |
| 资源 | `RES002` | 超长 sleep、超大写入、大量并发 | `needs_human_review` |
| 泄漏 | `SECRET001` | secret/private key 进入 log/file/network sink | `deny` |
| 策略 | `POLICY001` | timeout/output/命令违反策略 | `needs_human_review` |

规则对象无状态，在 Guard 构造时编译一次。扫描过程只做线性 AST 遍历和有界
递归，目标是 500 行单脚本小于 1 秒。

## 8. Filter、审计与 Telemetry

`ToolSafetyFilter._before()`：

1. 获取 tool 和适配器。
2. 构造、扫描请求。
3. 写 audit。
4. 写 span attributes。
5. deny/review 时设置报告并停止；allow 时注入 timeout 并继续。

`ToolSafetyFilter._after()` 限制返回 payload 大小，不改变 handler 的业务错误
语义。

接入：

```python
guard_filter = ToolSafetyFilter.from_policy(
    "tool_safety_policy.yaml",
    CompositeAuditSink(
        JsonlAuditSink("tool_safety_audit.jsonl"),
        LoggingAuditSink(),
    ),
)
bash_tool = BashTool(cwd=workspace)
bash_tool.add_one_filter(guard_filter)
```

CodeExecutor 不继承 `BaseTool`，使用组合式 `SafetyGuardedCodeExecutor`，不允许
调用方手工漏掉安全步骤：

```python
executor = SafetyGuardedCodeExecutor(
    delegate=unsafe_executor,
    guard=guard,
    audit_sink=audit_sink,
)
result = await executor.execute_code(invocation_context, code_execution_input)
```

wrapper 复用 `CodeExecutionInput` adapter，并在单一入口内完成扫描、audit
fail-closed、deny/review 阻断、`asyncio.wait_for()` wall-clock timeout 和
`CodeExecutionResult` stdout/stderr 截断。取消异步调用不保证底层进程必然退出；
生产 executor 仍必须实现进程终止和运行时资源隔离。

### 8.1 审计

- enforcement Filter 必须配置 audit sink；CLI 可显式关闭审计用于纯离线扫描。
- `JsonlAuditSink` 使用进程内锁保护 append，单条完成后 flush；POSIX 上每次
  打开均强制文件权限为 `0600`。
- 使用 `CompositeAuditSink` 时，primary sink 写失败会尝试配置的 fallback；
  示例 fallback 为结构化 logger。裸 `JsonlAuditSink` 不会自动 fallback。
  降级事件把 allow 提升为 `needs_human_review` 并阻断，原 deny/review 继续阻断。
- 两个 sink 都失败仍 fail closed，返回脱敏错误。无法在存储全故障时承诺日志
  已落盘，但绝不无审计地执行。
- `emit_report` 串行调用自定义 sink；进程内 coroutine/thread 并发由锁测试
  覆盖。多进程部署应使用外部集中 sink，不宣称普通 JSONL 文件具备跨进程原子
  保证。

事件至少包含 tool name、decision、risk level、排序后的 rule ids、duration、
redacted、execution blocked 和 timestamp，不保存原脚本或 env value。

### 8.2 Telemetry

当前 span 写入：

- `tool.safety.decision`
- `tool.safety.risk_level`
- `tool.safety.rule_id`
- `tool.safety.duration_ms`
- `tool.safety.redacted`
- `tool.safety.execution_blocked`

Telemetry 是可降级观测通道；无有效 span 或 attribute 写入失败只记脱敏 debug，
不能泄漏数据，也不能把 deny 改为 allow。

## 9. CLI、样本与文档

SDK 内 `_cli.py` 承担可测试 CLI 逻辑，`scripts/tool_safety_check.py` 仅做薄入口。
CLI 支持文件或命令文本、language、policy、report 路径、audit 路径、cwd、
argv/env key/tool metadata。

`examples/tool_safety_guard/manifest.yaml` 记录每个公开样本的 expected decision、
category 和是否计入安全/危险统计。公开样本严格保留题目要求的 12 个：

- 安全 Python、白名单网络请求。
- 危险删除、读取密钥、非白名单网络。
- subprocess、shell injection、依赖安装、无限循环、敏感信息输出。
- Bash 管道和动态网络人工复核。

验收计算 `false_positive / safe_total <= 10%`、总危险检出率
`detected_dangerous / dangerous_total >= 90%`，并单独断言危险删除、读取密钥、
非白名单网络 100% 检出。import alias、常量拼接、嵌套解释器、stdin、相对路径
等额外绕过变体放在单元测试中，不增加公开交付样本。

### 9.1 真实模型执行示例

`examples/tool_safety_guard/real_agent.py` 构建一个真实 `LlmAgent`，同时注册：

- 带 `ToolSafetyFilter` 的 `BashTool`；
- 带 Filter wrapper 的本地 `SkillToolSet`；
- 带 Filter 的本地 stdio `MCPToolset`；
- 包装 `UnsafeLocalCodeExecutor` 的 `SafetyGuardedCodeExecutor`。

本地 `mcp_server.py` 暴露真正执行 shell 的 `execute_command`。示例提供四个入口
各三种场景，共 12 个模型请求。review/deny 使用即使 Guard 失效也只影响示例
目录的命令；该措施只降低演示风险，不替代生产沙箱。

```bash
export TRPC_AGENT_API_KEY='<your-key>'
export TRPC_AGENT_BASE_URL='https://api.deepseek.com'
export TRPC_AGENT_MODEL_NAME='deepseek-v4-flash'

python examples/tool_safety_guard/real_agent.py --list-scenarios
python examples/tool_safety_guard/real_agent.py all
python examples/tool_safety_guard/real_agent.py tool-allow
python examples/tool_safety_guard/real_agent.py tool-review
python examples/tool_safety_guard/real_agent.py tool-deny
```

终端打印模型发出的 `CALL` 和框架返回的 `RESULT`；完整结构化安全决策写入
`real_agent_audit.jsonl`。模型可能违反提示，因此验收以实际 `CALL`、`RESULT`
和 audit 三者为准，不能只看最终自然语言回答。

## 10. 测试与验收

测试层：

- policy：配置校验、修改生效、域名边界、命令与路径规范化。
- adapters：四类入口、多个 code block、嵌套解释器、stdin、无法提取 payload。
- rules/scanner：六类风险、alias/常量折叠、动态输入、聚合、脱敏、递归上限。
- Filter：allow 调 handler；deny/review/audit failure 不调 handler；timeout
  注入；output 截断。
- audit/telemetry：字段、fallback、原 secret 不出现在任一序列化路径。
- concurrency：并发扫描、单进程 JSONL 写入不交错，自定义 sink 回调不重叠。
- acceptance：manifest 统计、12 类必需样本、500 行脚本小于 1 秒。
- quality constraints：AST 检查函数体行数、语句数、参数数和文件行数。

验收命令：

```bash
# 新增模块覆盖率（CLI 逻辑位于包内，纳入统计）
pytest tests/tools/safety \
  --cov=trpc_agent_sdk/tools/safety \
  --cov-report=term-missing \
  --cov-fail-under=90

# Python 无通用 race detector；以明确的并发安全测试替代
pytest tests/tools/safety/test_concurrency.py -q

# 直接受影响模块回归
pytest tests/filter tests/file_tools/test_bash_tool.py \
  tests/code_executors/test_base_code_executor.py \
  tests/code_executors/test_types.py \
  tests/code_executors/test_local_unsafe_local_code_executor.py \
  tests/code_executors/local tests/telemetry/test_trace.py

# 格式与 lint；changed_py_files 为相对 main 的新增/修改 Python 文件
yapf --diff ${changed_py_files}
flake8 --max-complexity=15 ${changed_py_files}
```

如环境缺少 `pytest-cov`、YAPF、flake8，必须安装
`requirements-test.txt`/项目 dev 依赖后验收，不能把缺失工具当作通过。

质量硬约束：

- 函数体不超过 80 行、60 个 AST statement。
- 圈复杂度不超过 15。
- 参数不超过 4 个（`self`/`cls` 是否计入由质量测试固定并写明；按用户要求保守
  计入）。
- 文件不超过 1000 行。
- 所有阈值使用命名常量或 policy 字段。

## 11. 分阶段实现与 subagent review 关卡

### 阶段 0：契约和样本

- 建立模型 schema、策略、manifest、统计公式和性能基线。
- R0：subagent reviewer 检查需求映射、样本代表性、非目标和文件边界。

### 阶段 1：adapter、模型、策略、sanitizer

- 实现所有输入入口、有效 timeout、路径上下文和统一脱敏。
- R1：subagent reviewer 专查未扫描载荷、secret 泄漏、fail-open。

### 阶段 2：Python/Bash/common rules

- 实现 alias、常量折叠、嵌套解释器和聚合。
- 跑检出率、误报率、性能与覆盖率。
- R2：subagent reviewer 专查绕过、规则冲突、复杂度和策略修改效果。

### 阶段 3：Filter、CodeExecutor wrapper、audit、telemetry

- 实现执行前阻断、timeout 注入、output 截断、CodeExecutor 统一安全入口、
  审计 fallback。
- R3：subagent reviewer 检查 handler 顺序、audit fail-closed、并发和错误边界。

### 阶段 4：CLI、示例、目标验收

- 生成报告/audit 示例，完成接入和限制文档。
- 跑新增模块 coverage、concurrency、直接受影响模块 pytest、YAPF、flake8。
- R4：subagent reviewer 独立 review 最终 diff 和验收证据；修复后完整复跑。

## 12. 预计文件路径

新增 SDK：

- `trpc_agent_sdk/tools/safety/__init__.py`
- `trpc_agent_sdk/tools/safety/_models.py`
- `trpc_agent_sdk/tools/safety/_sanitizer.py`
- `trpc_agent_sdk/tools/safety/_python_rules.py`
- `trpc_agent_sdk/tools/safety/_bash_rules.py`
- `trpc_agent_sdk/tools/safety/_common_rules.py`
- `trpc_agent_sdk/tools/safety/_scanner.py`
- `trpc_agent_sdk/tools/safety/_audit.py`
- `trpc_agent_sdk/tools/safety/_integration.py`
- `trpc_agent_sdk/tools/safety/_cli.py`

新增 CLI/示例：

- `scripts/tool_safety_check.py`
- `examples/tool_safety_guard/README.md`
- `examples/tool_safety_guard/tool_safety_policy.yaml`
- `examples/tool_safety_guard/manifest.yaml`
- `examples/tool_safety_guard/samples/` 下公开 `.py`/`.sh` 样本
- `examples/tool_safety_guard/tool_safety_report.json`
- `examples/tool_safety_guard/tool_safety_audit.jsonl`
- `examples/tool_safety_guard/real_agent.py`
- `examples/tool_safety_guard/mcp_server.py`
- `examples/tool_safety_guard/skills/safety-demo/SKILL.md`

新增测试：

- `tests/tools/safety/test_models_and_policy.py`
- `tests/tools/safety/test_adapters.py`
- `tests/tools/safety/test_scanner.py`
- `tests/tools/safety/test_filter.py`
- `tests/tools/safety/test_code_executor.py`
- `tests/tools/safety/test_audit_and_telemetry.py`
- `tests/tools/safety/test_concurrency.py`
- `tests/tools/safety/test_cli_and_acceptance.py`
- `tests/tools/safety/test_quality_constraints.py`

修改：

- `trpc_agent_sdk/filter/_filter_runner.py`：确保执行门禁在 handler 前最后运行，
  防止其他 filter/callback 在扫描后修改执行参数，并应用 opt-in timeout/output
  hooks。

明确不修改：

- `trpc_agent_sdk/tools/_base_tool.py`
- `trpc_agent_sdk/filter/_base_filter.py`
- `trpc_agent_sdk/code_executors/_base_code_executor.py`
- 现有 Tool/Skill/CodeExecutor 实现

## 13. 已知限制

- 静态规则仍可被复杂反射、编码、运行时下载和解释器差异绕过。
- 简单 secret taint 不能覆盖完整跨函数/跨文件数据流。
- `shlex` 不是完整 Bash parser，复杂语法会偏向人工复核。
- 符号链接、真实 DNS 解析、CPU/内存/PID 和子进程输出资源只能由运行时沙箱
  强制。
- JSONL 是本地审计示例，不是防篡改集中审计系统。
- 只有挂载 Filter 或显式调用 Guard 的执行入口受保护；部署必须清点所有入口。

## 14. 初版 review 处理记录

独立 subagent review 的 9 项必须修改全部纳入：

- 执行载荷绕过：新增 tool-specific adapters、嵌套解释器/stdin/code blocks。
- 限额语义：定义有效 timeout、写回执行参数、明确 output 能力边界。
- audit failure：改为 fail closed，补锁、flush、fallback 和并发测试。
- `allowed_commands`：定义逐段、递归和 Python subprocess 语义。
- 禁止路径：加入 cwd/目标 home/root、Windows 和动态路径处理。
- AST/Bash 绕过：加入 alias、常量折叠和嵌套命令。
- 脱敏：统一 sanitizer，覆盖全部输出路径。
- 统计验收：增加安全 corpus、变体和明确公式。
- 命令验收：加入 CLI 包覆盖、并发测试、直接受影响模块回归和复杂度检查。

同时采纳可选建议，将规则按 Python、Bash、common 拆分，降低单文件行数和圈
复杂度风险。
