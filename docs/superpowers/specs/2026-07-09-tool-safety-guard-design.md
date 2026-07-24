# Tool Script Safety Guard — 设计文档

- **日期**：2026-07-09
- **对应 issue**：[trpc-group/trpc-agent-python#90](https://github.com/trpc-group/trpc-agent-python/issues/90)「构建 Tool 执行脚本安全扫描、Filter 拦截与监控机制」
- **分支**：`feat/tool-script-safety-guard-90`
- **状态**：设计已与用户对齐，待实现

---

## 1. 目标与范围

### 目标（一句话）
在 Agent 的 Tool / Skill / CodeExecutor **真正执行脚本之前**，对脚本做静态安全扫描，产出 `Allow / Deny / NeedsReview` 决策，并在执行链前置位置拦截高危脚本。

### MVP 范围（本 spec 覆盖）
- Python（AST + import-as 别名追踪）与 Bash（shlex + 引号状态机）双语言扫描
- 三态决策聚合（保守策略）
- YAML 策略文件加载（含 strict 校验）
- 两种接入方式，均**不改核心源码、零回归**：
  - `ToolSafetyFilter`：外挂 Tool/Skill 执行链
  - `SafetyGuardedCodeExecutor`：包装任意 `BaseCodeExecutor`
- manifest 驱动的 ≥12 条测试样本 + 性能测试

### 实现约定
- **代码注释与 docstring 使用英文**，与 SDK 现有源码（`runners.py` / `_base_tool.py` / `_base_code_executor.py` 等）一致；本设计文档用中文撰写。
- 提交策略：spec 与代码暂不提交，待实现完成后统一处理。

### 明确不做（YAGNI / 后续迭代）
- 不改核心 `UnsafeLocalCodeExecutor` / `BashTool` 源码（用 wrapper 替代）
- 不做 policy 热更新
- 不做独立脱敏引擎（审计 evidence 仅做长度截断，不做敏感字段替换）
- 不做 OpenTelemetry 埋点（审计先用结构化日志，预留接口）
- 不做 Skill 专用 runner（Skill 经 Tool 走 Filter 即可覆盖）

---

## 2. 背景与对齐策略

### 2.1 issue #90 核心要求
覆盖 6 类风险：危险文件操作 / 网络外连 / 进程系统命令 / 依赖安装 / 资源滥用 / 敏感信息泄漏。
决策至少三态；可配置策略文件；以 Filter 或 wrapper 接入；输出结构化报告 + 审计事件；预留 OTel 埋点；明确误报/漏报/绕过风险。

### 2.2 与已有 6 个 PR 的关系
同 issue 的 6 人并行解法（均 open 未 merge）。本实现**站在它们肩膀上取最优拼装**：
- 接入范式 ← #113（wrapper / 默认关闭，向后兼容）
- 架构骨架 / rule_id ← #126（对齐 Go 官方参考实现）
- Python 精度 ← #103（AST + import-as 别名追踪）
- Bash 解析 ← #126（引号状态机）+ #103/#105（shlex 分词）
- 审计字段 ← #118 + #105（上下文字段）
- 测试组织 ← #113（manifest 驱动）

避开的坑：不放 examples/（#103）、Python 不纯正则（#126）、Bash 不裸正则（#108/#118）、覆盖率 ≥85%。

### 2.3 对齐 Go 参考实现
Go 版（`trpc-agent-go/tool/safety`，PR #2091 已合并）是官方参考。本实现对齐其**命名风格与类型骨架**：
- `rule_id` 前缀风格 `tool-<域>-<动作>`
- `Decision` / `RiskLevel` 整型枚举
- 入口签名 `scan(policy, script, language) → report`（对应 Go `ScanScript`）
- `Policy{Name,Description,Rules}` / `Rule{ID,RiskLevel,Decision,Config}`

**关键张力**：Go 的 `Decision` 是 `Undecided/Allowed/Blocked` 二态，而 issue 硬要求三态含 `needs_human_review`。
**解决**：保留 Go 命名与骨架，`Decision` 扩展为 `Undecided/Allow/Deny/NeedsReview` 四值（多出的 `NeedsReview` 满足 issue）。Go 仅 `tool-code/tool-fs/tool-net` 三域，本实现**扩展到 issue 全 6 类**（见 3.1）。

---

## 3. 架构与契约

### 3.1 rule_id 域映射（issue 6 类 → Go 风格前缀）
| 域前缀 | 覆盖风险 | 示例 rule_id |
|---|---|---|
| `tool-code-*` | 代码执行 | `tool-code-unsafe-eval`、`tool-code-unsafe-exec` |
| `tool-fs-*` | 危险文件操作 | `tool-fs-recursive-delete`、`tool-fs-read-credentials`（`~/.ssh`/`.env`/凭据） |
| `tool-net-*` | 网络外连 | `tool-net-http`、`tool-net-socket`（非白名单域名） |
| `tool-proc-*` | 进程/系统命令 | `tool-proc-subprocess`、`tool-proc-shell-pipe`、`tool-proc-privilege-escalation` |
| `tool-pkg-*` | 依赖安装 | `tool-pkg-install`（pip/npm/apt） |
| `tool-res-*` | 资源滥用 | `tool-res-infinite-loop`、`tool-res-fork-bomb`、`tool-res-long-sleep` |
| `tool-secret-*` | 敏感信息泄漏 | `tool-secret-logging`、`tool-secret-private-key` |

> `tool-code-* / tool-fs-* / tool-net-*` 与 Go 原版前缀一致；`tool-proc/pkg/res/secret-*` 为本实现按 issue 扩展。

### 3.2 核心类型（`_types.py`，对齐 Go）
```python
class Decision(IntEnum):
    UNDECIDED = 0      # 对齐 Go DecisionUndecided
    ALLOW = 1          # 对齐 Go DecisionAllowed
    DENY = 2           # 对齐 Go DecisionBlocked
    NEEDS_REVIEW = 3   # 扩展：满足 issue 三态

class RiskLevel(IntEnum):
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3           # 对齐 Go RiskLevelNone/Low/Medium/High

@dataclass
class Finding:
    rule_id: str
    risk_level: RiskLevel
    rule_decision: Decision      # 该规则自身建议的决策
    evidence: str                # 命中证据片段（截断，max_evidence_chars）
    recommendation: str
    language: str                # "python" | "bash"

@dataclass
class SafetyReport:
    decision: Decision
    risk_level: RiskLevel        # 所有命中的最高风险级
    findings: list[Finding]
    recommendation: str
    scan_duration_ms: int
    sanitized: bool              # MVP 固定 False（脱敏引擎未实现）
```

### 3.3 Policy / Rule（`_policy.py`，对齐 Go 结构 + YAML 加载）
```python
@dataclass
class Rule:
    id: str                      # rule_id
    risk_level: RiskLevel
    decision: Decision           # 该规则命中时的决策
    config: dict[str, str]       # 规则级参数（对齐 Go Rule.Config）

@dataclass
class Policy:
    name: str
    description: str
    rules: list[Rule]
    # 全局兜底配置
    whitelisted_domains: list[str]
    allowed_commands: list[str]
    denied_paths: list[str]
    max_timeout_seconds: int
    max_output_bytes: int
    deny_risk_level: RiskLevel   # 阈值：风险 ≥ 此级 → Deny
    review_risk_level: RiskLevel # 阈值：风险 ≥ 此级（但 <deny）→ NeedsReview
    max_evidence_chars: int

def load_policy(path: str | Path) -> Policy: ...   # YAML → Policy，含 strict 校验
```

---

## 4. 目录结构
```
trpc_agent_sdk/tools/safety/
  __init__.py                 # 导出 SafetyScanner/ToolSafetyFilter/SafetyGuardedCodeExecutor/load_policy
  _types.py                   # Decision/RiskLevel/Finding/SafetyReport
  _policy.py                  # Rule/Policy + load_policy + strict 校验
  _rules.py                   # rule_id 常量表 + 每域扫描函数
  _python_scanner.py          # AST + import-as 别名追踪
  _shell_parse.py             # shlex + 引号状态机
  _bash_scanner.py            # 基于 shell_parse 的风险检测
  _decision.py                # aggregate(findings, policy) → SafetyReport
  _safety_filter.py           # ToolSafetyFilter（@register_tool_filter）
  _code_executor_guard.py     # SafetyGuardedCodeExecutor wrapper
  tool_safety_policy.yaml     # 默认策略示例
scripts/tool_safety_check.py  # CLI 扫单脚本
tests/tools/safety/
  samples/manifest.yaml       # ≥12 样本：script+language+expected_decision+required_rule_ids
  test_python_scanner.py
  test_bash_scanner.py
  test_decision.py
  test_policy.py
  test_safety_filter.py
  test_code_executor_guard.py
  test_performance.py
```

---

## 5. 数据流
```
脚本（tool args / command / CodeBlock）
  → 识别语言（python / bash；启发式：含 ```python 标记或 Python 关键字 → python，否则按 bash 处理）
  → scanner: AST（python）或 shell_parse（bash）
  → List[Finding(rule_id, risk_level, rule_decision, evidence, recommendation, language)]
  → aggregate(findings, policy) → SafetyReport
  → Decision==DENY：
        Filter → 不调 handle()，返回 FilterResult(rsp={"error":"TOOL_SAFETY_BLOCKED",...}, is_continue=False)
        Executor wrapper → 跳过该 CodeBlock，返回 CodeExecutionResult(stderr="TOOL_SAFETY_BLOCKED: ...")
  → Decision==NEEDS_REVIEW：MVP 同 Deny 行为（保守拦截，记录需人工复核）
  → 记审计（结构化日志；jsonl/OTel 接口预留，MVP 不实现）
```

---

## 6. 核心组件与接口

### 6.1 扫描器
```python
def scan(policy: Policy, script: str, language: str, meta: dict | None = None) -> SafetyReport:
    """统一扫描入口（对应 Go ScanScript）。language ∈ {"python","bash","auto"}。"""
```
- **Python**（`_python_scanner.py`）：`ast.parse` + `ast.walk`；维护 `aliases`（解析 `import os as x` / `from os import system as s`，把 `x.system()`、`s()` 还原到目标），检测危险调用、危险文件路径、网络外连（从 Call 参数提取 URL 字面量比对白名单）、无限循环、敏感输出。
  AST 解析失败 → 降级字符串启发式（记录但不阻塞）。
- **Bash**（`_bash_scanner.py` + `_shell_parse.py`）：`shlex.split(posix=True)` 分词 + 引号状态机判定 `|`/`&&`/`&`/`>`（避免 `echo "a|b"` 误判为管道）；单独检测 `base64 -d | sh`、`sh -c`/`bash -c`、反引号/`$()`、fork bomb、长 sleep。

### 6.2 决策聚合（`_decision.py`，保守 + 双轨）
```python
def aggregate(findings: list[Finding], policy: Policy) -> SafetyReport:
    """规则级判定优先，policy 阈值兜底。"""
```
逻辑：
1. 任一 Finding 的 `rule_decision == DENY`，或其 `risk_level >= policy.deny_risk_level` → `Decision.DENY`
2. 否则任一 `rule_decision == NEEDS_REVIEW`，或 `risk_level >= policy.review_risk_level` → `Decision.NEEDS_REVIEW`
3. 否则 `Decision.ALLOW`
> 满足 issue"不能把不确定情况都直接放行"。

### 6.3 接入一：ToolSafetyFilter（`_safety_filter.py`）
```python
@register_tool_filter("tool_safety")
class ToolSafetyFilter(BaseFilter):
    async def run(self, ctx, req: dict, handle) -> FilterResult:
        script, language = extract_script_from_args(req)   # 识别 code/command/script/file 等字段
        report = scan(policy, script, language, meta={"tool_name": ...})
        if report.decision == Decision.ALLOW:
            return await handle()
        # DENY / NEEDS_REVIEW：拦截
        return FilterResult(rsp={"success": False, "error": "TOOL_SAFETY_BLOCKED",
                                 "decision": report.decision.name, "rule_ids": [...]},
                            is_continue=False)
```
- 策略来源：环境变量 `TRPC_AGENT_TOOL_SAFETY_POLICY` 指定路径，缺省用内置默认策略。
- 脚本提取：从 tool args 常见字段（`code`/`command`/`script`/`cmd`/`file`）取内容，无法识别则放行（不误伤普通 Tool）。

### 6.4 接入二：SafetyGuardedCodeExecutor（`_code_executor_guard.py`）
```python
class SafetyGuardedCodeExecutor(BaseCodeExecutor):
    def __init__(self, delegate: BaseCodeExecutor, policy: Policy, block_on_review: bool = True):
        self._delegate = delegate
        ...
    async def execute_code(self, invocation_context, code_execution_input) -> CodeExecutionResult:
        # 逐 CodeBlock 扫描；DENY（或 block_on_review 时的 REVIEW）→ 跳过该 block，返回 blocked 结果
        # ALLOW → 委托 delegate.execute_code
```
- 用户显式包装才启用（`SafetyGuardedCodeExecutor(UnsafeLocalCodeExecutor(...))`），零核心改动。

---

## 7. 子方案决策（已确认）
- **⑤A 规则组织 = 方案 A**：每域一个扫描函数 + `rule_id` 常量表集中定义。最简、文件少。新增规则 = 加一个检测分支 + 一条常量。不做插件注册表（YAGNI）。
- **⑤B 决策判定 = 双轨**：规则级 `rule_decision` 优先 + policy 阈值（`deny_risk_level`/`review_risk_level`）兜底。

---

## 8. 验收标准对齐
| issue 验收 | 本实现落点 |
|---|---|
| #1 12 样本可扫描出结构化报告 | `manifest.yaml` + 参数化测试 |
| #2 高危检出率 ≥90% / 误报率 ≤10% | manifest 覆盖正负样本，测试断言 |
| #3 读密钥/危险删除/非白名单外连 100% | 强制 Deny 用例（测试硬断言） |
| #4 单个 500 行脚本 ≤1s | `test_performance.py` |
| #5 报告含 decision/risk_level/rule_id/evidence/recommendation | `SafetyReport` / `Finding` 字段 |
| #6 改配置不改代码 | YAML `load_policy` |
| #7 Filter/wrapper 执行前拒绝 + 审计事件 | `ToolSafetyFilter` 不调 handle；wrapper 跳过 block；审计日志 |
| #8 文档说明与沙箱/Filter/Telemetry/CodeExecutor 关系 | README 章节（含"为何不能替代沙箱"） |

---

## 9. 测试策略
- **manifest 驱动**（`tests/tools/safety/samples/manifest.yaml`）：≥12 样本，每条声明 `script` / `language` / `expected_decision` / `required_rule_ids`；参数化测试自动遍历。
- 12 样本对应 issue 场景：安全 Python、危险删除、读取密钥、网络外连、白名单网络、subprocess、shell 注入、依赖安装、无限循环、敏感信息输出、Bash 管道、人工复核。
- **红线断言**：读密钥 / 危险删除 / 非白名单外连三类样本，`expected_decision == DENY`（硬断言，非概率）。
- **性能**：`test_performance.py` 生成 500 行脚本，断言 `scan_duration_ms < 1000`。
- **覆盖率目标 ≥85%**（pytest + pytest-asyncio，`asyncio_mode=auto` 已在 pyproject 配置）。

---

## 10. 已知限制（须写入 README）
1. **静态扫描固有局限**：混淆 / 编码绕过（如 `base64 -d | sh`）、动态拼接、间接调用（`getattr(os,"system")(...)`）、反射等可漏报。
2. **不能替代沙箱隔离**：本机制是"执行前静态策略判断"，运行时资源限制/环境隔离仍须靠 CodeExecutor 容器或沙箱。这正是选 wrapper、不改核心源码的原因。
3. 存在误报（合法脚本命中模式）与漏报（新型绕过）。policy 阈值与白名单是主要的误报调优手段。
4. Bash 解析为启发式（shlex + 状态机），非完整 POSIX shell 解析器；复杂引用/转义边界可能误判。

---

## 11. 未来迭代（非本 MVP）
- policy 热更新（文件 watch + reload）
- 独立脱敏引擎（私钥/AWS Key/Bearer 字段替换）
- OpenTelemetry span 属性（`tool.safety.decision/risk_level/rule_id/scan_duration_ms/sanitized/blocked`）
- Skill 专用 runner、MCP 工具适配
- 可选：改核心 `UnsafeLocalCodeExecutor` 加 `enable_safety_guard` 字段（默认关）以自动生效（需配 `test_core_integration` 回归）

---

## 12. 建议实现顺序（供 writing-plans 参考）
1. `_types.py`（Decision/RiskLevel/Finding/SafetyReport）—— 无依赖，可先测
2. `_policy.py`（Rule/Policy + `load_policy` + 默认 YAML）
3. `_decision.py`（`aggregate`）+ `test_decision.py`
4. `_shell_parse.py` + `_bash_scanner.py` + `test_bash_scanner.py`
5. `_python_scanner.py`（含别名追踪）+ `test_python_scanner.py`
6. `scan()` 统一入口 + `samples/manifest.yaml`（≥12 样本）+ 参数化测试 + `test_performance.py`
7. `_safety_filter.py` + `test_safety_filter.py`
8. `_code_executor_guard.py` + `test_code_executor_guard.py`
9. `scripts/tool_safety_check.py` CLI
10. `tool_safety_policy.yaml` 默认策略 + README/设计说明（中英）
```
