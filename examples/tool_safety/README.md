# Tool Script Safety Guard

A pluggable **pre-execution safety scanner** for tRPC-Agent Tool / Skill / CodeExecutor scripts.
It performs static analysis on Python and Bash content **before** execution, emits
`allow` / `deny` / `needs_human_review` decisions, and produces structured reports +
audit events + OpenTelemetry span attributes.

> This is a **defense-in-depth** layer. It does **not** replace sandbox isolation —
> see [Relationship with Sandbox / Filter / Telemetry / CodeExecutor](#relationship-with-sandbox--filter--telemetry--codeexecutor).

---

## 目录

- [快速开始](#快速开始)
- [规则体系](#规则体系)
- [策略文件](#策略文件)
- [接入方式](#接入方式)
- [结构化报告与审计](#结构化报告与审计)
- [测试与验收](#测试与验收)
- [已知限制与绕过风险](#已知限制与绕过风险)
- [扩展新规则](#扩展新规则)
- [与沙箱/Filter/Telemetry/CodeExecutor 的关系](#relationship-with-sandbox--filter--telemetry--codeexecutor)

---

## 快速开始

### 扫描单个脚本

```bash
python examples/tool_safety/tool_safety_check.py --script examples/tool_safety/samples/02_dangerous_delete.sh
```

### 扫描 12 条样本并生成报告 + 审计日志

```bash
python examples/tool_safety/tool_safety_check.py \
    --samples examples/tool_safety/samples/ \
    --policy examples/tool_safety/tool_safety_policy.yaml \
    --report examples/tool_safety/tool_safety_report.json \
    --audit examples/tool_safety/tool_safety_audit.jsonl \
    --verbose
```

预期输出:

```
Summary: 12 scanned | 2 allow | 9 deny | 1 needs_review
```

### 作为库使用

```python
from examples.tool_safety.safety import PolicyConfig, SafetyScanner, ScanInput

policy = PolicyConfig.from_yaml("examples/tool_safety/tool_safety_policy.yaml")
scanner = SafetyScanner(policy=policy)
report = scanner.scan(ScanInput(script="rm -rf /", language="bash"))
print(report.decision)   # Decision.DENY
print(report.rule_ids)   # ['R001_dangerous_files', ...]
```

---

## 规则体系

6 类内置规则,每条规则是一个独立的 `SafetyRule` 子类,可插拔、可禁用:

| 规则 ID | 规则名 | 风险类型 | 默认级别 | 覆盖范围 |
|---|---|---|---|---|
| `R001_dangerous_files` | Dangerous File Operation | dangerous_files | CRITICAL | `rm -rf`、`shutil.rmtree`、系统目录(`/etc` `/usr` `C:\Windows`)、`~/.ssh`、`.env`、`id_rsa`、`.aws/credentials` 等 |
| `R002_network_egress` | Network Egress | network | HIGH | `curl`/`wget`/`requests`/`aiohttp`/`socket`/`urllib` 访问非白名单域名;动态目标标记为需复核 |
| `R003_process_system` | Process / System Command | process | HIGH/CRITICAL | `subprocess`/`os.system`/`os.popen`、`shell=True`、`eval`/`exec`、`sudo`/`su`、后台进程 `&`、嵌套命令替换 |
| `R004_dependency_install` | Dependency Installation | dependency_install | HIGH | `pip install`/`npm install`/`apt install`/`yarn add`/`conda install` 等,含 Python 字符串字面量内嵌的安装命令 |
| `R005_resource_abuse` | Resource Abuse | resource_abuse | HIGH | 无限循环(`while True` 无 break)、fork bomb、`dd`、`yes` 重定向、超长 `sleep`、高并发池 |
| `R006_secret_leak` | Sensitive Information Leakage | secret_leak | CRITICAL | OpenAI/AWS/Slack/GitHub/JWT 密钥模式、`bearer` token、密钥变量传入 `print`/`logging`/`open` 等 sink |

### 判定逻辑

- 聚合所有命中的 finding,取最高 `risk_level`。
- `risk_level >= deny_risk_level`(默认 HIGH) → **DENY**
- `risk_level >= review_risk_level`(默认 MEDIUM) → **NEEDS_HUMAN_REVIEW**
- 否则 → **ALLOW**
- 不确定情况(如动态网络目标)**不会**直接放行,而是标记为 `needs_human_review`。

---

## 策略文件

[`tool_safety_policy.yaml`](tool_safety_policy.yaml) 控制全部行为,**修改后无需改代码**:

```yaml
whitelisted_domains:      # 网络白名单(后缀匹配),空列表 = 全部拒绝(fail-closed)
  - api.github.com
  - pypi.org
forbidden_paths:          # 禁止访问的路径子串
  - ~/.ssh
  - .env
allowed_commands:         # 允许的 bash 命令(仍会扫描参数)
  - ls
  - git
max_timeout_seconds: 300  # 超时上限,用于判定长 sleep
max_output_bytes: 10485760
deny_risk_level: high     # HIGH/CRITICAL → DENY
review_risk_level: medium # MEDIUM → NEEDS_HUMAN_REVIEW
secret_patterns:          # 额外密钥正则(补充内置默认)
  - '(?i)sk-[A-Za-z0-9]{20,}'
disabled_rules: []        # 要跳过的 rule_id 列表
```

热更新示例见 [`tests/tool_safety/test_policy.py`](../../tests/tool_safety/test_policy.py):
修改 YAML 后重新 `PolicyConfig.from_yaml(...)` 即可改变白名单域名、禁止路径、允许命令的判定行为。

---

## 接入方式

### 方式 1:作为 Tool Filter 接入 SDK 执行链路(推荐)

`ToolSafetyFilter` 继承 SDK 的 `BaseFilter`,在 `_before` 钩子里扫描 tool 参数,
DENY 时设置 `is_continue=False` 阻断 `_run_async_impl`,并写审计日志:

```python
from examples.tool_safety.safety import ToolSafetyFilter, PolicyConfig
from trpc_agent_sdk.tools.file_tools import BashTool

policy = PolicyConfig.from_yaml("examples/tool_safety/tool_safety_policy.yaml")
safety_filter = ToolSafetyFilter(policy=policy, audit_path="audit.jsonl")
tool = BashTool(filters=[safety_filter])
```

### 方式 2:wrapper 包装现有 tool / executor

```python
from examples.tool_safety.safety import wrap_tool, PolicyConfig

policy = PolicyConfig.from_yaml("...")
tool = wrap_tool(existing_tool, policy, audit_path="audit.jsonl")
```

### 方式 3:独立 CLI 扫描

见 [快速开始](#快速开始),适合 CI 流水线或人工预检。

接入点说明:SDK 的 `BaseTool.run_async` 在调用 `_run_async_impl` 前会先跑 `_run_filters`,
本 Filter 正是挂在这个前置位置,因此能在执行前拦截高危脚本。

---

## 结构化报告与审计

### SafetyReport 字段(issue 验收标准 5)

每条扫描产出 `SafetyReport`,序列化为 JSON,包含:

| 字段 | 说明 |
|---|---|
| `decision` | `allow` / `deny` / `needs_human_review` |
| `risk_level` | 聚合最高风险级别 |
| `rule_ids` | 命中规则 ID 列表 |
| `findings[].rule_id` / `evidence` / `line` / `recommendation` | 单条证据 |
| `scan_duration_ms` | 扫描耗时 |
| `sanitized` | 证据是否已脱敏(始终为 true) |
| `blocked` | 是否拦截(decision==deny) |

示例输出见 [`tool_safety_report.json`](tool_safety_report.json)。

### 审计日志(issue 验收标准 7)

每条决策写一行 JSONL 到 `tool_safety_audit.jsonl`,字段含:
`tool_name`、`decision`、`risk_level`、`rule_ids`、`scan_duration_ms`、
`sanitized`、`intercepted`、`blocked`、`timestamp`、`script_path`。

示例见 [`tool_safety_audit.jsonl`](tool_safety_audit.jsonl)。

### OpenTelemetry 埋点

当宿主进程启用了 OpenTelemetry,扫描会在当前 span 上设置:

- `tool.safety.decision`
- `tool.safety.risk_level`
- `tool.safety.rule_id`(逗号分隔)
- `tool.safety.scan_duration_ms`
- `tool.safety.sanitized`
- `tool.safety.blocked`
- `tool.safety.tool_name`

未启用 OTel 时为静默 no-op,不影响安全路径。

---

## 测试与验收

### 运行测试

```bash
cd d:\Tencent\trpc-agent-python
pytest tests/tool_safety/ -v
```

### 验收标准对照

| issue 验收标准 | 验收方式 | 结果 |
|---|---|---|
| 1. 12 条样本可扫描并输出结构化报告 | `test_scanner.py` 12 个 case + CLI | ✅ 12/12 |
| 2. 高危检出率 ≥90%,误报率 ≤10% | `test_scanner.py::test_detection_rate` | ✅ 9/9 检出,0/2 误报 |
| 3. 读密钥/危险删除/非白名单外连 100% 检出 | `test_scanner.py::test_required_100_percent_detection` | ✅ 3/3 |
| 4. 500 行脚本 ≤1s | `test_performance.py` | ✅ <1s |
| 5. 报告含 decision/risk_level/rule_id/evidence/recommendation | `test_scanner.py::_assert_report_well_formed` | ✅ |
| 6. 改策略不改代码即生效 | `test_policy.py::test_hot_reload_*` | ✅ |
| 7. Filter 执行前拒绝高危 + 写审计 | `test_tool_filter.py` | ✅ 4 case |
| 8. 文档说明与沙箱等关系 | 本 README 末节 | ✅ |

### 12 条样本

| # | 样本 | 期望决策 |
|---|---|---|
| 01 | 安全 Python | allow |
| 02 | 危险删除 `rm -rf /` | deny |
| 03 | 读取 `~/.ssh/id_rsa` / `.env` / `.aws/credentials` | deny |
| 04 | 网络外连 evil.example.com | deny |
| 05 | 白名单网络(api.github.com) | allow |
| 06 | subprocess + `shell=True` | deny |
| 07 | shell 注入(eval/sudo/嵌套$()) | deny |
| 08 | 依赖安装(pip/npm/apt) | deny |
| 09 | 无限循环 + 长 sleep | deny |
| 10 | 密钥泄漏到日志/网络/文件 | deny |
| 11 | bash 管道 + fork + dd | deny |
| 12 | 动态网络目标(无法静态判定) | needs_human_review |

---

## 已知限制与绕过风险

本工具是**静态分析 + 策略判定**,存在以下固有局限:

### 误报(False Positives)

- 字符串中包含 `rm -rf` 文本(如文档/注释)可能被标记。
- 变量名含 `token`/`key` 但实际非密钥(如 `token_count`)可能被标记。
- 白名单域名的子域匹配可能对形如 `evil-api.github.com.attacker.com` 的伪造成因后缀匹配逻辑而漏报(已用 `endswith("." + d)` 缓解)。

### 漏报(False Negatives)

- **动态构造**: `getattr(os, "sys" + "tem")("rm -rf /")` 无法被静态解析捕获。
- **编码混淆**: base64/hex 编码后的命令、`exec(__import__('base64').b64decode(...))` 可绕过。
- **间接调用**: 通过 `importlib.import_module` 动态加载模块再调用。
- **环境变量拼接**: `os.system(env_var + " evil")` 的 `env_var` 值在运行时才确定。
- **Bash 复杂语法**: heredoc、`eval` 嵌套、别名展开等超出正则覆盖范围。

### 绕过风险

- 攻击者可利用 Python 元编程(`__import__`、`globals()`、`getattr` 链)绕过 AST 名字解析。
- Bash 中可通过变量间接展开 `${!var}` 或 `eval` 二次展开绕过静态扫描。
- 这是**所有静态分析工具**的固有限制,正是为什么本工具**不能替代沙箱**。

---

## 扩展新规则

1. 在 [`safety/rules/`](safety/rules/) 下新建文件,实现 `SafetyRule` 子类:

```python
from .base import SafetyRule
from ..types import RiskLevel, SafetyFinding, ScanInput
from ..policy import PolicyConfig

class MyRule(SafetyRule):
    rule_id = "R007_my_rule"
    rule_name = "My Custom Rule"
    risk_type = "custom"
    default_level = RiskLevel.MEDIUM
    languages = ("python", "bash")

    def check(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        # 你的检测逻辑
        return []
```

2. 在 [`safety/rules/__init__.py`](safety/rules/__init__.py) 的 `default_rules()` 中注册。

3. 可在 `tool_safety_policy.yaml` 的 `disabled_rules` 中按 `rule_id` 禁用,无需改代码。

4. 在 `tests/tool_safety/test_rules.py` 添加单测。

---

## Relationship with Sandbox / Filter / Telemetry / CodeExecutor

本机制在 tRPC-Agent 的安全体系中处于**执行前静态检查**位置,与其它组件分工如下:

```
Tool 调用请求
     │
     ▼
┌─────────────────────────────┐
│  ToolSafetyFilter (本工具)   │  ← 静态扫描 + 策略判定,执行前拦截
│  - AST/正则分析脚本           │
│  - allow/deny/review 决策     │
│  - 写审计日志 + OTel span     │
└────────────┬────────────────┘
             │ allow / review
             ▼
┌─────────────────────────────┐
│  CodeExecutor (执行层)       │  ← 实际运行代码
│  - UnsafeLocalCodeExecutor   │
│  - ContainerCodeExecutor     │  ← 沙箱隔离(容器/进程级)
└────────────┬────────────────┘
             │
             ▼
┌─────────────────────────────┐
│  Telemetry (可观测)          │  ← 运行时监控
│  - span / metrics / logs     │
└─────────────────────────────┘
```

### 为什么不能替代沙箱隔离

| 维度 | 本 Safety Guard | 沙箱(CodeExecutor) |
|---|---|---|
| 作用时机 | 执行**前** | 执行**中** |
| 能力 | 静态模式匹配、策略判定 | 资源限制(CPU/内存/磁盘)、文件系统隔离、网络隔离、系统调用过滤 |
| 局限 | 无法检测动态构造/编码混淆(见上文) | 无法识别脚本意图,但有硬隔离边界 |
| 失败模式 | 漏报 → 危险代码进入执行 | 即便执行也是受限环境 |

**结论**:本工具是**第一道防线**,负责在执行前过滤明显的高危模式;沙箱是**最后一道防线**,负责限制已执行代码的副作用。两者**必须配合使用**:

- 只用沙箱不用本工具:无法提前预警,审计缺失意图信息。
- 只用本工具不用沙箱:一旦漏报,攻击者直接获得宿主权限。

Filter 链路(`ToolSafetyFilter`)负责把守入口,CodeExecutor 负责隔离执行,Telemetry 贯穿全程提供可观测性——三者共同构成 defense-in-depth。
