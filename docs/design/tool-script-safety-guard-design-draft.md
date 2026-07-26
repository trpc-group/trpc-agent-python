# Tool Script Safety Guard 初版设计

## 1. 目标与边界

在 Tool 真正执行 Python 脚本或 Bash 命令前完成静态风险扫描，输出
`allow`、`deny`、`needs_human_review`，并生成结构化报告、JSONL 审计事件和
OpenTelemetry span attributes。

本实现负责“执行前风险识别与阻断”，不负责替代容器、权限隔离、网络隔离、
文件系统隔离或运行时资源配额。静态扫描无法可靠识别动态拼接、编码混淆、
运行时下载内容和解释器漏洞；生产环境仍必须使用沙箱和最小权限。

控制范围：

- 支持 Python 源码和 Bash 命令。
- 扫描输入包含脚本、命令行参数、工作目录、环境变量和 tool 元数据。
- 策略修改后无需改代码即可调整白名单域名、允许命令、禁止路径、最大超时、
  最大输出大小。
- 复用现有 `BaseTool -> FilterRunner -> BaseFilter` 前置链路，不修改
  `BaseTool.run_async()`。
- 首版不实现通用 shell 解释器、数据流分析器、自动审批系统或新的沙箱。

## 2. 现有扩展点

- `trpc_agent_sdk/tools/_base_tool.py`：`BaseTool.run_async()` 在
  `_run_async_impl()` 前运行 Tool Filter。
- `trpc_agent_sdk/filter/_base_filter.py`：`BaseFilter._before()` 可设置
  `FilterResult.is_continue = False`，阻止实际执行。
- `trpc_agent_sdk/tools/_context_var.py`：Filter 可通过 `get_tool_var()` 取得
  tool name 和描述。
- `trpc_agent_sdk/telemetry/_trace.py`：项目已使用 OpenTelemetry 当前 span；
  Safety Guard 只补充要求的 `tool.safety.*` attributes。
- 项目已有 Pydantic、PyYAML、日志设施，直接复用。

## 3. 总体设计

执行流：

```text
Tool args
  -> ToolSafetyFilter._before
  -> ScriptScanRequest 规范化/脱敏
  -> ToolScriptSafetyGuard.scan
      -> Python AST 规则
      -> Bash/通用文本规则
      -> 策略约束规则
      -> 聚合 decision/risk_level
  -> SafetyReport
  -> JSONL audit + current span attributes
  -> allow: 继续执行
     deny/review: FilterResult.is_continue=False，返回结构化报告
```

`needs_human_review` 在未接入审批器时按“阻断等待审批”处理，不能继续执行。

### 3.1 数据模型

使用 Pydantic，统一序列化和配置校验：

- `SafetyDecision`：`allow | deny | needs_human_review`。
- `RiskLevel`：`none | low | medium | high | critical`。
- `RiskCategory`：文件、网络、进程、依赖、资源、敏感信息、策略。
- `ScriptLanguage`：`python | bash`。
- `ToolMetadata`：tool name、description、可选 tags。
- `ScriptScanRequest`：language、content、argv、cwd、env、metadata、
  requested timeout/output size。
- `SafetyFinding`：category、risk level、rule id、evidence、recommendation、
  decision。
- `SafetyReport`：最终 decision、risk level、findings、scan duration、
  redacted 标记和安全摘要。
- `SafetyAuditEvent`：tool name、decision、risk level、rule ids、耗时、
  redacted、execution_blocked、timestamp。

证据片段限制长度并脱敏。报告和审计事件不保存原始环境变量值、疑似 secret
值或完整私钥。

### 3.2 策略

`ToolSafetyPolicy` 从 YAML 加载并严格校验：

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

域名匹配按规范化后的完整 host 或其子域匹配，防止
`example.com.attacker.test` 绕过。路径同时检查 `~` 展开形式、POSIX/Windows
分隔符和规范化文本。非法或缺失策略 fail closed：构造 Guard 时抛出明确配置
错误，不启动执行链。

### 3.3 规则实现

采用“小型 AST 扫描 + 预编译正则/`shlex` token”组合，复用标准库，不引入
第三方安全扫描器。

| 类别 | 规则 | 默认结果 |
|---|---|---|
| 文件 | `FILE001` 递归删除、覆盖根/系统目录 | `deny` |
| 文件 | `FILE002` 访问策略禁止路径、`.env`、凭据/私钥文件 | `deny` |
| 网络 | `NET001` literal URL/host 不在白名单 | `deny` |
| 网络 | `NET002` requests/aiohttp/socket/curl/wget 使用动态目标 | `needs_human_review` |
| 进程 | `PROC001` `subprocess`、`os.system`、提权、后台进程 | `needs_human_review`；明确提权为 `deny` |
| 进程 | `PROC002` 管道、重定向、命令替换、命令拼接 | `needs_human_review` |
| 依赖 | `DEP001` pip/npm/apt 等安装或修改环境 | `needs_human_review`；提权安装为 `deny` |
| 资源 | `RES001` `while True`、fork bomb | `deny` |
| 资源 | `RES002` 超长 sleep、超大写入、大量并发 | `needs_human_review` |
| 泄漏 | `SECRET001` secret/private key 进入 print/log/file/network sink | `deny` |
| 策略 | `POLICY001` timeout/output 请求超过策略 | `needs_human_review` |

Python：

- 用 `ast.parse()` 识别 import、调用链、常量 URL/路径、`while True`、sleep、
  并发构造和简单 secret-to-sink 关系。
- 语法错误不猜测为安全：产生 `needs_human_review`。
- 同时运行通用文本规则，覆盖 Python 内嵌 shell。

Bash：

- 使用 `shlex` 提取基础命令；正则识别管道、重定向、后台、命令替换、
  fork bomb、危险路径、URL 和安装命令。
- `allowed_commands` 只降低“命令本身”的风险，不能覆盖危险参数、禁止路径、
  非白名单网络和泄漏规则。

聚合优先级：`deny > needs_human_review > allow`；风险等级取最高值。无命中时
为 `allow/none`。规则对象保持无状态，Guard 在构造时编译一次规则，满足
500 行脚本单次扫描小于 1 秒目标。

### 3.4 Filter 接入

`ToolSafetyFilter`：

1. 从 `get_tool_var()` 读取 tool 元数据。
2. 从 args 的 `command`、`code` 或 `script` 提取内容；无可扫描字段时允许，
   但仍不声称已扫描脚本。
3. 从 args 提取 `cwd`、`env`、`timeout`/`timeout_sec`、`argv`。
4. 调用 Guard、写审计、写 span attributes。
5. `deny` 和 `needs_human_review` 设置 `rsp.rsp` 为报告字典并停止 Filter 链。

接入示例使用现有 API：

```python
guard_filter = ToolSafetyFilter.from_policy("tool_safety_policy.yaml")
bash_tool = BashTool(cwd=workspace)
bash_tool.add_one_filter(guard_filter)
```

不修改 `BashTool`、`SkillExecTool`、`WorkspaceExecTool` 构造函数。它们都继承
`BaseTool`，可复用同一 Filter。CodeExecutor 不继承 `BaseTool`；文档给出在
调用 `execute_code()` 前将 `CodeExecutionInput` 转换成 `ScriptScanRequest`
的显式示例，首版不侵入其抽象接口。

### 3.5 审计与 Telemetry

`JsonlAuditSink` 每次扫描追加一行 UTF-8 JSON；单事件一次写入，写入失败记录
错误但不改变既有安全决策。事件不含原始脚本和 env 值。

当前 span 写入：

- `tool.safety.decision`
- `tool.safety.risk_level`
- `tool.safety.rule_id`：排序、逗号连接
- `tool.safety.duration_ms`
- `tool.safety.redacted`
- `tool.safety.execution_blocked`

无有效 span 时 OpenTelemetry API 为 no-op，不需要功能开关。

CLI `scripts/tool_safety_check.py` 支持扫描单文件或命令文本，加载 YAML，
将完整报告输出到 stdout 或指定 JSON 文件，并可选写 JSONL 审计。

## 4. 测试与验收

公开样本至少 12 个：

1. 安全 Python。
2. 危险递归删除。
3. 读取 `~/.ssh`/凭据。
4. 非白名单网络请求。
5. 白名单网络请求。
6. `subprocess` 调用。
7. shell 注入/命令拼接。
8. 依赖安装。
9. 无限循环。
10. 敏感信息输出。
11. Bash 管道。
12. 动态网络目标，进入人工复核。

单元测试分层：

- policy：YAML 校验、策略热修改效果、域名边界、路径规范化。
- Python/Bash scanner：六类风险、聚合优先级、语法错误、证据脱敏。
- Filter：allow 时 handler 被调用；deny/review 时 handler 未调用且审计仅一条。
- audit/telemetry：必需字段、JSONL、脱敏和 span attributes。
- CLI/examples：12 个样本均可扫描并生成合法报告。
- 性能：预生成 500 行脚本，扫描耗时小于 1 秒。
- 指标集：危险样本检出率、安全样本误报率以及三类 100% 检出率显式断言。

目标新增模块语句覆盖率 `>=90%`，硬门槛 `>=85%`。验收命令：

```bash
pytest tests/tools/safety \
  --cov=trpc_agent_sdk.tools.safety \
  --cov-report=term-missing \
  --cov-fail-under=90
pytest tests/tools/safety
yapf --diff <新增和修改的 Python 文件>
flake8 <新增和修改的 Python 文件>
```

Python 没有 Go/Rust 等语言的数据竞争检测器。这里的 `race` 验收定义为并发
扫描/并发 JSONL 写入测试；若环境提供 `pytest-run-parallel` 等工具再补充，
不擅自新增开发依赖。

## 5. 分阶段实现与 review 关卡

### 阶段 0：基线与契约

- 固化公开样本、预期 decision、报告 JSON schema 和策略示例。
- 建立检出率、误报率、性能测试。
- 关卡 R0：subagent reviewer 检查需求映射、是否过度设计、文件边界。

### 阶段 1：模型、策略、扫描器

- 实现模型、YAML 加载、规则注册、Python/Bash 扫描和聚合。
- 跑 scanner/policy 测试、覆盖率、性能测试。
- 关卡 R1：subagent reviewer 检查漏检/绕过、规则冲突、复杂度和脱敏。

### 阶段 2：Filter、审计、Telemetry

- 实现 `ToolSafetyFilter`、JSONL sink、span attributes。
- 验证 deny/review 在 handler 前阻断，allow 继续执行。
- 关卡 R2：subagent reviewer 检查执行顺序、fail-closed、错误处理和并发写入。

### 阶段 3：CLI、示例、文档

- 加入 CLI、策略、12 样本、报告和审计示例、接入说明与已知限制。
- 跑目标测试、覆盖率、并发测试、fmt、lint。
- 关卡 R3：subagent reviewer 对最终 diff 和验收证据做独立 review；修复后复跑。

每个函数遵守函数体不超过 80 行/60 语句、圈复杂度不超过 15、参数不超过
4 个；每文件不超过 1000 行；阈值全部使用命名常量或策略字段。使用
`radon` 仅在环境已有时检查圈复杂度，否则通过小函数拆分和 review 控制，
不为此增加运行时依赖。

## 6. 预计文件路径

新增：

- `trpc_agent_sdk/tools/safety/__init__.py`
- `trpc_agent_sdk/tools/safety/_models.py`
- `trpc_agent_sdk/tools/safety/_sanitizer.py`
- `trpc_agent_sdk/tools/safety/_common_rules.py`
- `trpc_agent_sdk/tools/safety/_python_rules.py`
- `trpc_agent_sdk/tools/safety/_bash_rules.py`
- `trpc_agent_sdk/tools/safety/_scanner.py`
- `trpc_agent_sdk/tools/safety/_audit.py`
- `trpc_agent_sdk/tools/safety/_integration.py`
- `trpc_agent_sdk/tools/safety/_cli.py`
- `scripts/tool_safety_check.py`
- `examples/tool_safety_guard/README.md`
- `examples/tool_safety_guard/tool_safety_policy.yaml`
- `examples/tool_safety_guard/samples/` 下至少 12 个 `.py`/`.sh` 样本
- `examples/tool_safety_guard/tool_safety_report.json`
- `examples/tool_safety_guard/tool_safety_audit.jsonl`
- `tests/tools/safety/test_policy.py`
- `tests/tools/safety/test_scanner.py`
- `tests/tools/safety/test_filter.py`
- `tests/tools/safety/test_audit.py`
- `tests/tools/safety/test_cli_and_acceptance.py`

可能修改：

- `trpc_agent_sdk/tools/__init__.py`：仅在项目惯例要求顶层导出 Safety API 时修改。
- `docs/design/tool-script-safety-guard-design.md`：终版设计与 review 结论。

明确不改：

- `trpc_agent_sdk/tools/_base_tool.py`
- `trpc_agent_sdk/filter/_base_filter.py`
- `trpc_agent_sdk/code_executors/_base_code_executor.py`
- 现有 Tool/Skill/CodeExecutor 执行实现

## 7. 主要风险

- 静态规则可被字符串拼接、反射、编码和动态下载绕过。
- 简单 secret taint 只覆盖直接赋值和常见 sink，存在漏报；过宽关键词会误报。
- Bash 语法复杂，`shlex` 不是完整 parser；复杂构造进入人工复核。
- 审计文件不是防篡改存储；生产环境应转发集中日志系统。
- Filter 仅保护明确挂载它的 Tool；部署文档必须要求对所有执行型 Tool 注入。
- Guard 不能限制已获准脚本的真实 CPU、内存、进程、网络和输出，仍需沙箱。
