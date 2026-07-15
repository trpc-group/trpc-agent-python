# Tool Script Safety Guard 设计说明（中文）

[English version](tool_safety_guard.md)

面向 Tool、Skill、MCP Tool 与 CodeExecutor 所执行 Python/Bash 脚本的
执行前静态安全检查器。它输出结构化的 ``allow``、``deny`` 或
``needs_human_review`` 决策、脱敏报告、JSONL 审计事件及 OpenTelemetry
属性；**但它不能替代沙箱隔离。**

## 机制概览

Guard 是一个在代码真正执行前运行的、由策略驱动的静态安全门禁。它扫描
脚本、命令行参数、工作目录、环境变量和 tool 元数据，并按规则目录给出
三态决策。决策及其证据会以脱敏的 ``SafetyReport``、审计事件和 OTel
span 属性/指标输出。

```text
+----------------+      +-------------+      +------------------+
| Tool / Skill / | ---> |   Guard     | ---> | allow: 执行       |
| CodeExecutor   |      | (静态扫描)  |      | deny: 拦截        |
| 输入           |      |             |      | review: 暂停复核  |
+----------------+      +------+------+      +------------------+
                               |
                               v
                  +-----------+-----------+
                  | Audit | Telemetry | OTel |
                  +-----------------------+
```

静态检查只是纵深防御中的一层。它补充而不是取代容器/沙箱隔离、网络出口
策略、操作系统权限和运行时资源限制。

## 不解决的问题

* **不是沙箱。** 静态分析无法预测已放行代码在运行时的全部行为。生产环境
  仍需使用非特权容器、只读挂载、出口白名单、cgroup/ulimit 及超时限制。
* **不是完美检测器。** 混淆、运行时拼接、反射、原生扩展、下载载荷、符号
  链接竞争和依赖运行时状态的行为都可能绕过静态扫描。对不确定构造应返回
  ``needs_human_review``，而非静默放行。
* **策略文件不是秘密库。** 策略文件是决策来源，应受到保护。能修改
  ``rule_overrides`` 或允许命令的人也能降低安全门槛。

## 职责边界

| 层 | 职责 |
|---|---|
| **SafetyWrappedCallable / SafetyCheckedExecutor** | 当前可用的强制接入路径：扫描、等待审计写入，仅在允许后委托执行 |
| **ToolScriptSafetyFilter** | 为 wrapper 和未来 SDK 终端钩子归一化并记录决策 |
| **Wrapper / Sandbox / Runtime** | 运行时隔离：CPU、内存、PID、文件系统和网络硬限制 |
| **Audit / Telemetry** | 决策证据；当 ``audit.required`` 为真时，审计持久化属于 wrapper 的失败关闭门禁 |

## 快速开始

```bash
python scripts/tool_safety_check.py \
    --policy examples/tool_safety/tool_safety_policy.yaml \
    --language python \
    --script-file examples/tool_safety/samples/03_dangerous_delete.py \
    --output tool_safety_report.json \
    --audit-file tool_safety_audit.jsonl
echo $?  # 0=allow, 2=deny, 3=review, 4=输入/策略/必需审计错误
```

扫描全部 14 个公开样例：

```bash
python scripts/tool_safety_check.py \
    --policy examples/tool_safety/tool_safety_policy.yaml \
    --manifest examples/tool_safety/samples/manifest.yaml \
    --manifest-output examples/tool_safety/manifest_run.json \
    --audit-file examples/tool_safety/tool_safety_audit.jsonl
```

## 作为库使用

```python
from tool.safety import (
    ToolSafetyGuard,
    load_safety_policy,
    SafetyScanRequest,
    ScriptLanguage,
)

policy = load_safety_policy("examples/tool_safety/tool_safety_policy.yaml")
guard = ToolSafetyGuard(policy)

request = SafetyScanRequest(
    tool_name="workspace_exec",
    language=ScriptLanguage.BASH,
    script="rm -rf /tmp/x",
    cwd="/tmp",
    env={"PATH": "/usr/bin"},
)
report = guard.scan(request)
print(report.decision, report.rule_ids)
```

### 包装普通 callable

```python
import subprocess
from tool.wrapper import SafetyWrappedCallable
from tool.safety import ToolSafetyGuard, load_safety_policy, ScriptLanguage

guard = ToolSafetyGuard(load_safety_policy("policy.yaml"))
safe_run = SafetyWrappedCallable(
    guard, subprocess.run,
    tool_name="subprocess.run",
    language=ScriptLanguage.BASH,
    script_pos=0,
)
safe_run("ls -la")  # 策略拒绝时抛出 BlockedExecutionError
```

若 delegate 或调用方已在事件循环内，应使用
``await safe_run.call_async(...)``。它会在委托执行前等待必需的审计 I/O
完成。对于 tool 风格 callable，请将 ``argv_kw``、``cwd_kw``、``env_kw``、
``metadata_kw`` 和 ``output_bytes_kw`` 映射到实际参数名，以便规范化请求
包含全部可用执行字段。

### 包装 CodeExecutor

```python
from tool.wrapper import SafetyCheckedExecutor
from tool.safety import ToolSafetyGuard, load_safety_policy, ScriptLanguage

guard = ToolSafetyGuard(load_safety_policy("policy.yaml"))
safe_executor = SafetyCheckedExecutor(
    guard,
    delegate=real_executor,
    language=ScriptLanguage.PYTHON,
    effective_timeout_seconds=30,
)
await safe_executor.execute_code(code_input)
```

每个 ``CodeBlock.language`` 会独立决定扫描器；当 ``code_blocks`` 为空时，
wrapper 会继续扫描 ``CodeExecutionInput.code``。拒绝和按策略阻断的人工
复核不会到达 delegate；返回的文本输出会被限制在 ``max_output_bytes`` 内。

## 规则目录

规则 ID 是稳定接口，便于策略覆盖与审计聚合。

| Rule ID | 类别 | 默认决策 | 检测内容 |
|---|---|---|---|
| `FILE001_RECURSIVE_DELETE` | file | deny | `shutil.rmtree`、`rm -rf` |
| `FILE002_DENIED_WRITE` | file | deny | 向禁止路径写入 |
| `FILE003_CREDENTIAL_READ` | file | deny | 读取 `.ssh`、`id_rsa`、`.pem`、凭据文件 |
| `FILE004_DOTENV_READ` | file | deny | 读取 `.env` 文件 |
| `NET001_DOMAIN_NOT_ALLOWED` | network | deny | `requests`/curl/wget 访问非白名单主机 |
| `NET002_DYNAMIC_TARGET` | network | review | 运行时计算网络目标 |
| `NET003_IP_LITERAL` | network | deny | 启用 `deny_ip_literals` 时使用 IP 字面量 |
| `PROC001_PROCESS_EXEC` | process | review | 不在允许列表中的子进程或命令 |
| `PROC002_SHELL_INJECTION` | process | deny | 含 shell 语法的 `shell=True` |
| `PROC003_SHELL_OPERATOR` | process | review | `;`、`&&`、`\|`、`&`、命令替换 |
| `PROC004_PRIVILEGE` | process | deny | `sudo`、`su`、`doas` |
| `DEP001_ENV_MUTATION` | dependency | deny | `pip install`、`npm install`、`apt install` |
| `RES001_UNBOUNDED_LOOP` | resource | deny | 无 `break` 的 `while True` |
| `RES002_FORK_BOMB` | resource | deny | 经典 `:(){ :\|:& };:` fork bomb |
| `RES003_LONG_SLEEP` | resource | deny | 超过策略上限的 sleep |
| `RES004_CONCURRENCY` | resource | deny | 超过 `max_parallel_tasks` 或 `max_processes` 的并发 |
| `RES005_LARGE_WRITE` | resource | deny | 超过 `max_file_write_bytes` 的写入 |
| `SECRET001_LOG_SINK` | secret | deny | 污点值流入 print/log |
| `SECRET002_FILE_SINK` | secret | deny | 污点值流入文件写入 |
| `SECRET003_NETWORK_SINK` | secret | deny | 污点值流入网络请求载荷 |
| `PARSE001_UNCERTAIN` | analysis | review | 语法错误或无法可靠分析的构造 |
| `OBF001_DYNAMIC_EXEC` | analysis | review | `eval`、`exec`、间接 Bash 执行、解释器载荷 |
| `SAFE000` | safe | allow | 无规则命中 |
| `GUARD001_INTERNAL_ERROR` | analysis | deny | Guard 内部错误（失败关闭） |

## 策略配置

```yaml
version: "1"

defaults:
  unknown_construct: needs_human_review   # allow | needs_human_review | deny
  guard_error: deny                       # 默认失败关闭
  human_review_blocks_execution: true     # review 是否阻断 wrapper

limits:
  max_timeout_seconds: 60
  max_output_bytes: 1048576
  max_script_bytes: 262144
  max_sleep_seconds: 30
  max_parallel_tasks: 16
  max_processes: 8
  max_file_write_bytes: 10485760

network:
  allow_domains:
    - api.github.com
    - "*.internal.example.com"   # 仅匹配一层子域名
  deny_ip_literals: true

commands:
  allow: [python, python3, pytest, git]
  deny: [sudo, su, doas, chmod, chown, mount]

paths:
  deny:
    - "~/.ssh"
    - "/etc"
    - "/root"
    - ".env"
    - "**/*credentials*"

dependencies:
  decision: deny

sensitive_env_key_patterns:
  - "*KEY*"
  - "*TOKEN*"
  - "*PASSWORD*"
  - "*SECRET*"
  - "*CREDENTIAL*"

tools:
  workspace_exec:
    execution_capable: true
    language: bash
    script: command
    cwd: cwd
    env: env
    timeout: timeout_sec

rule_overrides: {}
# 覆盖任一规则决策。例如：
#   DEP001_ENV_MUTATION: needs_human_review

audit:
  enabled: true
  required: true                  # 审计写入失败时失败关闭
  path: tool_safety_audit.jsonl
```

修改 YAML 后无需改代码即可调整域名白名单、命令允许/拒绝列表、禁止路径和
资源阈值。Guard 不会监听文件变化；修改后应新建 ``ToolSafetyGuard``，并重新
注册 wrapper/filter。每份报告和审计事件中的 ``policy_hash`` 可用于关联实际
生效的策略版本。

## 审计与 Telemetry

审计事件至少包含 tool name、decision、risk level、rule ID、扫描耗时、
是否发生脱敏和是否拦截执行。事件不包含原始脚本、环境变量或参数；仅记录
脚本 SHA-256 用于关联。

启用 OpenTelemetry 后，Guard 在当前 span 上写入低基数属性：

```text
tool.safety.decision
tool.safety.risk_level
tool.safety.rule_id           # 逗号分隔，最多 8 项
tool.safety.blocked
tool.safety.redacted
tool.safety.scan_duration_ms
tool.safety.policy_hash
```

OTel 不存在时指标会安全地 no-op。可用指标：

```text
trpc_agent.tool_safety.scan_count{decision,risk_level,tool_name}
trpc_agent.tool_safety.block_count{decision,rule_id,tool_name}
trpc_agent.tool_safety.scan_duration_ms{decision,tool_name}
```

证据片段、环境变量、脚本哈希和命令文本均不会作为 span 属性或 metric label
上报。

## CLI 退出码

| 退出码 | 含义 |
|---|---|
| 0 | 最终决策为 ``allow`` |
| 2 | 最终决策为 ``deny`` |
| 3 | 最终决策为 ``needs_human_review`` |
| 4 | 输入、策略或必需审计错误 |

## 与 SDK 的接入关系

当前 SDK 尚未提供位于 ``ToolCallbackFilter`` 之后的终端 Filter 阶段。若把
普通 ``filters=`` 中的检查器作为强制点，后续 callback 仍可能修改参数，造成
TOCTOU 绕过。因此目前必须使用 ``SafetyWrappedCallable`` 或
``SafetyCheckedExecutor``：二者都会先扫描、等待审计事件完成，再调用 delegate。

``ToolScriptSafetyFilter`` 提供了与未来 SDK 终端阶段对应的
``_before``/``_after`` 钩子及 ``terminal_before_handler`` 标记。该标记在 SDK
真正实现“所有可改参 callback 之后”的排序前仅是元数据，不能视为当前的安全
强制点。

## 扩展自定义规则

实现 ``SafetyRule`` 并显式传入规则列表：

```python
from tool.safety import ToolSafetyGuard, SafetyScanRequest

class MyRule:
    rule_id = "CUSTOM001_MY_RULE"

    def scan(self, request, policy):
        # Return an iterable of SafetyFinding.
        return []

guard = ToolSafetyGuard(
    policy,
    rules=[*default_rules(), MyRule()],
)
```

规则必须是纯函数：不得执行文件 I/O、网络访问或创建进程。

## 已知限制与绕过风险

* **混淆。** Base64/十六进制解码等会隐藏意图。Guard 能识别
  ``eval``、``exec``、``compile`` 和动态导入，但不会尝试静态还原全部载荷。
* **间接数据流。** 污点传播有意保持浅层：字面量、名称、直接赋值、f-string、
  拼接和浅层容器。更深的数据流应进入人工复核。
* **符号链接竞争。** 静态路径匹配不能解析符号链接；文件系统边界必须由沙箱
  强制执行。
* **原生扩展。** ``ctypes.CDLL(...)``、``cffi.dlopen(...)`` 等原语无法可靠
  静态分析。
* **运行时下载。** 分两阶段下载并执行载荷可绕过静态检查；网络出口策略和
  沙箱不可省略。
* **运行时资源。** wrapper 会校验声明的 timeout 并限制返回输出，但无法为任意
  executor 强制 CPU、内存、PID、文件大小或网络上限。必须在沙箱或
  CodeExecutor 运行时同时配置这些限制。
* **Shell 语法缺口。** Bash lexer-lite 是保守实现；不平衡引号或不支持的替换
  形式会变成 ``PARSE001_UNCERTAIN``。

当无法确定时，Guard 应输出 ``needs_human_review``，由人工明确批准后再继续。

## 测试

```bash
python -m pytest tests/tool_safety/ -v
```

测试覆盖模型与策略校验、脱敏、Python AST 扫描、Bash lexer-lite、跨字段检查、
审计、Filter/wrapper、CLI、500 行脚本性能预算，以及 14 个样例与预期决策的
集成验证。

## 文件布局

```text
tool/
  safety/                       # Guard、策略、规则、扫描器、审计和 Telemetry
  wrapper.py                    # SafetyWrappedCallable、SafetyCheckedExecutor
scripts/
  tool_safety_check.py          # CLI
tests/tool_safety/              # 安全检查器测试
examples/tool_safety/           # 策略、14 个样例、报告和审计样例
docs/
  tool_safety_guard.md          # English version
  tool_safety_guard.zh_CN.md    # 本文
```
