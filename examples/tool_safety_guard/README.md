# 工具脚本安全检查示例

本示例演示如何在执行前对 Python 脚本和 POSIX/Bash 命令进行确定性的静态安全检查。安全检查器既可以
独立运行，也可以作为 Tool Filter 使用，或包装 CodeExecutor 和工作区程序运行器。

[English](./README.en.md)

## 快速开始

环境要求：Python 3.10 或更高版本。

首次使用时，在仓库根目录安装项目：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e ".[tool-safety]"
```

`tool-safety` 扩展提供 Bash 扫描所需的 tree-sitter 解析器。未安装该扩展时，Python 扫描仍可用；
Bash 扫描会按 fail-closed 原则返回 `needs_human_review`，并提示安装扩展。

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

未显式传入 `--language` 时，CLI 仅从 `.py`、`.sh` 和 `.bash` 后缀推断语言。其他后缀或无后缀
文件必须使用 `--language python` 或 `--language bash`，避免被静默误判为 Bash。

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
from trpc_agent_sdk.tools.safety import BashToolBlockResponseAdapter
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
        default_timeout_seconds=30,
        block_response_adapter=BashToolBlockResponseAdapter(),
    )
)
```

`deny` 和 `needs_human_review` 都会阻止执行，Tool 不调用真实处理函数，而是返回结构化扫描报告。人工复核
表示调用方需要先获得批准，再发起一次新调用；本模块不实现暂停、恢复和审批状态持久化。

`MCPTool` 也应通过构造参数 `filters=[ToolSafetyFilter(...)]` 挂载执行前 Filter；MCP 的工具选择谓词
不是执行 Filter，不能用于替代这里的安全检查。

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

对于一次受保护的程序调用，provider 环境变量会在扫描前且仅解析一次。空结果或已处理的 provider
异常同样是本次调用的最终解析结果；如果仅由 delegate 重试，可能执行扫描时从未出现的新环境值。
需要重试 provider 时应发起一次新的调用。

代码执行器可使用相同模式：

```python
from trpc_agent_sdk.tools.safety import GuardedCodeExecutor

safe_executor = GuardedCodeExecutor(real_executor, guard)
```

`GuardedCodeExecutor` 会把一次调用中的所有代码块合并为一次安全裁决和一条审计事件。未知语言、截断的
可执行输入文件以及无效的输入文件路径不会自动放行；扩展名或 MIME type 表明是 Python/Bash 的
`input_files` 也会在委托执行前扫描。CSV、图片等普通数据文件不会被当成脚本分析。

## 策略配置

YAML 策略只修改配置数据，不需要修改规则实现：

- `network.allowed_domains`：精确匹配的域名白名单
- `network.allow_subdomains`：是否允许已审核域名的子域名
- `commands.allowed`：可执行命令白名单
- `paths.denied`：禁止访问的凭据路径和系统路径
- `limits`：脚本、超时、输出、并发、sleep 和静态写入大小限制
- `rule_overrides`：启用、停用规则或覆盖规则动作

每份策略必须声明 `api_version: trpc-agent.io/tool-safety/v1`、`kind: ToolSafetyPolicy`、内容
`version` 和 `policy_id`。命令加入白名单只表示允许启动该可执行文件；没有内置参数 profile 的命令仍会
进入人工复核，不会因为一个 YAML 字符串而获得任意参数权限。
不带 `/` 的条目只匹配通过 `PATH` 解析的命令名；`/usr/bin/tool` 这类绝对路径必须在 YAML 中精确列出，
相对路径白名单会被拒绝，也不会因为文件名与某个白名单命令相同而信任 `/tmp/tool`。`PATH`、
`BASH_ENV`、`LD_PRELOAD`、`PYTHONPATH` 等会改变命令或代码解析结果的环境覆盖，以及 Python
`subprocess` 的动态 `env`/`executable`，默认阻止自动执行并要求复核。

`NET-002`、`PROC-003`、`PROC-UNKNOWN-001`、`PARSE-001` 和 `POLICY-INPUT-001`
表示目标动态、语义未知、解析不完整或输入无效。这些完整性规则不能通过 YAML 关闭或降为 `allow`，
但可以收紧为 `deny`。其他已确认语义的规则仍可按环境启停或调整动作。

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
风险等级与决策动作分别报告。`policy_relaxed` 只表示本次报告实际命中的规则使用了放宽后的策略动作；
与本次输入无关的全局放宽配置不会把该字段置为 `true`。

## 审计与 Telemetry

每次受保护的调用都会在执行前产生一条审计事件。`rule_id` 保存最高优先级命中规则（允许执行时为
`ALLOW-000`），`rule_ids` 保存全部有序命中规则。默认的 `LoggerAuditSink` 通过 SDK logger 输出一行
结构化日志，不会创建文件。`JsonlAuditSink` 需要显式启用，并使用固定大小的进程内锁分片，避免按路径
缓存锁导致无界增长；多个进程共享同一文件时，仍需要外部日志系统或锁机制。异步 Filter 和执行 wrapper
会把 audit sink 的同步 `emit()` 调用卸载到工作线程，避免文件 I/O 阻塞事件循环；独立扫描器和同步
`SafetyGuard.check()` 保持同步行为。

当前 OpenTelemetry span 正在记录时，安全检查器写入：

```text
tool.safety.decision
tool.safety.risk_level
tool.safety.rule_id
tool.safety.rule_ids
tool.safety.blocked
tool.safety.duration_ms
tool.safety.sanitized
tool.safety.analysis_complete
tool.safety.analysis_status
tool.safety.blocks_scanned
```

span attributes 不会包含源码、环境变量值或证据。报告只保留短小且经过脱敏的证据，以及输入内容的
SHA-256。

审计 sink 写入失败时，只按异常类型记录错误，不会把已经得到的 `allow`、`needs_human_review` 或
`deny` 裁决替换为基础设施异常。若业务要求“审计不可用即停止服务”，应在外层健康检查和流量入口实现，
而不是让 Tool handler 获得不一致的返回类型。

## 公开样本与实测结果

CLI 和 pytest 共用 `samples/manifest.yaml` 中的 28 个样本，包括 10 个安全样本和 18 个危险或人工
复核样本。危险删除、凭据读取和非白名单网络访问均同时覆盖 Python 与 Bash。在本次开发环境中，
28 个样本的决策和规则 ID 全部符合预期，18 个风险样本全部检出，10 个安全样本误报数为 0。评审者
仍应在自己的环境中重新测量。公开样本包含 Bash 续行、注释/单引号安全反例、Python 常量路径传播、
Session keyword URL、curl 裸 URL 和 `while 1`；更细的 argv/env、动态 shell、敏感上传和报告脱密
用例位于 `tests/tools/safety/`。

运行专项测试：

```bash
pytest -q tests/tools/safety
```

## 安全边界与已知限制

该机制只是纵深防御中的一层，不能替代沙箱、文件系统权限、网络隔离、进程和内存配额、运行时超时，
也不能替代依赖来源检查。

`GuardedProgramRunner` 和 `GuardedCodeExecutor` 都会强制执行策略超时，超时后发出协作式取消请求并
返回失败结果；外层调用被取消时也会向 delegate 传递取消。wrapper 会消费取消后迟到的任务异常，避免
产生未检索任务异常，但 delegate 仍可能吞掉 `CancelledError`，子进程也可能继续运行。只有具体
runtime 或沙箱停止进程、容器或远端任务，才能保证执行已经终止。

扫描器采用闭世界放行：`allow` 表示本次输入中的每个可执行调用、命令、wrapper、重定向、执行环境覆盖
和外部副作用参数均已被识别，并由有限的 capability/profile 和当前策略明确许可；它不等于“没有命中
危险字符串”。任何未消费参数、未知选项、动态值、未知 callable、缺少命令 profile 或不完整分析都会
返回 `needs_human_review`，并在自动执行链中阻断。YAML 是可信配置，只能裁决已经识别的能力，不能把
解析不完整或未知副作用改成 `allow`；策略变更仍应按代码变更审核。

已知限制：

- Python 使用 AST、词法作用域和有界的本地 wrapper 参数/返回值传播，不是完整的跨模块污点追踪。
- 未建模的第三方或相对 Python import 默认进入人工复核；显式传给 `GuardedCodeExecutor` 的
  Python/Bash 输入文件会分别扫描。扫描器仍无法观察工作区中未作为输入传入的模块、标准库同名模块
  覆盖或运行时新生成的文件。
- Bash 使用 tree-sitter 语法树提取实际命令、替换和重定向，再对命令参数做有界语义分析；它不是
  Bash 运行时，也不能求出所有动态展开结果。
- 混淆、运行时生成代码、`source` 引入文件、复杂 heredoc 和间接命令可能绕过静态规则，或需要人工复核。
- Skill wrapper 扫描即将执行的命令，不会扫描 Skill 目录中的所有文件。
- 通用 Tool 只有在显式配置输出适配器时才会截断输出；适配器支持 `str`、`bytes`、字符串列表或指定
  dict 字段，wrapper 只限制其已知的返回字段。

显式传给 curl/wget 的配置文件、Header/Cookie 文件或上传文件会读取扫描器无法查看的外部内容，因此
默认进入人工复核；若路径属于 `paths.denied`，则直接拒绝。Python 文件内容流向网络请求时采用相同的
保守策略。默认禁止路径还包含 `.netrc`、`.npmrc`、`.pypirc`、`.git-credentials`、Docker 凭据文件和
常见 `credentials.json`/`secrets.json`。

项目自定义规则可以实现公开的 `SafetyRule` 协议，并通过
`SafetyScanner(custom_rules=[...])` 注入，不需要修改扫描器。规则必须使用稳定且唯一的 `rule_id`，
返回相同 ID 的 `SafetyFinding`，且不得执行待扫描内容。扫描器会在合并报告前再次截断并脱敏自定义
evidence。YAML `rule_overrides` 只管理内置规则；自定义规则自行定义 action。

扫描器会在词法作用域内传播可静态确定的简单 callable/client 别名，例如 `run = os.system`、
`fetch = requests.get` 和 `fetch = session.get`。条件分支或跨作用域重绑定无法得到唯一结论时进入人工
复核。Bash 中 `env`、`command`、`exec`、`nice`、`timeout`、`nohup`、`setsid`、`xargs` 等常见
wrapper 会继续扫描其静态命令参数。无法确认身份的 Python callable、被重新绑定的模块名、动态字典查找、
带未知 callback 的高阶函数，以及同名的第三方对象方法不会按名称直接放行，而是生成
`PROC-UNKNOWN-001` 并阻止自动执行。静态分析仍无法证明所有运行时反射和混淆代码的真实行为，因此这些
情况需要人工复核和沙箱共同兜底。
