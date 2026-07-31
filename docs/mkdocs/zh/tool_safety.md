# 工具脚本安全守卫

工具脚本安全守卫在显式配置的真实执行边界之前，对 Python、Shell 或结构化命令字段做静态扫描，并返回 `allow`、`deny` 或 `needs_human_review`。第一版会同时阻断 deny 和 review。

它是执行前检查，不是沙箱。`allow` 只表示当前静态规则与 Policy 没有阻断该输入；运行不受信任代码时仍需最小权限以及合适的容器或远程 Sandbox。

## 公共 API 与基本用法

公共名称只从 `trpc_agent_sdk.safety` 导入，不扩大 SDK 顶层 API：

```python
from trpc_agent_sdk.safety import PolicyLoader
from trpc_agent_sdk.safety import SafetyScanRequest
from trpc_agent_sdk.safety import SafetyScanner

loader = PolicyLoader("examples/tool_safety/tool_safety_policy.yaml")
policy = loader.load()
scanner = SafetyScanner(policy)
report = scanner.scan(
    SafetyScanRequest(
        script="print('hello')",
        language="python",
        source_type="application",
    )
)
print(report.decision.value)
```

raw script、env、完整 argv 和无界 metadata 不进入请求默认 repr/dump。报告保留 SHA-256 输入标识和有界、已脱敏 evidence，不保存 source preview。hash 不是加密，也不能替代脱敏。

## 不改代码地修改 Policy

YAML 必须声明 `schema_version: "1"`，未知字段、重复 key、anchor、alias 和 merge key 都会失败。Policy 支持域名/路径/命令列表、Rule 开关与受限 override、risk threshold、failure action、nested budget、脱敏上限、Audit 声明和 runtime-only 声明。第一版不允许关闭脱敏。

```python
loader = PolicyLoader("policy.yaml")
policy_a = loader.load()
scanner = SafetyScanner(loader)

# 仅在显式管理操作时 reload；失败会保留 last-known-good。
policy_b = loader.reload()
```

Scanner 热路径不重复读 Policy 文件，同一次 root/nested 扫描固定使用相同不可变快照和 hash。第一版 `block_on_review` 固定为 true。普通 allowlist 不能覆盖秘密外传、protected-root 删除、fork bomb 或明确 download-and-execute。

`allowed_commands` 在 `SafetyProgramRunner` 等 structured `argv` 边界作为白名单执行，未列出的命令需要 review；自由 Shell 脚本仍由语义 Rule 判断，不采用全局命令白名单。第一版的 `allowed_paths` 是保留的 Policy metadata，不会把路径标记为可信，也不能覆盖 Finding。

`runtime_limits.enforcement` 固定为 `declaration_only`。timeout、CPU、内存、PID、网络和文件系统限制必须由 Executor/Sandbox 接线强制，静态 Scanner 不执行这些限制。

## Python 与 Shell 覆盖

Python 每个 source 只调用一次 `ast.parse`，共享 import/from-import alias、简单 shadowing、Call/参数、known/partially-known/unknown、常量拼接、f-string 静态部分、有限 object origin 和 source-to-sink。覆盖敏感文件、文件变更、网络目标、进程、动态执行、依赖安装、资源模式和可静态恢复的 nested script。

Shell 每个 source 只经过一次 quote/operator-aware 保守 lexer，保留 quote、escape、group、pipeline、env prefix、ordered redirection、wrapper、command substitution、heredoc 和 `-c` nested payload。`shlex` 只用于把已有 structured argv 安全表示成静态输入，不声称它是完整 Bash parser。

语法错误、不支持语言、安全相关动态值和预算耗尽不会静默 allow。默认把不完整分析映射为阻断 review，把 Scanner internal failure 映射为 deny。

## Tool Filter 接入

Filter 必须配置显式 extractor，避免把普通业务字符串无差别当脚本：

```python
from trpc_agent_sdk.safety import ToolArgumentExtractor
from trpc_agent_sdk.safety import ToolSafetyFilter

safety_filter = ToolSafetyFilter(
    scanner,
    ToolArgumentExtractor(
        script_field="script",
        language_field="language",
        cwd_field="cwd",
        env_field="env",
    ),
)
# 将 safety_filter 放入 BaseTool/FunctionTool 的 filters。
```

它在修复后的 args 到达 `BaseTool._run_async_impl` 前扫描。allow 只调用真实 Tool 一次并原样保留返回值；deny/review 调用零次并返回最小、已脱敏的 `{"safety": ...}` envelope。上游仍负责保留 function-call/response id。Filter 不保存可变 `last_report`。

## CodeExecutor wrapper 接入

```python
from trpc_agent_sdk.safety import SafetyCodeExecutor

safe_executor = SafetyCodeExecutor(inner=existing_executor, scanner=scanner)
# 配置为 LlmAgent(code_executor=safe_executor)。
```

wrapper 先扫描整个 code block batch。任一 block deny/review 会阻断整批，inner 调用零次；全 allow 时只委托一次，并返回同一个原始 `CodeExecutionResult` 对象。阻断使用现有类型的 `OUTCOME_FAILED`、原 execution id 和 output 中最小 JSON safety envelope；详细 report 通过 `SafetyScanner(report_observers=(callback,))` 旁路获得。inner backend exception 不会伪装成 Policy deny。

## Callable、Skill、Workspace 与 MCP

`SafetyCallable` 用显式 request factory 包装同步或异步 callable；完整的无执行示例位于 [examples/tool_safety](../../../examples/tool_safety/)。

Skill 是工作流包，不是统一执行器；必须保护它最终调用的 FunctionTool、CodeExecutor、Workspace runner 或 MCP Tool。`SafetyProgramRunner` 只组合 `BaseProgramRunner.run_program`：allow 原样转发同一个 `WorkspaceRunProgramSpec`，阻断返回 exit code 126 且不调用 inner；它不伪造不存在的 `start_program`。

`SafetyMCPAdapter` 在 ClientSession-like `call_tool` 前按 tool name 选择 extractor；未配置 extractor 的业务 args 原样传递。客户端扫描看不到 MCP Server 内部、discovery、后续动作或 stdio Server 启动。

集成方必须区分 framework-controlled Skill staging 与 model-controlled Workspace command；全局包装每条 Workspace 命令可能阻断可信的框架内部流程。

## Audit、脱敏、Monitor 与 OpenTelemetry

```python
from pathlib import Path
from tempfile import gettempdir

from trpc_agent_sdk.safety import JsonlAuditSink
from trpc_agent_sdk.safety import OpenTelemetrySafetySink

audit = JsonlAuditSink(Path(gettempdir()) / "tool-safety-audit.jsonl")
scanner = SafetyScanner(
    policy,
    audit_sink=audit,
    telemetry_sink=OpenTelemetrySafetySink(),
)
```

allow、deny 和 review 都会在 decision 完成后生成 immutable observation。fan-out 前统一进行一次有界递归 sanitizer，覆盖 mapping、容器、dataclass、Pydantic、bytes、Path、Enum、Exception、循环引用、敏感 key 和尺寸上限。sink 不接收 raw script、完整 env/argv/output、evidence、完整 URL/path/command。

JSONL 使用 UTF-8、一事件一行、`allow_nan=False` 和进程内锁。请每进程使用独立文件或外部 writer；不承诺多进程同时 append 的原子性。reader 可忽略一个 partial tail。sink failure 不改变 decision，并向剩余 Monitor 发送已脱敏 health signal。

可选 OTel sink 沿用仓库的 `trpc.python.agent` instrumentation scope，只发送 `safety.scan` event、计数/耗时指标和低基数 `safety.*` 属性。无 provider、无 active span、reporter/exporter failure 均不改变决策。

## 新增 Safety Rule

继承 `SafetyRule`，使用稳定、带命名空间的 `rule_id`，保持实例无状态，并返回 immutable `SafetyFinding`。Rule 只能读取 context 和 Policy；不得执行 source、访问网络/真实文件、写 Audit 或决定整个 Report。

```python
from trpc_agent_sdk.safety import RiskLevel
from trpc_agent_sdk.safety import SafetyCategory
from trpc_agent_sdk.safety import SafetyFinding
from trpc_agent_sdk.safety import SafetyRule

class OrganizationRule(SafetyRule):
    rule_id = "ORG.PROCESS.EXAMPLE"
    languages = ("python",)

    def evaluate(self, context, policy):
        del policy
        if "organization_specific_call(" not in context.source:
            return ()
        return (
            SafetyFinding(
                rule_id=self.rule_id,
                category=SafetyCategory.PROCESS,
                risk_level=RiskLevel.MEDIUM,
                message="Organization-specific operation requires review.",
                evidence="<organization operation>",
                recommendation="Obtain approval.",
            ),
        )

scanner = SafetyScanner(policy, rules=(OrganizationRule(),))
```

Rule 异常统一转为 fail-closed internal diagnostic；Finding 稳定排序、去重，注册顺序不改变 decision precedence。

## CLI 与退出码

CLI 只读取和扫描，不执行输入：

```bash
.venv/bin/python scripts/tool_safety_check.py script.py \
  --policy examples/tool_safety/tool_safety_policy.yaml --json

.venv/bin/python scripts/tool_safety_check.py --stdin --language shell --json

.venv/bin/python scripts/tool_safety_check.py \
  --manifest examples/tool_safety/sample_manifest.yaml \
  --policy examples/tool_safety/tool_safety_policy.yaml --json
```

| 退出码 | 含义 |
| --- | --- |
| 0 | allow，或 manifest 全部匹配 |
| 2 | deny |
| 3 | needs_human_review |
| 4 | invalid input/policy |
| 5 | operational failure |
| 6 | manifest expectation mismatch |

公开 manifest 覆盖 12 个具名样例。检测率和误报率使用独立的确定性 corpus；不会把 12 个演示样例复用为指标分母。

## 误报、漏报、绕过与非目标

动态 Python 反射、复杂控制/数据流、运行时 monkey patch、native extension，以及复杂 Shell expansion/function/array/process substitution、运行时 sourced content 可能分析不完整。保守 review 会造成误报；unknown、混淆、编码、替代 binary、parser differential 和外部服务隐藏行为可能造成漏报或绕过。

不得把 corpus 指标外推为任意真实程序检测率。Policy 应版本化，Scanner health 应监控，并结合组织语料、最小权限与独立 runtime enforcement。

安全非目标与已知限制：

- 不提供 CPU、内存、PID、网络、文件系统、syscall、权限或 secret runtime isolation；
- 不保证 allow 脚本无害；
- 不是完整 Python 语义解释器或完整 Bash parser；
- 不自动覆盖所有 Skill、Workspace、MCP discovery/startup 或 Server 内部；
- 第一版没有审批/恢复状态机，review 固定阻断；
- 不保证多进程 JSONL writer。

Safety Guard、Filter、CodeExecutor/Workspace 边界、Sandbox 与 Telemetry 是互补层，任何一层都不能替代其他层。
