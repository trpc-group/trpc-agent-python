# 工具脚本安全检查示例

本示例演示如何在执行前对 Python 脚本和 POSIX/Bash 命令进行确定性的静态安全检查。安全检查器既可以
独立运行，也可以作为 Tool Filter 使用，或包装 CodeExecutor 和工作区程序运行器。

## 快速开始

环境要求：Python 3.10 或更高版本。

首次使用时，在仓库根目录安装项目：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

然后仍在仓库根目录运行：

```bash
python examples/tool_safety_guard/run_safety_check.py \
  --manifest examples/tool_safety_guard/samples/manifest.yaml \
  --policy examples/tool_safety_guard/tool_safety_policy.yaml \
  --cwd /tmp/tool-safety-workspace \
  --check-expected \
  --report /tmp/tool_safety_report.json \
  --audit /tmp/tool_safety_audit.jsonl
```

只有全部公开样本的决策和规则 ID 都符合预期时，`--check-expected` 才返回 `0`。不使用该参数扫描单个
文件时，退出码为：

- `0`：允许执行（`allow`）
- `2`：需要人工复核（`needs_human_review`）
- `3`：拒绝执行（`deny`）

CLI 只读取并扫描脚本，不会执行脚本。

## 独立扫描器

```python
from trpc_agent_sdk.tools.safety import SafetyScanRequest
from trpc_agent_sdk.tools.safety import SafetyScanner
from trpc_agent_sdk.tools.safety import ScriptLanguage

scanner = SafetyScanner.from_yaml(
    "examples/tool_safety_guard/tool_safety_policy.yaml"
)
report = scanner.scan(
    SafetyScanRequest(
        content='import shutil\nshutil.rmtree("/")',
        language=ScriptLanguage.PYTHON,
        cwd="/tmp/tool-safety-workspace",
        tool_name="example",
    )
)
print(report.model_dump_json(indent=2))
```

## Tool Filter 接入

`BaseTool.run_async()` 会在工具处理函数之前运行已挂载的 Filter。安全 Filter 将显式配置的 Tool 参数字段
映射为 `SafetyScanRequest`。

```python
from trpc_agent_sdk.tools import BashTool
from trpc_agent_sdk.tools.safety import SafetyScanner
from trpc_agent_sdk.tools.safety import ToolSafetyFilter

scanner = SafetyScanner.from_yaml(
    "examples/tool_safety_guard/tool_safety_policy.yaml"
)
tool = BashTool(cwd="/tmp/tool-safety-workspace")
tool.add_one_filter(
    ToolSafetyFilter(
        scanner,
        language="bash",
        content_field="command",
        cwd_field="cwd",
        timeout_field="timeout",
    )
)
```

`deny` 和 `needs_human_review` 都会阻止执行，Tool 不调用真实处理函数，而是返回结构化扫描报告。人工复核
表示调用方需要先获得批准，再发起一次新调用；本模块不实现暂停、恢复和审批状态持久化。

`StreamingProgressTool` 是一个明确的例外：框架会直接调用它的 `run_streaming()`，绕过
`BaseTool.run_async()`。这类 Tool 应使用专用 wrapper 保护，不能假设普通 Tool Filter 会生效。

## Skill 与工作区运行器接入

Skill 最终通过 `BaseProgramRunner.run_program()` 执行程序，可以在调用前包装运行器：

```python
from trpc_agent_sdk.tools.safety import GuardedProgramRunner
from trpc_agent_sdk.tools.safety import SafetyGuard

guard = SafetyGuard(scanner)
runner = GuardedProgramRunner(
    workspace_runtime.runner(ctx),
    guard,
    tool_name="SkillRun",
)
result = await runner.run_program(workspace, program_spec, ctx)
```

wrapper 能识别 `bash -c <script>` 并扫描其中的脚本。调用被阻止时，它返回
`WorkspaceRunResult(exit_code=126)`，在 `stderr` 中携带脱敏报告，且不会调用真实运行器。

代码执行器可使用相同模式：

```python
from trpc_agent_sdk.tools.safety import GuardedCodeExecutor

safe_executor = GuardedCodeExecutor(real_executor, guard)
```

## 策略配置

YAML 策略只修改配置数据，不需要修改规则实现：

- `network.allowed_domains`：精确匹配的域名白名单
- `network.allow_subdomains`：是否允许已审核域名的子域名
- `commands.allowed`：可执行命令白名单
- `paths.denied`：禁止访问的凭据路径和系统路径
- `limits`：脚本、超时、输出、并发、sleep 和静态写入大小限制
- `rule_overrides`：启用、停用规则或覆盖规则动作

加载策略时会拒绝未知字段和非正数限制。`api.example.com.evil.test` 这类域名不会错误匹配
`api.example.com`。

## 规则体系

| 规则 | 覆盖范围 |
|---|---|
| `FILE-001..004` | 危险删除、工作区递归删除、凭据读取、受保护路径覆盖 |
| `NET-001..002` | 非白名单或运行时动态生成的网络目标 |
| `PROC-001..004` | shell/提权、非允许命令、动态命令、后台进程 |
| `DEP-001` | 安装或卸载依赖 |
| `RES-001..003` | fork bomb、无界时间或并发、超大静态写入 |
| `SECRET-001` | 敏感值流向输出、文件或网络 |
| `POLICY-001..002` | 超时和解析输入限制 |
| `PARSE-001` | 轻量解析器无法安全判断的输入 |

最终决策是确定性的：`deny` 优先于 `needs_human_review`，`needs_human_review` 优先于 `allow`。
风险等级与决策动作分别报告。

## 审计与 Telemetry

每次受保护的调用都会在执行前产生一条审计事件。`rule_id` 保存最高优先级命中规则（允许执行时为
`null`），`rule_ids` 保存全部有序命中规则。默认的 `LoggerAuditSink` 通过 SDK logger 输出一行
结构化日志，不会创建文件。`JsonlAuditSink` 需要显式启用，并使用进程内锁；多个进程共享同一文件时，
需要外部日志系统或锁机制。

当前 OpenTelemetry span 正在记录时，安全检查器写入：

```text
tool.safety.decision
tool.safety.risk_level
tool.safety.rule_id
tool.safety.rule_ids
tool.safety.blocked
tool.safety.duration_ms
tool.safety.sanitized
```

span attributes 不会包含源码、环境变量值或证据。报告只保留短小且经过脱敏的证据，以及输入内容的
SHA-256。

## 公开样本与实测结果

CLI 和 pytest 共用 `samples/manifest.yaml` 中的 28 个样本，包括 10 个安全样本和 18 个危险或人工
复核样本。危险删除、凭据读取和非白名单网络访问均同时覆盖 Python 与 Bash。在本次开发环境中，
28 个样本的决策和规则 ID 全部符合预期，18 个风险样本全部检出，10 个安全样本误报数为 0。评审者
仍应在自己的环境中重新测量。

运行专项测试：

```bash
pytest -q tests/tools/safety
```

## 安全边界与已知限制

该机制只是纵深防御中的一层，不能替代沙箱、文件系统权限、网络隔离、进程和内存配额、运行时超时，
也不能替代依赖来源检查。

已知限制：

- Python 使用局部 AST 分析，不是完整的跨函数污点追踪。
- Bash 使用 `shlex` 和有界结构检查，不是完整的 shell 解析器。
- 混淆、运行时生成代码、`source` 引入文件、复杂 heredoc 和间接命令可能绕过静态规则，或需要人工复核。
- Skill wrapper 扫描即将执行的命令，不会扫描 Skill 目录中的所有文件。
- 通用 Tool 只有在显式配置输出适配器时才会截断输出；wrapper 只限制其已知的返回字段。

扩展新规则时，应先在 `_rules.py` 中定义稳定的规则元数据，再增加分析逻辑、一个危险样本、一个安全
反例和相应的脱敏测试。
