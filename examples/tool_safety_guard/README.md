# Tool Script Safety Guard

**语言 / Language**: [中文](#中文) | [English](#english)

---

<a id="中文"></a>

# 工具脚本安全护栏（中文）

面向 tRPC-Agent 中工具 / 技能 / `CodeExecutor` 载荷的**执行期策略闸门 + 可观测性**组件。
在脚本或命令真正运行之前，护栏会对其做静态扫描，返回三种决策之一——
`allow`（放行）/ `deny`（拒绝）/ `needs_human_review`（需人工复核）——
并附带一份结构化报告、一条可审计事件以及 OpenTelemetry 属性。

> 它是一道**执行前闸门**，是纵深防御的前置一环。
> 它**不是**沙箱，**不提供**运行时隔离。参见
> [为什么它不能替代沙箱](#6-与沙箱--filter--telemetry--codeexecutor-的关系)。

---

## 1. 它是什么 / 不是什么

| 它**是** | 它**不是** |
|---|---|
| 静态的、执行前的策略闸门（Filter / 包装器） | 运行时沙箱 / 容器 |
| 结构化报告 + 审计日志 + OTel span 的生产者 | 资源限制器（CPU / 内存 / 超时） |
| 快速、由允许列表驱动、失败即安全的决策器 | 对混淆 / 动态载荷的保证 |

静态文本分析可能被欺骗（见[已知限制](#7-已知限制)）。
对运行时行为的真正隔离是**沙箱**的职责。二者互补，而非互相替代。

---

## 2. 规则体系

### 六类风险

| 类别 | `RiskType` | 示例 |
|---|---|---|
| 危险文件操作 | `DANGEROUS_FILE_OP` | `rm -rf /`、`shutil.rmtree("/")`、`chmod 777` |
| 网络出站 | `NETWORK_EGRESS` | 请求非允许列表主机、内网 IP |
| 进程 / 命令执行 | `PROCESS_EXEC` | `subprocess`、`os.system`、shell 注入、`eval` |
| 依赖安装 | `DEPENDENCY_INSTALL` | `pip install`、`npm install`、`curl ... \| bash` |
| 资源滥用 | `RESOURCE_ABUSE` | `while True`、fork 炸弹、超长 `sleep` |
| 密钥泄露 | `SECRET_LEAK` | 读取 `~/.ssh/id_rsa` / `.env`、打印 `api_key` |

### `rule_id` 命名

`rule_id` 采用 `<类别>_<动作>_<对象>` 的 UPPER_SNAKE_CASE 形式。前缀：
`FILE` / `SECRET` / `NET` / `EXEC` / `PRIV` / `PKG` / `RES`。示例：
`FILE_RM_RF`、`SECRET_READ_SSH`、`NET_EGRESS_NON_ALLOWLIST`、`EXEC_SHELL_INJECTION`、
`PKG_CURL_PIPE_SH`、`RES_INFINITE_LOOP`。

### 三级决策聚合

每条规则带有 `risk_level`（`low/medium/high/critical`）和
`suggested_action`（`allow/review/deny`）。单条 finding 的决策取其 action 与
其 level 经 `decision_thresholds` 映射出的决策中**更严重**的一方。报告整体决策
取所有 finding 中最严重的决策：

```
finding 为 CRITICAL/HIGH 且 action=deny  -> DENY
任意 finding 的 action=deny               -> DENY
任意 finding 为 MEDIUM 或 action=review   -> NEEDS_HUMAN_REVIEW
其余                                      -> ALLOW
```

三类**必拦**场景——密钥读取、危险删除、非允许列表出站——在 `rules.py` 中
固定为 `CRITICAL + DENY`，因此在任何合理的阈值调优下都会被拒绝。

> `needs_human_review` **不会阻断**执行；它只是把该次调用标记为需要带外人工裁决。
> 只有 `deny` 会阻断。这也是为什么误报指标只统计 `safe -> deny`。

---

## 3. 接入方式（四种）

### a) 作为工具 Filter

```python
from trpc_agent_sdk.tools.safety import ToolSafetyFilter  # 注册 "tool_safety_guard"
from trpc_agent_sdk.tools import register_tool

@register_tool("Bash", filters_name=["tool_safety_guard"])
class MyBashTool(...):
    ...
```

命中 `deny` 时，Filter 返回 `is_continue=False` 的阻断结果，**不会调用工具主体**，
并写入一条审计事件。

### b) `SafeBashTool` 包装器

```python
from trpc_agent_sdk.tools.safety import SafeBashTool

tool = SafeBashTool(cwd="/work", audit_path="tool_safety_audit.jsonl")
```

`BashTool` 的子类，在真正调用 `asyncio.create_subprocess_shell` 之前扫描命令。

### c) `guard_code_executor` 包装器

```python
from trpc_agent_sdk.tools.safety import guard_code_executor

guarded = guard_code_executor(my_code_executor, audit_path="audit.jsonl")
```

在委托给内部 `BaseCodeExecutor` 之前扫描每个代码块。

### d) 命令行（CLI）

```bash
python scripts/tool_safety_check.py examples/tool_safety_guard/samples \
  --policy examples/tool_safety_guard/tool_safety_policy.yaml \
  --report tool_safety_report.json \
  --audit tool_safety_audit.jsonl
```

任何内容被拒绝时以非零码退出（`--fail-on deny|review|never`）。

---

## 4. 策略文件（`tool_safety_policy.yaml`）

所有行为都在此处调优——**无需改代码**（验收标准 6）。

| 字段 | 含义 |
|---|---|
| `allow_domains` | 出站允许列表（主机或子域名）。其余均视为非允许列表出站。 |
| `allowed_commands` | 视为可接受的 Bash 基础命令；其余标记为需复核。 |
| `forbidden_paths` | 禁止读写的路径。 |
| `max_timeout` / `max_output_size` | 报告中呈现的运行时预算（由沙箱强制执行）。 |
| `decision_thresholds` | 每个风险等级升级到的决策的映射。 |
| `param_keys` | 工具名关键字 → 扫描哪些参数键、用哪个扫描器。 |
| `redact` | 证据脱敏（开关、掩码字符串、额外模式）。 |
| `scan_limits` | `max_input_size` / `max_line_length`——保护扫描器的边界（防 ReDoS / OOM）。 |

解析顺序：显式 `load_policy(path=...)` → `TOOL_SAFETY_POLICY_PATH` 环境变量 → 内置默认值。
显式指定的文件若缺失、格式错误或非法将**快速失败**；内置默认值永不抛错。

热加载示例：

```yaml
allow_domains: [api.example.com]   # 新增可信主机 -> 其请求变为 allow
forbidden_paths: [/etc, ~/.ssh]    # 收紧禁止访问的路径
allowed_commands: [ls, cat, git]   # 放宽/收紧可接受的 bash 命令
```

---

## 5. 报告 / 审计 / OTel 字段

**结构化报告**（`SafetyReport.to_dict()`）——五个必备要素：

```json
{
  "tool_name": "02_dangerous_delete.py",
  "language": "python",
  "decision": "deny",
  "risk_level": "critical",
  "redacted": false,
  "scan_duration_ms": 0.1,
  "findings": [
    {
      "rule_id": "FILE_RM_RF",
      "risk_type": "dangerous_file_op",
      "risk_level": "critical",
      "evidence": {"snippet": "shutil.rmtree(\"/\")", "line": 6},
      "recommendation": "Recursive force-delete is destructive...",
      "suggested_action": "deny"
    }
  ]
}
```

**审计日志**（`tool_safety_audit.jsonl`，每行一个 JSON 对象）：`timestamp`、
`tool_name`、`language`、`decision`、`risk_level`、`rule_id`、`rule_ids`、
`finding_count`、`scan_duration_ms`、`redacted`、`blocked`。

**OTel span 属性**（启用追踪时设置在活动 span 上，否则静默跳过）：
`tool.safety.decision`、`tool.safety.risk_level`、`tool.safety.rule_id`、
`tool.safety.rule_ids`、`tool.safety.blocked`、`tool.safety.redacted`。

---

## 6. 与沙箱 / Filter / Telemetry / CodeExecutor 的关系

需求背景描述了一条完整的链路——**事前扫描、事中隔离、事后审计**。护栏负责
其中的*事前*与*事后*两环：

| 阶段 | 防御 | 负责方 | 本组件覆盖？ |
|---|---|---|---|
| **事前** | 静态扫描 + 决策（allow/deny/review） | **本护栏**（Filter / 包装器） | ✅ 是 |
| **事中** | 资源限制（timeout/cgroup/memory）+ 隔离 | **CodeExecutor 沙箱**（容器 / E2B） | ❌ 否——交由沙箱 |
| **事后** | 审计日志 + 指标 + 追踪 | **本护栏**（`audit.jsonl` + OTel span） | ✅ 是 |

- **对比 Filter**：护栏本身*就是*一种专用工具 Filter；它接入既有的
  `BaseTool` → `FilterRunner` 流水线，在 `run()` 中、`handle()` 之前进行闸门控制。
- **对比 Telemetry**：护栏把决策以 span 属性 / 审计记录的形式发出，由遥测栈导出到监控。
- **对比 CodeExecutor**：`guard_code_executor` 包装一个执行器，在代码运行前扫描；
  执行器的沙箱仍负责运行时隔离。

### 为什么它不能替代沙箱

护栏执行的是**静态文本分析**，只能看到*字面写出来*的内容。它无法约束**运行时行为**：
一段动态拼接命令、先 base64 解码再 `exec` 的载荷，或仅仅用一个运行时计算的边界
在循环里耗尽全部内存的代码，都会绕过静态规则。只有强制 `timeout` / `cgroup` /
内存上限 / 系统调用限制的沙箱才能遏制它们。护栏是快速、可审计的第一道闸门；
沙箱才是真正的遏制手段。**纵深防御，而非替代关系。**

---

## 7. 已知限制

- **误报**：注释里或不可达分支中一个看起来危险的字符串（如 `# never run rm -rf /`）
  可能被标记，尽管它从不运行。
- **漏报（绕过）**：动态构造（`getattr(os, "sys" + "tem")`）、base64/`eval` 解码、
  混淆、间接调用、子 shell，或"先写文件再 `source` 它"都能绕过静态规则。
- **资源滥用是最弱的类别**：死循环、fork 炸弹、超大写入和长 sleep 本质上都是*运行时*行为。
  护栏只能捕捉字面可见的模式（`while True`、fork 炸弹语法、常量大 `sleep`）；
  运行时计算出的边界会被漏掉。**真正的资源耗尽必须由沙箱遏制。**
- **裸 socket 主机不解析**：网络外连检测依赖 URL 字面量与下载器命令（`curl`/`wget` 等）。
  `socket.connect(("evil.com", 80))` 这类不含 `http(s)://` 前缀、主机以变量传入的连接不会命中
  `NET_*` 规则。若需覆盖，请在沙箱出网层面做主机级管控。
- **`forbidden_paths` 按字面/`~` 展开匹配**：禁止路径检测对每行做带边界的字面匹配（并对 `~` 做
  home 展开），因此运行时拼接出来的路径（`os.path.join(base, "etc")`）或经变量传入的路径会被漏掉；
  过于宽泛的 `/` 条目会被跳过以避免误报（根目录删除已由 `FILE_RM_RF` 覆盖）。`/dev`、`/proc`、
  `/sys` 前缀的命中归类为 `FILE_OVERWRITE_DEVICE`，其余归类为 `FILE_FORBIDDEN_PATH`。

这些限制是静态分析固有的，也正是护栏被定位在沙箱之前——而绝非取代沙箱——的原因。

---

## 8. 如何扩展新规则

1. 在 `trpc_agent_sdk/tools/safety/rules.py` 中新增一个 `RuleSpec`（选定 `rule_id`、
   `RiskType`、`RiskLevel`、`SuggestedAction`、recommendation）。
2. 添加检测逻辑：
   - 可正则检测 → 在 `scanners/patterns.py` 加一个模式，并在 `iter_text_findings` 中发出；
   - AST 相关（Python）→ 在 `scanners/python_scanner.py` 中处理；
   - shell 相关 → 在 `scanners/bash_scanner.py` 中处理。
3. 若需要配置（新的允许列表、阈值等），把字段加到 `policy.py`，并在
   `tool_safety_policy.yaml` 中记录说明。
4. 在 `tests/tools/safety/` 中补充测试。

---

## 文件结构

```
examples/tool_safety_guard/
├── README.md                 # 本文件
├── tool_safety_policy.yaml   # 示例策略（可热加载）
├── samples/                  # 12 个示例脚本 + EXPECTED.json
├── run_scan.py               # 批量扫描示例、打印验收指标
├── run_with_filter.py        # 演示：Filter 在高风险工具运行前阻断它
├── tool_safety_report.json   # 示例报告输出（由 run_scan.py 生成）
└── tool_safety_audit.jsonl   # 示例审计输出（由 run_scan.py 生成）

trpc_agent_sdk/tools/safety/  # 核心子包
scripts/tool_safety_check.py  # CLI
tests/tools/safety/           # 单元测试 + 验收测试
```

## 快速开始

```bash
# 批量扫描 12 个示例并打印验收指标。
python examples/tool_safety_guard/run_scan.py

# 通过 Filter 演示执行前阻断。
python examples/tool_safety_guard/run_with_filter.py

# 运行测试套件。
pytest tests/tools/safety/ -v
```

---

<a id="english"></a>

# Tool Script Safety Guard (English)

An **execution-time policy gate plus observability** for tool / skill /
`CodeExecutor` payloads in tRPC-Agent. Before a script or command actually runs,
the guard statically scans it and returns one of three decisions —
`allow` / `deny` / `needs_human_review` — together with a structured report, an
auditable event and OpenTelemetry attributes.

> It is a **pre-execution gate**, the front leg of defence-in-depth.
> It is **not** a sandbox and does **not** provide runtime isolation. See
> [Why it cannot replace a sandbox](#6-relationship-with-sandbox--filter--telemetry--codeexecutor).

---

## 1. What it is / what it is not

| It **is** | It **is not** |
|---|---|
| A static, pre-execution policy gate (Filter / wrapper) | A runtime sandbox / container |
| A structured report + audit log + OTel span producer | A resource limiter (CPU / memory / timeout) |
| A fast, allow-list-driven, fail-safe decision maker | A guarantee against obfuscated / dynamic payloads |

Static text analysis can be fooled (see [Known limitations](#7-known-limitations)).
Real isolation of runtime behaviour is the **sandbox's** job. The two are
complementary, not interchangeable.

---

## 2. Rule system

### Six risk categories

| Category | `RiskType` | Examples |
|---|---|---|
| Dangerous file operations | `DANGEROUS_FILE_OP` | `rm -rf /`, `shutil.rmtree("/")`, `chmod 777` |
| Network egress | `NETWORK_EGRESS` | request to a non-allow-listed host, internal IP |
| Process / command execution | `PROCESS_EXEC` | `subprocess`, `os.system`, shell injection, `eval` |
| Dependency installation | `DEPENDENCY_INSTALL` | `pip install`, `npm install`, `curl ... \| bash` |
| Resource abuse | `RESOURCE_ABUSE` | `while True`, fork bomb, very long `sleep` |
| Secret leakage | `SECRET_LEAK` | reading `~/.ssh/id_rsa` / `.env`, printing `api_key` |

### `rule_id` naming

`rule_id` is `<CATEGORY>_<ACTION>_<OBJECT>` in UPPER_SNAKE_CASE. Prefixes:
`FILE` / `SECRET` / `NET` / `EXEC` / `PRIV` / `PKG` / `RES`. Examples:
`FILE_RM_RF`, `SECRET_READ_SSH`, `NET_EGRESS_NON_ALLOWLIST`, `EXEC_SHELL_INJECTION`,
`PKG_CURL_PIPE_SH`, `RES_INFINITE_LOOP`.

### Three-level decision aggregation

Each rule carries a `risk_level` (`low/medium/high/critical`) and a
`suggested_action` (`allow/review/deny`). A finding's decision is the **more
severe** of its action and the decision its level maps to via
`decision_thresholds`. The report decision is the most severe finding decision:

```
finding with CRITICAL/HIGH and action=deny  -> DENY
any finding with action=deny                -> DENY
any finding with MEDIUM or action=review    -> NEEDS_HUMAN_REVIEW
otherwise                                   -> ALLOW
```

The three **must-catch** categories — secret read, dangerous delete and
non-allow-listed egress — are fixed at `CRITICAL + DENY` in `rules.py`, so they
deny under any reasonable threshold tuning.

> `needs_human_review` does **not** block execution; it flags the call for an
> out-of-band human decision. Only `deny` blocks. This is also why the
> false-positive metric counts only `safe -> deny`.

---

## 3. Integration (four ways)

### a) As a Tool Filter

```python
from trpc_agent_sdk.tools.safety import ToolSafetyFilter  # registers "tool_safety_guard"
from trpc_agent_sdk.tools import register_tool

@register_tool("Bash", filters_name=["tool_safety_guard"])
class MyBashTool(...):
    ...
```

On a `deny`, the filter returns a blocked result with `is_continue=False`
**without calling the tool body**, and writes one audit event.

### b) `SafeBashTool` wrapper

```python
from trpc_agent_sdk.tools.safety import SafeBashTool

tool = SafeBashTool(cwd="/work", audit_path="tool_safety_audit.jsonl")
```

A `BashTool` subclass that scans the command before the real
`asyncio.create_subprocess_shell` call.

### c) `guard_code_executor` wrapper

```python
from trpc_agent_sdk.tools.safety import guard_code_executor

guarded = guard_code_executor(my_code_executor, audit_path="audit.jsonl")
```

Scans each code block before delegating to the inner `BaseCodeExecutor`.

### d) CLI

```bash
python scripts/tool_safety_check.py examples/tool_safety_guard/samples \
  --policy examples/tool_safety_guard/tool_safety_policy.yaml \
  --report tool_safety_report.json \
  --audit tool_safety_audit.jsonl
```

Exits non-zero when anything is denied (`--fail-on deny|review|never`).

---

## 4. Policy file (`tool_safety_policy.yaml`)

All behaviour is tuned here — **no code change required** (acceptance 6).

| Field | Meaning |
|---|---|
| `allow_domains` | Egress allow-list (host or sub-domain). Anything else is non-allow-listed egress. |
| `allowed_commands` | Bash base-commands considered acceptable; others are flagged for review. |
| `forbidden_paths` | Paths that must not be read/written. |
| `max_timeout` / `max_output_size` | Runtime budgets surfaced in the report (enforced by the sandbox). |
| `decision_thresholds` | Maps each risk level to the decision it escalates to. |
| `param_keys` | Tool-name keyword → which arg keys to scan and with which scanner. |
| `redact` | Evidence masking (toggle, mask string, extra patterns). |
| `scan_limits` | `max_input_size` / `max_line_length` — bounds that protect the scanner (ReDoS / OOM). |

Resolution order: explicit `load_policy(path=...)` → `TOOL_SAFETY_POLICY_PATH`
env var → built-in default. An explicitly requested file that is missing,
malformed or invalid **fails fast**; the built-in default never raises.

Hot-reload examples:

```yaml
allow_domains: [api.example.com]   # add a trusted host -> its requests become allow
forbidden_paths: [/etc, ~/.ssh]    # tighten which paths are off-limits
allowed_commands: [ls, cat, git]   # widen/narrow acceptable bash commands
```

---

## 5. Report / audit / OTel fields

**Structured report** (`SafetyReport.to_dict()`) — the five required elements:

```json
{
  "tool_name": "02_dangerous_delete.py",
  "language": "python",
  "decision": "deny",
  "risk_level": "critical",
  "redacted": false,
  "scan_duration_ms": 0.1,
  "findings": [
    {
      "rule_id": "FILE_RM_RF",
      "risk_type": "dangerous_file_op",
      "risk_level": "critical",
      "evidence": {"snippet": "shutil.rmtree(\"/\")", "line": 6},
      "recommendation": "Recursive force-delete is destructive...",
      "suggested_action": "deny"
    }
  ]
}
```

**Audit log** (`tool_safety_audit.jsonl`, one JSON object per line): `timestamp`,
`tool_name`, `language`, `decision`, `risk_level`, `rule_id`, `rule_ids`,
`finding_count`, `scan_duration_ms`, `redacted`, `blocked`.

**OTel span attributes** (set on the active span when tracing is enabled,
silently skipped otherwise): `tool.safety.decision`, `tool.safety.risk_level`,
`tool.safety.rule_id`, `tool.safety.rule_ids`, `tool.safety.blocked`,
`tool.safety.redacted`.

---

## 6. Relationship with sandbox / Filter / Telemetry / CodeExecutor

The issue background describes a complete chain — **scan before, isolate during,
audit after**. The guard owns the *before* and *after* legs:

| Stage | Defence | Owner | Covered here? |
|---|---|---|---|
| **Before** | Static scan + decision (allow/deny/review) | **This guard** (Filter / wrapper) | ✅ yes |
| **During** | Resource limits (timeout/cgroup/memory) + isolation | **CodeExecutor sandbox** (container / E2B) | ❌ no — delegated to the sandbox |
| **After** | Audit log + metrics + tracing | **This guard** (`audit.jsonl` + OTel span) | ✅ yes |

- **vs Filter**: the guard *is* a specialised Tool Filter; it plugs into the
  existing `BaseTool` → `FilterRunner` pipeline and gates in `run()` before
  `handle()`.
- **vs Telemetry**: the guard emits its decision as span attributes / audit
  records that the telemetry stack exports to monitoring.
- **vs CodeExecutor**: `guard_code_executor` wraps an executor to scan code
  before it runs; the executor's sandbox still does the runtime isolation.

### Why it cannot replace a sandbox

The guard performs **static text analysis**, which can only see what is
*literally written*. It cannot constrain **runtime behaviour**: a payload that
builds a command dynamically, decodes base64 then `exec`s it, or simply consumes
all memory in a loop with a runtime-computed bound will slip past static rules.
Only a sandbox enforcing `timeout` / `cgroup` / memory caps / syscall limits can
contain those. The guard is the fast, auditable first gate; the sandbox is the
real containment. **Defence in depth, not a substitute.**

---

## 7. Known limitations

- **False positives**: a dangerous-looking string in a comment or unreachable
  branch (e.g. `# never run rm -rf /`) can be flagged even though it never runs.
- **False negatives (evasion)**: dynamic construction
  (`getattr(os, "sys" + "tem")`), base64/`eval` decoding, obfuscation, indirect
  calls, sub-shells, or "write a file then `source` it" can bypass static rules.
- **Resource abuse is the weakest category**: infinite loops, fork bombs, huge
  writes and long sleeps are fundamentally *runtime* behaviours. The guard only
  catches literally-visible patterns (`while True`, fork-bomb syntax, constant
  large `sleep`); a runtime-computed bound will be missed. **Real resource
  exhaustion must be contained by the sandbox.**
- **Bare sockets are not resolved**: egress detection relies on URL literals and
  downloader commands (`curl`/`wget`, ...). A connection such as
  `socket.connect(("evil.com", 80))` with no `http(s)://` prefix and a
  variable host will not hit the `NET_*` rules. Enforce host-level egress
  control at the sandbox layer if you need to cover it.
- **`forbidden_paths` is matched literally (with `~` expansion)**: forbidden-path
  detection does a boundary-anchored literal match per line (expanding `~` to the
  home directory), so a path assembled at runtime (`os.path.join(base, "etc")`)
  or passed via a variable is missed; the overly-broad `/` entry is skipped to
  avoid false positives (root deletion is already covered by `FILE_RM_RF`). Hits
  under `/dev`, `/proc`, `/sys` are classified as `FILE_OVERWRITE_DEVICE`, the
  rest as `FILE_FORBIDDEN_PATH`.

These limits are inherent to static analysis and are the reason the guard is
positioned in front of — never instead of — a sandbox.

---

## 8. How to extend rules

1. Add a `RuleSpec` to `trpc_agent_sdk/tools/safety/rules.py` (pick a `rule_id`,
   `RiskType`, `RiskLevel`, `SuggestedAction`, recommendation).
2. Add detection:
   - regex-detectable → add a pattern in `scanners/patterns.py` and emit it from
     `iter_text_findings`;
   - AST-specific (Python) → handle it in `scanners/python_scanner.py`;
   - shell-specific → handle it in `scanners/bash_scanner.py`.
3. If it needs configuration (a new allow-list, threshold, etc.), add the field
   to `policy.py` and document it in `tool_safety_policy.yaml`.
4. Add a test in `tests/tools/safety/`.

---

## Files

```
examples/tool_safety_guard/
├── README.md                 # this file
├── tool_safety_policy.yaml   # example policy (hot-reloadable)
├── samples/                  # 12 sample scripts + EXPECTED.json
├── run_scan.py               # batch-scan the samples, print acceptance metrics
├── run_with_filter.py        # demo: filter blocks a high-risk tool before it runs
├── tool_safety_report.json   # example report output (generated by run_scan.py)
└── tool_safety_audit.jsonl   # example audit output (generated by run_scan.py)

trpc_agent_sdk/tools/safety/  # the core sub-package
scripts/tool_safety_check.py  # CLI
tests/tools/safety/           # unit + acceptance tests
```

## Quick start

```bash
# Batch-scan the 12 samples and print acceptance metrics.
python examples/tool_safety_guard/run_scan.py

# Demonstrate pre-execution blocking via the Filter.
python examples/tool_safety_guard/run_with_filter.py

# Run the test suite.
pytest tests/tools/safety/ -v
```
