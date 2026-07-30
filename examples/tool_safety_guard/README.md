# Tool Script Safety Guard（工具脚本安全扫描 / 拦截 / 监控）

在 Agent 执行任意脚本/命令**之前**做静态安全扫描，输出三态结论
（`allow` / `deny` / `needs_human_review`），并以 **Filter** 或 **CodeExecutor 装饰器**
两种零侵入方式接入框架，同时产出结构化报告、审计 JSONL 与 OpenTelemetry 埋点。

> 对应 issue #90：构建 Tool 执行脚本安全扫描、Filter 拦截与监控机制。

## 为什么需要它

LLM 驱动的 Agent 会自行生成 `BashTool` 命令或 `CodeExecutor` 代码。一旦模型被诱导
（提示注入）或自身产生幻觉，就可能执行 `rm -rf /`、读取 `~/.ssh/id_rsa`、把数据
`curl` 到外部域名。本模块在“生成”与“执行”之间加一道**可解释、可配置、可观测**的静态闸门，
把明确高危的操作挡下、把不确定的操作转人工复核，而不是一律放行。

## 三层漏斗式扫描管线

```
脚本/命令 (ScanInput)
        │
        ▼
┌─ L1 正则快路径 ────────────┐  策略 YAML 中的 regex 规则，逐行匹配，毫秒级
│  规则即数据，改规则不改代码 │  → 保证 500 行脚本 < 1s
└──────────┬─────────────────┘
           ▼
┌─ L2 语法感知层 ────────────┐  对抗“正则绕过”的核心
│  Python: ast 别名追踪 /     │  import subprocess as sp、getattr 混淆、eval/exec
│  Bash:   shlex 结构化解析   │  $(...)|bash、base64 -d|sh、curl|bash、域名白名单
└──────────┬─────────────────┘
           ▼
┌─ L3 决策融合 ──────────────┐  借鉴 Claude Code：deny > review > allow
│  critical/high → deny       │  任一 medium → needs_human_review
│  否则 → allow（不确定不放行）│  网络类命中再做域名感知二次研判
└──────────┬─────────────────┘
           ▼
  ScanReport + 审计 JSONL + OTel span attributes
```

- **L1 正则层**：来自策略文件的声明式规则，逐行 `re.search`，是保证性能的快路径。
- **L2 语法感知层**：把脚本解析成结构再判断，专门对付纯字符串匹配漏掉的混淆写法。
- **L3 决策融合**：按最高风险等级聚合；网络命中会用允许域名列表再研判（见下）。

## 规则体系

规则**全部是数据**，声明在
[`_default_policy.yaml`](../../trpc_agent_sdk/tools/safety/_default_policy.yaml)（内置默认）
或你自己的策略文件里。每条规则含 `rule_id` / `category` / `risk_level` / `title` /
`pattern` / `language` / `recommendation`。六类风险与代表规则：

| 类别 | 说明 | 代表规则 |
| --- | --- | --- |
| `dangerous_file_operation` | 危险文件操作 | `FS001` 递归删除根/家目录、`FS003` dd 写块设备 |
| `network_exfiltration` | 网络外连 | `NET001` curl/wget/requests、`NET002` netcat 反弹 shell、`SH021` 非白名单域 |
| `process_system_command` | 进程/系统命令 | `PS001` sudo 提权、`PS002` 管道进解释器、`AST001` subprocess、`AST002` eval/exec |
| `dependency_install` | 依赖安装 | `DEP001` pip、`DEP002` npm/yarn、`DEP003` apt/yum |
| `resource_abuse` | 资源滥用 | `RES001` fork bomb、`AST005` 无 break 死循环 |
| `sensitive_info_leak` | 敏感信息泄漏 | `CR001` SSH 私钥、`CR003` 云/集群凭证、`SEC001` 硬编码密钥 |

- 风险等级语义：`critical`/`high` → 拒绝；`medium` → 人工复核；`low` → 仅记录不单独拦截。
- `AST*`（Python）与 `SH*`（Bash）由 L2 语法层产生，`layer="ast"`；其余为 L1 正则规则，`layer="regex"`。
- 策略里还有 `allowed_domains`（网络白名单，支持子域名）、`allowed_commands`、
  `forbidden_paths`、`max_timeout_seconds`、`max_output_bytes`、`redact_sensitive`、`ast_analysis`。

### 网络域名感知研判

网络类命中（`NET001`/`AST004`/`SH02x`）不是简单“见 curl 就拦”，而是提取目标域名后：

- 目标全部在 `allowed_domains` 白名单内 → **丢弃命中**（视为放行）；
- 出现非白名单域名 → 保持 `high` → **拒绝**；
- 目标是变量、静态无法确定 → 降级为 `medium` → **人工复核**（不确定不放行）。

## 接入方式（两种，零核心侵入）

### 方式一：Tool Filter（推荐用于 BashTool 等工具）

`import trpc_agent_sdk.tools.safety` 即完成 `@register_tool_filter("tool_safety_guard")` 注册，
之后按名字挂到任意携带脚本参数的工具上：

```python
import trpc_agent_sdk.tools.safety  # 注册 "tool_safety_guard" 过滤器
from trpc_agent_sdk.tools import BashTool

bash_tool = BashTool(cwd=".", filters_name=["tool_safety_guard"])
```

命中拦截时 Filter 返回 `FilterResult(is_continue=False)`，底层工具**根本不会执行**，
模型收到一段结构化拒绝说明（含决策、风险等级、触发规则、报告 JSON）。
见 [`agent/agent.py`](./agent/agent.py) 与 [`run_agent.py`](./run_agent.py)。

### 方式二：SafeCodeExecutor 装饰器（用于 CodeExecutor）

包装**任意** `BaseCodeExecutor`，逐个 code block 先扫描后执行：

```python
from trpc_agent_sdk.code_executors import UnsafeLocalCodeExecutor
from trpc_agent_sdk.tools.safety import SafeCodeExecutor

executor = SafeCodeExecutor(inner=UnsafeLocalCodeExecutor())
```

任一 block 被拦截则整批拒绝，内层 executor 不被调用，返回失败的
`CodeExecutionResult`（`output` 携带拒绝原因）。

两个接入点共用同一个 `SafetyScanner` 与 `SafetyAuditLogger`，只是挂载面不同。

## 可观测：审计与遥测

决策在**同一处**以两种 agent-native 方式产出（借鉴 Codex 把审计与遥测就地埋点）：

- **审计 JSONL**：每条决策一行，含 `timestamp` / `tool_name` / `language` / `decision` /
  `risk_level` / `rule_ids` / `num_hits` / `blocked` / `redacted` / `duration_ms`。
- **OpenTelemetry span attributes**：`tool.safety.decision` / `tool.safety.risk_level` /
  `tool.safety.rule_id` / `tool.safety.blocked`，直接挂在当前工具 span 上。

审计与遥测都是 best-effort，绝不把异常抛进执行路径——监控不能拖垮它保护的工具。
开启脱敏（`redact_sensitive: true`）后，报告与审计里的密钥样式子串会被替换为 `***`。

## 运行示例

```bash
cd examples/tool_safety_guard
python main.py            # 批量扫描 samples/ 下 12 个样例
```

`main.py` 会读取 [`tool_safety_policy.yaml`](./tool_safety_policy.yaml)（含一条自定义规则
`CUSTOM001`，演示“改策略不改代码”），扫描 [`samples/`](./samples) 全部 12 个样例，
生成：

- `tool_safety_report.json`：每个样例的完整结构化报告。
- `tool_safety_audit.jsonl`：逐条审计事件。

> 以上两个文件由 `main.py` 运行时生成，不纳入版本库。

12 个样例覆盖：安全 Python、危险删除、读 SSH 私钥、网络外连、白名单请求、
subprocess 别名混淆、shell 注入、依赖安装、无限循环、硬编码密钥、base64 管道、人工复核场景，
预期约 8 条 `deny`、2 条 `needs_human_review`、2 条 `allow`。

运行接入 Agent 的实时拦截 Demo（需先在 `.env` 配置模型）：

```bash
python run_agent.py       # 第 1 条安全查询放行；第 2 条 rm -rf / 被执行前拦截
```

## 关键文件

| 文件 | 职责 |
| --- | --- |
| [`_types.py`](../../trpc_agent_sdk/tools/safety/_types.py) | 数据模型：Decision/RiskLevel/Category/RuleHit/ScanInput/ScanReport |
| [`_policy.py`](../../trpc_agent_sdk/tools/safety/_policy.py) | 策略 pydantic 模型 + YAML 加载/校验 |
| [`_default_policy.yaml`](../../trpc_agent_sdk/tools/safety/_default_policy.yaml) | 内置默认规则集（六类风险） |
| [`_python_scanner.py`](../../trpc_agent_sdk/tools/safety/_python_scanner.py) | L2 Python AST 扫描（别名追踪、eval/exec、敏感路径） |
| [`_bash_scanner.py`](../../trpc_agent_sdk/tools/safety/_bash_scanner.py) | L2 Bash shlex 结构化扫描（管道、base64、域名） |
| [`_scanner.py`](../../trpc_agent_sdk/tools/safety/_scanner.py) | L1→L2→决策融合编排器 |
| [`_guard_filter.py`](../../trpc_agent_sdk/tools/safety/_guard_filter.py) | `ToolSafetyGuardFilter` 接入点 |
| [`_executor_wrapper.py`](../../trpc_agent_sdk/tools/safety/_executor_wrapper.py) | `SafeCodeExecutor` 接入点 |
| [`_audit.py`](../../trpc_agent_sdk/tools/safety/_audit.py) | 审计 JSONL + OTel span attributes |

## 如何扩展新规则（无需改代码）

在你的策略 YAML 的 `rules:` 下新增一条即可，例如禁止访问某内部路径：

```yaml
rules:
  - rule_id: CUSTOM001
    category: sensitive_info_leak
    risk_level: high
    title: Access to internal secrets mount
    pattern: '/run/secrets/'          # Python 正则
    language: unknown                 # unknown = Python 与 Bash 都生效
    recommendation: 内部密钥挂载点不应被脚本读取。
```

保存后用 `load_policy("your_policy.yaml")` 构造 `SafetyScanner` 即刻生效。规则 id 必须唯一、
正则必须可编译，否则加载时立即报错（见 `tests/tools/safety/test_policy.py`）。
若要新增**结构级**检测（如新的 AST 模式），才需要在 `_python_scanner.py` / `_bash_scanner.py`
里加逻辑。

## 与沙箱的关系：这是静态闸门，不是沙箱替代品

本模块做的是**执行前的静态分析**，它降低风险但**不能替代** OS/容器级隔离：

- 静态分析**无法覆盖运行时动态行为**：从网络下载再执行、反射式动态拼接、编码/加密载荷、
  依赖包内的恶意代码等，在“执行前看源码”这个视角下天然存在盲区。
- 因此定位是**纵深防御的第一层**：先用它把明显高危挡在门外、把可疑的转人工，
  真正的执行仍应放在受限沙箱（容器、seccomp、只读文件系统、网络策略、资源配额）里。
- 二者互补：扫描器提供“可解释的拒绝理由 + 审计 + 遥测”，沙箱提供“即使放行也炸不穿”的兜底。

### 误报 / 漏报 / 绕过 的取舍

- **误报（把安全的当危险）**：正则/AST 可能过度匹配。缓解手段是白名单
  （域名/命令）、把不确定项设为 `medium` 走复核而非直接 `deny`、以及规则可由操作者按需下调。
- **漏报（把危险的当安全）**：新型混淆、多步组合、运行时才成型的命令可能逃逸。
  策略是“不确定不放行”（降级复核）+ 依赖后端沙箱兜底，而非假设扫描器能识别一切。
- **绕过**：攻击者可构造静态不可判定的输入。这正是保留 `needs_human_review` 态、
  并强调不能只靠本模块的原因——把它当作可观测的第一道闸，而不是唯一防线。

## 测试

```bash
pytest tests/tools/safety/ -q
```

覆盖：策略加载/校验/改策略生效、Python 与 Bash 各风险类别检出、别名/混淆变体、
安全样本不误报、决策融合、三类必检项（密钥读取 / 危险删除 / 非白名单外连）100% 检出、
500 行脚本 < 1s 性能断言、Filter 执行前拦截 + 审计落盘、SafeCodeExecutor 拒绝高危 block。
