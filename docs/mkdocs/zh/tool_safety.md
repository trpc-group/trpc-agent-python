# Tool Script Safety Guard（工具脚本安全防护）

Tool Script Safety Guard 是 tRPC-Agent-Python SDK 中的**静态脚本安全扫描模块**，用于在 Agent 的 Tool / Skill / CodeExecutor **真正执行脚本之前**，对脚本做静态安全扫描，产出 `Allow / Deny / NeedsReview` 决策，并在执行链前置位置拦截高危脚本。

## 核心概念

### 是什么与为什么

**Tool Script Safety Guard** 是一个**静态预执行脚本扫描器**，而非运行时沙箱。它在代码执行**之前**分析脚本内容，通过以下方式降低安全风险：

- **静态分析**：无需运行脚本即可检测危险模式（如危险文件操作、网络外连、敏感信息泄漏等）
- **零侵入**：通过 Filter 或 Wrapper 接入，不修改核心源码，完全向后兼容
- **双语言支持**：同时支持 Python（AST + import-as 别名追踪）和 Bash（shlex + 引号状态机）
- **保守策略**：默认采用保守决策，对不确定情况倾向于拦截而非放行

**重要**：本机制是"执行前静态策略判断"，**不能替代沙箱隔离**。运行时资源限制、环境隔离仍须依靠 CodeExecutor 的容器或沙箱机制。这正是选择 wrapper、不改核心源码的原因。

### 快速开始

#### 方式一：使用 `ToolSafetyFilter` 拦截 Tool/Skill 执行

```python
from trpc_agent_sdk.tools.safety import ToolSafetyFilter

# 在 Tool 定义时添加安全过滤器
class MyTool(BaseTool):
    # 方法1：通过 filters_name 参数附加
    filters_name = ["tool_safety"]  # 自动注册的 filter 名称
    
    # 方法2：通过 add_one_filter 动态附加
    def __init__(self):
        super().__init__()
        self.add_one_filter("tool_safety")
    
    async def _run_async_impl(self, **kwargs):
        # 当 kwargs 包含 script/code/command 等字段时
        # 会先经过安全扫描，决策为 ALLOW 才会执行到这里
        code = kwargs.get("code", "")
        return await execute_some_script(code)
```

#### 方式二：使用 `SafetyGuardedCodeExecutor` 包装 CodeExecutor

```python
from trpc_agent_sdk.code_executors import UnsafeLocalCodeExecutor
from trpc_agent_sdk.tools.safety import SafetyGuardedCodeExecutor

# 包装任意 CodeExecutor，自动扫描每个 code block
original_executor = UnsafeLocalCodeExecutor()
guarded_executor = SafetyGuardedCodeExecutor(
    delegate=original_executor,
    block_on_review=True  # NEEDS_REVIEW 决策是否拦截，默认 True
)

# 使用 guarded_executor，危险代码块会被跳过
result = await guarded_executor.execute_code(
    invocation_context=context,
    input_data=CodeExecutionInput(
        code="import os; os.system('rm -rf /')",  # 危险代码
        language="python"
    )
)
# result.stderr 会包含 "TOOL_SAFETY_BLOCKED [python] DENY (...)"
```

### rule_id 规则域映射

系统覆盖 **7 大风险域**，共 18 条规则（对齐 `trpc-agent-go/tool/safety` 风格）：

| 域前缀 | 覆盖风险 | 示例 rule_id |
|--------|----------|-------------|
| `tool-code-*` | 代码执行 | `tool-code-unsafe-eval`、`tool-code-unsafe-exec`、`tool-code-unsafe-import` |
| `tool-fs-*` | 危险文件操作 | `tool-fs-recursive-delete`、`tool-fs-read-credentials`、`tool-fs-system-dir-write` |
| `tool-net-*` | 网络外连 | `tool-net-http`、`tool-net-socket` |
| `tool-proc-*` | 进程/系统命令 | `tool-proc-subprocess`、`tool-proc-shell-pipe`、`tool-proc-privilege-escalation` |
| `tool-pkg-*` | 依赖安装 | `tool-pkg-install`（pip/npm/apt） |
| `tool-res-*` | 资源滥用 | `tool-res-infinite-loop`、`tool-res-fork-bomb`、`tool-res-long-sleep` |
| `tool-secret-*` | 敏感信息泄漏 | `tool-secret-logging`、`tool-secret-private-key` |

### 决策与风险级别

#### Decision（决策）

```python
class Decision(IntEnum):
    UNDECIDED = 0      # 未确定（规则未覆盖）
    ALLOW = 1          # 允许执行
    DENY = 2           # 拒绝执行
    NEEDS_REVIEW = 3   # 需要人工复核
```

#### RiskLevel（风险级别）

```python
class RiskLevel(IntEnum):
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
```

#### 决策聚合逻辑（保守策略）

系统采用**双轨判定**：规则级判定优先 + policy 阈值兜底。

1. **任一 Finding 的 `rule_decision == DENY`**，或其 `risk_level >= policy.deny_risk_level` → `Decision.DENY`
2. **否则任一 `rule_decision == NEEDS_REVIEW`**，或 `risk_level >= policy.review_risk_level` → `Decision.NEEDS_REVIEW`
3. **否则** → `Decision.ALLOW`

默认阈值：
- `deny_risk_level: HIGH`        # 风险 ≥ HIGH → DENY
- `review_risk_level: MEDIUM`     # 风险 ≥ MEDIUM → NEEDS_REVIEW

### 策略配置

通过 YAML 文件配置策略，**编辑配置文件即可改变行为，无需修改代码**（满足 issue acceptance #6）。

#### 配置文件位置

通过环境变量 `TRPC_AGENT_TOOL_SAFETY_POLICY` 指定路径，未指定时使用内置默认策略：

```bash
export TRPC_AGENT_TOOL_SAFETY_POLICY=/path/to/custom_policy.yaml
```

#### YAML 字段说明

```yaml
name: default
description: Default tool script safety policy for tRPC-Agent.

# 风险级别阈值（当规则的 decision 为 UNDECIDED 时使用）
deny_risk_level: HIGH        # findings >= HIGH -> DENY
review_risk_level: MEDIUM    # findings >= MEDIUM (and < deny) -> NEEDS_REVIEW

# 全局白名单
whitelisted_domains:         # 网络白名单域
  - pypi.org
  - github.com
  - example.com
allowed_commands:            # 允许的命令
  - ls
  - cat
  - echo
  - python
denied_paths:                # 禁止访问的路径
  - /etc
  - /root
  - ~/.ssh
  - ~/.env
  - ~/.aws/credentials

# 资源限制（静态扫描仅为信息性，实际由 executor 运行时执行）
max_timeout_seconds: 30
max_output_bytes: 1048576
max_evidence_chars: 200      # 证据片段最大长度

# 规则级覆盖（可选）
rule_overrides:
  # tool-net-http:
  #   risk_level: HIGH
  #   decision: DENY
```

### 与其他组件的关系

#### 与沙箱的关系

**Tool Script Safety Guard 不能替代沙箱隔离**：

- **本机制**：静态分析，执行**前**拦截，基于模式匹配
- **沙箱**：运行时隔离，执行**中**限制，基于资源/权限控制
- **互补性**：静态拦截降低沙箱逃逸风险，沙箱防止静态分析漏报的代码造成实际损害

**为什么不能替代沙箱**：
1. 静态分析固有局限：混淆/编码绕过（`base64 -d | sh`）、动态拼接、间接调用可漏报
2. 运行时行为不可预测：内存中的代码注入、反射调用等静态无法检测
3. 资源滥用需要运行时限制：无限循环、内存耗尽等需要超时/资源配额控制

#### 与 Filter 的关系

`ToolSafetyFilter` 是 `BaseFilter` 的子类，注册为 `"tool_safety"`：

- **执行时机**：在 Tool 的 `_run_async_impl` **之前**
- **拦截行为**：决策非 `ALLOW` 时，返回 `FilterResult(is_continue=False)`，不调用 `handle()`
- **适用场景**：拦截 Tool/Skill 的单次执行

#### 与 CodeExecutor 的关系

`SafetyGuardedCodeExecutor` 是 `BaseCodeExecutor` 的包装器：

- **执行时机**：在 CodeExecutor 的 `execute_code` **之前**
- **拦截行为**：逐 CodeBlock 扫描，跳过危险块，仅执行安全块
- **适用场景**：保护任意 CodeExecutor（包括 `UnsafeLocalCodeExecutor`）

#### 与 Telemetry 的关系

- **审计事件**：拦截时记录结构化日志（含 `decision`/`risk_level`/`rule_ids`/`evidence`/`recommendation`）
- **OpenTelemetry**：预留接口（MVP 不实现，未来可添加 span 属性）

### 已知限制

1. **静态扫描固有局限**
   - 混淆/编码绕过：`base64 -d | sh`、`eval(base64.b64decode("..."))` 可漏报
   - 动态拼接：`getattr(os, "system")("rm -rf /")`、间接调用可能漏报
   - 运行时代码注入：内存修改、`__import__` 动态加载等静态无法检测

2. **误报与漏报**
   - **误报**：合法脚本命中危险模式（如合法的 `subprocess.call` 被标记）
   - **漏报**：新型绕过技术未覆盖（如新混淆方法）
   - **调优手段**：通过 `whitelisted_domains`、`allowed_commands`、`rule_overrides` 调整

3. **Bash 解析为启发式**
   - 使用 `shlex` + 状态机，非完整 POSIX shell 解析器
   - 复杂引用/转义边界可能误判（如 `$'..."..."'` 嵌套）

4. **Python AST 解析失败**
   - AST 解析失败时降级字符串启发式（记录但不阻塞）
   - 依赖语法正确性，语法错误的脚本可能绕过检测

### 扩展规则

添加新规则需要修改两处：

#### 1. 在 `_rules.py` 中添加常量

```python
# trpc_agent_sdk/tools/safety/_rules.py

# 新增规则ID
R_MY_CUSTOM_RULE = "tool-custom-my-rule"

# 在 DEFAULT_RULE_POLICIES 中定义默认行为
DEFAULT_RULE_POLICIES: dict[str, tuple[RiskLevel, Decision]] = {
    # ... 现有规则 ...
    R_MY_CUSTOM_RULE: (RiskLevel.MEDIUM, Decision.NEEDS_REVIEW),
}
```

#### 2. 在扫描器中添加检测逻辑

**Python 扫描器**（`_python_scanner.py`）：

```python
# 在扫描函数中添加检测分支
def _scan_python_script(policy: Policy, script: str) -> list[Finding]:
    findings = []
    # ... 现有检测逻辑 ...
    
    # 添加新检测
    if "dangerous_pattern" in script:
        findings.append(Finding(
            rule_id=R_MY_CUSTOM_RULE,
            risk_level=RiskLevel.MEDIUM,
            rule_decision=Decision.NEEDS_REVIEW,
            evidence="...",
            recommendation="...",
            language="python"
        ))
    
    return findings
```

**Bash 扫描器**（`_bash_scanner.py`）：

```python
# 在 Bash 扫描函数中添加检测分支
def _scan_bash_script(policy: Policy, script: str) -> list[Finding]:
    findings = []
    # ... 现有检测逻辑 ...
    
    # 添加新检测
    if "dangerous_command" in tokens:
        findings.append(Finding(
            rule_id=R_MY_CUSTOM_RULE,
            risk_level=RiskLevel.MEDIUM,
            rule_decision=Decision.NEEDS_REVIEW,
            evidence="...",
            recommendation="...",
            language="bash"
        ))
    
    return findings
```

#### 3. 在 YAML 中覆盖（可选）

```yaml
# tool_safety_policy.yaml
rule_overrides:
  tool-custom-my-rule:
    risk_level: HIGH
    decision: DENY
```

### 参考

- **设计文档**：`docs/superpowers/specs/2026-07-09-tool-safety-guard-design.md`
- **实现代码**：`trpc_agent_sdk/tools/safety/`
- **测试用例**：`tests/tools/safety/samples/manifest.yaml`
- **对应 Issue**：[trpc-group/trpc-agent-python#90](https://github.com/trpc-group/trpc-agent-python/issues/90)
