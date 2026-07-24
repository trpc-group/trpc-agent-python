# Tool Script Safety Guard — 设计文档

## 概述

Tool Script Safety Guard 是一种安全机制，在 Tool 执行脚本（Python、Bash）**之前**对其进行风险扫描。它集成到 tRPC-Agent 现有的过滤器管道中，提供独立的扫描 API，并输出结构化的审计记录和 OpenTelemetry span 属性。

### 范围

- 扫描 Python 脚本和 Bash 命令的 6 类风险
- 作为 `BaseFilter` 可插拔接入 Tool 执行链路
- 通过 `tool_safety_policy.yaml` 配置，无需修改代码
- 输出结构化的扫描报告和 JSONL 审计日志
- 用安全元数据装饰现有的 OpenTelemetry span

### 不在范围内

- 运行时沙箱（由 `ContainerCodeExecutor`、`CubeCodeExecutor` 处理）
- 网络层面的防火墙或出口控制
- 替代容器/虚拟机隔离

---

## 包结构

```
trpc_agent_sdk/tools/safety/
├── __init__.py            # 公开导出
├── _types.py              # 枚举和数据类（RiskType、Decision、ScanReport、AuditEvent）
├── _policy.py             # SafetyPolicy 模型、YAML 加载/校验
├── _rules.py              # 规则定义：PatternRule、AstRule（约14条规则）
├── _scanner.py            # ToolSafetyScanner——编排与扫描管道
├── _filter.py             # ToolSafetyFilter(BaseFilter)——过滤器集成
├── _audit.py              # SafetyAuditLogger——JSONL 审计日志
├── _telemetry.py          # set_safety_span_attrs()——OTel span 装饰
└── tool_safety_policy.yaml  # 默认策略文件
```

---

## 数据模型

### 枚举

| 枚举 | 值 | 描述 |
|------|------|------|
| `RiskType` | `dangerous_file_operation`、`network_access`、`system_command`、`dependency_install`、`resource_abuse`、`sensitive_info_leak` | 6 个风险类别 |
| `Decision` | `allow`、`deny`、`needs_human_review` | 扫描结果 |
| `RiskLevel` | `low`、`medium`、`high`、`critical` | 风险严重程度 |

### 数据类

**RuleFinding**——单条规则匹配：
- `rule_id: str`——例如 `"DANGEROUS_DELETE_001"`
- `risk_type: RiskType`
- `risk_level: RiskLevel`
- `evidence: str`——匹配的脚本行/片段
- `message: str`——人类可读的描述
- `recommendation: str`——建议的缓解措施

**ScanReport**——聚合扫描结果：
- `decision: Decision`——所有发现中的最差决策
- `risk_level: RiskLevel | None`
- `findings: list[RuleFinding]`
- `scan_duration_ms: float`
- `script_snippet: str | None`——前 N 个字符用于上下文
- `scan_error: str | None`

**AuditEvent**——用于 JSONL 日志：
- `timestamp: str`（ISO 8601 格式）
- `tool_name: str`
- `decision: str`
- `risk_level: str | None`
- `rule_ids: list[str]`
- `scan_duration_ms: float`
- `sanitized: bool`
- `intercepted: bool`
- `script_hash: str`（SHA-256）

---

## 策略配置

`tool_safety_policy.yaml` 控制所有可配置行为：

```yaml
version: "1.0"
max_script_size_bytes: 1048576    # 1 MB
max_scan_time_ms: 1000            # 1 秒
default_decision: deny

rules:
  - rule_id: DANGEROUS_DELETE_001
    enabled: true
    risk_type: dangerous_file_operation
    severity: critical
    decision: deny
  - rule_id: SENSITIVE_PATH_002
    enabled: true
    risk_type: dangerous_file_operation
    severity: critical
    decision: deny
  # ...

whitelist:
  domains: ["api.example.com", "trusted.internal.org"]
  commands: ["ls", "cat", "echo", "pwd"]
  paths: ["/tmp/", "/workspace/", "./"]

blocklist:
  paths: ["~/.ssh", "~/.aws", "/etc/passwd", "/etc/shadow", ".env"]
  commands: ["sudo", "chmod 777"]
```

### 策略行为规则：

1. 白名单优先于黑名单——同时出现在两者中的项将被允许
2. 当无规则匹配时使用 `default_decision`（保守策略：`deny`）
3. 可通过 `enabled: false` 单独开关每条规则
4. 每条规则可独立覆盖严重程度和决策
5. `max_scan_time_ms` 为硬性超时；超时 → `default_decision`

---

## 风险类别与规则

### 1. 危险文件操作 (`dangerous_file_operation`)

| 规则 ID | 触发条件 | 检测方式 |
|---------|----------|----------|
| `DANGEROUS_DELETE_001` | `rm -rf`、`shutil.rmtree()`、`os.remove()` 作用于非 /tmp 路径 | 模式 + AST |
| `SENSITIVE_PATH_002` | 访问 `~/.ssh`、`/etc/passwd`、`~/.aws`、`.env`、`~/.config` | 模式 |

### 2. 网络外连 (`network_access`)

| 规则 ID | 触发条件 | 检测方式 |
|---------|----------|----------|
| `NETWORK_CURL_003` | Bash 脚本中的 `curl`、`wget` | 模式 + 域名白名单检查 |
| `NETWORK_PYTHON_004` | `requests.get/post`、`httpx.get/post`、`urllib.request` | 模式 + AST |
| `NETWORK_SOCKET_005` | `socket.connect()`、`socket.create_connection()` | 模式 + AST |

### 3. 系统与进程命令 (`system_command`)

| 规则 ID | 触发条件 | 检测方式 |
|---------|----------|----------|
| `SUBPROCESS_006` | `subprocess.run/Popen/call` | 模式 + AST |
| `OS_SYSTEM_007` | `os.system()`、`os.popen()`、Bash 反引号执行 | 模式 |
| `PRIVILEGE_ESCALA_009` | `sudo`、`su`、`chmod 777`、`chown` | 模式 |

### 4. 依赖安装 (`dependency_install`)

| 规则 ID | 触发条件 | 检测方式 |
|---------|----------|----------|
| `DEP_INSTALL_008` | `pip install`、`npm install`、`apt-get install`、`yum install` | 模式 |

### 5. 资源滥用 (`resource_abuse`)

| 规则 ID | 触发条件 | 检测方式 |
|---------|----------|----------|
| `FORK_BOMB_011` | `:(){ :\|:& };:`、大量 `fork()` 调用 | 模式 + AST |
| `INFINITE_LOOP_012` | `while True:`、`while (true)`、`for(;;)` | 模式 + AST |

### 6. 敏感信息泄漏 (`sensitive_info_leak`)

| 规则 ID | 触发条件 | 检测方式 |
|---------|----------|----------|
| `SENSITIVE_LOG_010` | `print(api_key)`、`write(token)`、命名为 `*KEY*`/`*TOKEN*`/`*SECRET*`/`*PASSWORD*` 的环境变量 | 模式 + AST |

---

## 扫描流程

```
script_text、tool_name、args、env_vars
         │
         ▼
  1. 预检查——大小 > max_script_size_bytes？→ deny
         │
         ▼
  2. 白名单快速通道——所有检测到的域名/命令/路径都在白名单中？
         │                                           → allow（跳过完整扫描）
         ▼
  3. 模式扫描——对所有启用的模式规则应用于文本、参数、环境变量
         │
         ▼
  4. AST 扫描——如果检测到 Python，解析 AST 并应用 AST 规则
         │
         ▼
  5. 聚合——合并发现，解析最高严重程度 → 决策
         │
         ▼
  6. 审计——输出 ScanReport + 审计事件
         │
         ▼
      return ScanReport
```

### 决策聚合

```
worst_decision = max(findings, key=severity_priority)
```

优先级：`DENY > NEEDS_HUMAN_REVIEW > ALLOW`。单个 `DENY` 发现即阻断执行，无论其他发现结果如何。

### Python 检测启发式

扫描器检查：`def `、`import `、`from `、`class `、`#!python`。如果以上均不存在，则跳过 AST 扫描步骤，仅应用模式规则。`ast.parse()` 解析失败会优雅捕获，仍然返回模式检测结果。

### 超时

`asyncio.wait_for()` 包裹模式扫描和 AST 扫描阶段。如果超时（`max_scan_time_ms`），扫描返回 `default_decision` 并附带错误提示。

---

## 集成点

### 1. 过滤器模式

`ToolSafetyFilter` 继承 `BaseFilter`，注册为 `FilterType.TOOL`。它实现 `_before()` 方法，在 Tool 执行前扫描参数，拒绝时设置 `rsp.is_continue = False`。

```python
scanner = ToolSafetyScanner("tool_safety_policy.yaml")
register_filter(FilterType.TOOL, "tool_safety", ToolSafetyFilter(scanner, audit_logger))

# 绑定到 Tool
tool = FunctionTool(func=..., filters=["tool_safety"])
```

### 2. 独立模式

```python
scanner = ToolSafetyScanner("tool_safety_policy.yaml")
report = await scanner.scan(
    script="rm -rf /home/user/data",
    tool_name="bash_tool",
)
if report.decision == Decision.DENY:
    raise SafetyViolationError(report)
```

### 3. OpenTelemetry

当存在活跃 span（例如 `ToolsProcessor._execute_tool()` 中创建的 Tool 执行 span），安全过滤器设置以下属性：

| 属性 | 类型 | 描述 |
|-----------|------|------|
| `tool.safety.decision` | string | `allow`、`deny` 或 `needs_human_review` |
| `tool.safety.risk_level` | string | `low`、`medium`、`high` 或 `critical` |
| `tool.safety.rule_ids` | string[] | 所有触发规则的 ID |
| `tool.safety.scan_duration_ms` | float | 扫描耗时（毫秒） |

不创建新 span——仅装饰现有的 Tool 执行 span。

---

## 审计日志

每行写入一个 JSON 对象到 `tool_safety_audit.jsonl`：

```json
{"timestamp":"2026-07-10T12:00:00Z","tool_name":"bash_tool","decision":"deny","risk_level":"critical","rule_ids":["DANGEROUS_DELETE_001"],"scan_duration_ms":12.5,"sanitized":false,"intercepted":true,"script_hash":"a1b2c3..."}
{"timestamp":"2026-07-10T12:01:00Z","tool_name":"python_tool","decision":"allow","risk_level":null,"rule_ids":[],"scan_duration_ms":3.1,"sanitized":false,"intercepted":false,"script_hash":"d4e5f6..."}
```

---

## 与其他框架组件的关系

| 组件 | 关系 |
|-----------|------|
| **CodeExecutor**（`UnsafeLocalCodeExecutor`、`ContainerCodeExecutor`、`CubeCodeExecutor`） | Safety Guard 是**执行前**扫描器。CodeExecutor 提供**运行时**隔离。二者是互补层次：Safety Guard 阻断已知危险的脚本；执行器沙箱化通过扫描的脚本。Safety Guard **不能**替代沙箱隔离。 |
| **过滤器系统**（`BaseFilter`、`FilterRunner`、`FilterRegistry`） | `ToolSafetyFilter` 是一个标准过滤器，通过 `FilterType.TOOL` 接入 Tool 过滤器链。在 `_before()` 中运行，检查并可能阻断执行。 |
| **遥测**（`trace`、`metrics`） | Safety Guard 用 `tool.safety.*` 属性装饰现有的 Tool span。不创建新 span 或指标。 |
| **回调系统**（`ToolCallbackFilter`） | Safety Guard 与回调在同一个过滤器链中运行。顺序：安全过滤器应优先运行（在回调之前），尽早阻断危险执行。这由过滤器注册顺序控制。 |

---

## 已知限制

1. **模式检测可绕过**——混淆后的脚本（如 `__import__("os").system(...)`）将避开正则规则。AST 规则可以捕获部分混淆，但并非全部。
2. **Bash 解析仅限模式**——没有 Bash AST。包含变量间接引用的复杂 Bash 脚本可能避开规则。
3. **误报**——提及危险关键词的安全脚本（如安全教程示例）也会被标记。
4. **无数据流分析**——扫描器仅检查语法，不判断敏感值是否实际流向危险调用。读取 `API_KEY` 但从未输出的脚本仍会被 `SENSITIVE_LOG_010` 标记。
5. **不是沙箱**——这是静态分析工具，无法防止运行时漏洞利用、内存破坏或新型攻击向量。
6. **白名单快速通道偏向保守**——只要有一个元素不在白名单中，就会运行完整扫描。这意味着部分白名单的脚本仍会产生扫描开销。

---

## 扩展新规则

为新的风险类型添加规则：

```python
@pattern_rule(
    rule_id="NEW_RULE_013",
    risk_type=RiskType.SYSTEM_COMMAND,
    severity=RiskLevel.HIGH,
    pattern=r"evil_command\s+--dangerous",
    message="检测到 evil_command 的使用",
    recommendation="请改用 safe_command",
)
async def check_evil_command(text: str) -> RuleFinding | None:
    ...
```

对于 AST 规则，继承 `ast.NodeVisitor` 并注册到扫描器。所有规则由扫描器根据策略文件自动发现并按顺序应用。

---

## 文件索引

| 文件 | 用途 |
|------|------|
| `design.md` | 本文档的英文版 |
| `design.zh_CN.md` | 本文档——架构与设计（中文） |
| `test_plan.md` | 测试用例与验收标准 |
| `tool_safety_policy.yaml` | 默认策略配置（位于 `tools/safety/`） |
| `tool_safety_report.json` | 示例扫描报告输出 |
| `tool_safety_audit.jsonl` | 示例审计日志输出 |
