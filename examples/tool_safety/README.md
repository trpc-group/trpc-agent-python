# Tool Script Safety Guard

A pluggable **pre-execution safety scanner** for tRPC-Agent Tool / Skill / CodeExecutor scripts.
It performs static analysis on Python and Bash content **before** execution, emits
`allow` / `deny` / `needs_human_review` decisions, and produces structured reports +
audit events + OpenTelemetry span attributes.

Implementation lives in the SDK package:

```python
from trpc_agent_sdk.safety import PolicyConfig, SafetyScanner, ScanInput, ToolSafetyFilter
```

This example directory provides the policy file, sample scripts, CLI wrappers, and
generated report / audit fixtures.

> This is a **defense-in-depth** layer. It does **not** replace sandbox isolation —
> see [Relationship with Sandbox / Filter / Telemetry / CodeExecutor](#relationship-with-sandbox--filter--telemetry--codeexecutor).

---

## 快速开始

### 扫描单个脚本

```bash
python scripts/tool_safety_check.py --script examples/tool_safety/samples/02_dangerous_delete.sh
```

### 扫描全部样本并生成报告 + 审计日志

```bash
python scripts/tool_safety_check.py \
    --samples examples/tool_safety/samples/ \
    --policy examples/tool_safety/tool_safety_policy.yaml \
    --report examples/tool_safety/tool_safety_report.json \
    --audit examples/tool_safety/tool_safety_audit.jsonl \
    --verbose
```

### 评估检出率

```bash
python scripts/tool_safety_eval.py \
    --samples examples/tool_safety/samples/ \
    --manifest examples/tool_safety/samples/manifest.yaml \
    --policy examples/tool_safety/tool_safety_policy.yaml
```

### 作为库使用

```python
from trpc_agent_sdk.safety import PolicyConfig, SafetyScanner, ScanInput

policy = PolicyConfig.from_yaml("examples/tool_safety/tool_safety_policy.yaml")
scanner = SafetyScanner(policy=policy)
report = scanner.scan(ScanInput(script="rm -rf /", language="bash"))
print(report.decision)   # Decision.DENY
print(report.rule_ids)   # ['R001_dangerous_files', ...]
```

---

## 规则体系

6 类内置规则，每条规则是独立的 `SafetyRule` 子类，可插拔、可禁用：

| 规则 ID | 规则名 | 风险类型 | 默认级别 | 覆盖范围 |
|---|---|---|---|---|
| `R001_dangerous_files` | Dangerous File Operation | dangerous_files | CRITICAL | `rm -rf`、`shutil.rmtree`、系统目录、`~/.ssh`、`.env`、`id_rsa`、pathlib 链 |
| `R002_network_egress` | Network Egress | network | HIGH | `curl`/`wget`/`requests`/`httpx`/`Session().get`/`aiohttp`/`socket`；动态目标 → review |
| `R003_process_system` | Process / System Command | process | HIGH/CRITICAL | `subprocess`/`os.system`（含 import alias / from-import）、`getattr`、`eval`/`exec`、`sudo`、`base64 \| sh` |
| `R004_dependency_install` | Dependency Installation | dependency_install | HIGH | `pip`/`npm`/`apt`/`yarn`/`conda` 等，含 subprocess list 形态 |
| `R005_resource_abuse` | Resource Abuse | resource_abuse | HIGH | 无限循环、fork bomb、`dd`、超长 sleep、高并发池 |
| `R006_secret_leak` | Sensitive Information Leakage | secret_leak | CRITICAL | API key / JWT / 私钥模式、`os.environ[SECRET]` sink、curl 上传凭据 |

### 判定逻辑

- 聚合所有 finding，取最高 `risk_level`
- `risk_level >= deny_risk_level`（默认 HIGH）→ **DENY**
- `risk_level >= review_risk_level`（默认 MEDIUM）→ **NEEDS_HUMAN_REVIEW**
- 否则 → **ALLOW**
- 不确定情况（动态网络目标等）**不会**直接放行

静态分析增强：

- Python import 别名解析（`import os as x` / `from os import system`）
- `requests.Session()` / `httpx.Client()` 变量追踪
- `python -c` / `bash -c` payload 二次扫描
- `base64 -d | sh` 等解码执行链
- pathlib 敏感路径链

---

## 策略文件

[`tool_safety_policy.yaml`](tool_safety_policy.yaml) 控制全部行为，**修改后无需改代码**：

```yaml
whitelisted_domains:      # 网络白名单（后缀匹配），空列表 = 全部拒绝
  - api.github.com
forbidden_paths:          # 禁止访问的路径子串
  - ~/.ssh
  - .env
allowed_commands:         # bash 命令允许列表
  - ls
  - git
strict_command_allowlist: false  # true 时不在列表中的命令会被 HIGH 标记
block_on_review: false           # true 时 Filter 对 needs_review 也拦截
strict_policy: false             # true 时未知 YAML 字段报错
max_timeout_seconds: 300
deny_risk_level: high
review_risk_level: medium
disabled_rules: []
```

---

## 接入方式

### 1. Tool Filter（推荐）

```python
from trpc_agent_sdk.safety import ToolSafetyFilter, PolicyConfig
from trpc_agent_sdk.tools.file_tools import BashTool

policy = PolicyConfig.from_yaml("examples/tool_safety/tool_safety_policy.yaml")
safety_filter = ToolSafetyFilter(policy=policy, audit_path="audit.jsonl")
tool = BashTool(filters=[safety_filter])
```

`policy.block_on_review=True` 时，`needs_human_review` 也会阻断执行。

### 2. wrap_tool

```python
from trpc_agent_sdk.safety import wrap_tool, PolicyConfig

tool = wrap_tool(existing_tool, policy, audit_path="audit.jsonl")
```

### 3. 装饰器

```python
from trpc_agent_sdk.safety import safety_wrapper, SafetyDeniedError

@safety_wrapper(tool_name="my_runner", script_arg="code", policy=policy)
async def execute(*, tool_context, args):
    ...
```

### 4. Skill runner wrapper

```python
from trpc_agent_sdk.safety import SafetyReviewedSkillRunner

safe_skill = SafetyReviewedSkillRunner(my_skill_runner, policy, block_review=True)
result = await safe_skill.run(tool_context, args)
```

### 5. CodeExecutor wrapper

```python
from trpc_agent_sdk.safety import SafetyGuardedCodeExecutor, safe_code_executor

guarded = SafetyGuardedCodeExecutor(inner_executor, policy, audit_path="audit.jsonl")
# or: guarded = safe_code_executor(inner_executor, policy)
```

### 6. CLI / CI gate

退出码：`0` allow / `1` deny / `2` needs_review。

---

## 结构化报告与审计

报告字段：`decision`、`risk_level`、`rule_ids`、`findings[].rule_id/evidence/line/recommendation`、`scan_duration_ms`、`sanitized`、`blocked`。

审计 JSONL 字段：`tool_name`、`decision`、`risk_level`、`rule_ids`、`scan_duration_ms`、`sanitized`、`intercepted`、`blocked`、`timestamp`。

OpenTelemetry span attributes（宿主启用 OTel 时）：

- `tool.safety.decision`
- `tool.safety.risk_level`
- `tool.safety.rule_id`
- `tool.safety.scan_duration_ms`
- `tool.safety.sanitized`
- `tool.safety.blocked`
- `tool.safety.tool_name`

---

## 测试与验收

```bash
pytest tests/tool_safety/ -v
```

| issue 验收标准 | 结果 |
|---|---|
| 1. 样本可扫描并输出结构化报告 | ✅ 20 条样本 + manifest |
| 2. 高危检出率 ≥90%，误报率 ≤10% | ✅ |
| 3. 读密钥/危险删除/非白名单外连 100% | ✅ |
| 4. 500 行脚本 ≤1s | ✅ |
| 5. 报告含 decision/risk_level/rule_id/evidence/recommendation | ✅ |
| 6. 改策略不改代码即生效 | ✅ |
| 7. Filter/wrapper 执行前拒绝 + 写审计 | ✅ |
| 8. 文档说明与沙箱等关系 | ✅ |

样本清单见 [`samples/manifest.yaml`](samples/manifest.yaml)（含 alias / base64 管道 / Session / pathlib / env secret 等对抗场景）。

---

## 已知限制与绕过风险

### 误报

- 文档字符串中的 `rm -rf` 文本可能被标记
- 变量名含 `token` 但非密钥可能被标记

### 漏报

- 深度元编程：`globals()['__builtins__']['eval'](...)`
- 多层编码混淆、远程加载后再执行
- 运行时才确定的环境变量拼接

### 绕过风险

静态分析无法覆盖所有动态构造。本工具是第一道防线，**不能替代沙箱隔离**。

---

## 扩展新规则

```python
from trpc_agent_sdk.safety import register_custom_rule, SafetyRule, RiskLevel, SafetyFinding

class CompanyPolicyRule(SafetyRule):
    rule_id = "CUSTOM_company_policy"
    rule_name = "Company-specific banned API"
    risk_type = "custom"
    default_level = RiskLevel.HIGH
    languages = ("python",)

    def check(self, scan_input, policy):
        if "internal_unstable_api" in scan_input.script:
            return [SafetyFinding(
                rule_id=self.rule_id, rule_name=self.rule_name,
                risk_type=self.risk_type, risk_level=self.default_level,
                evidence="internal_unstable_api", line=1,
                recommendation="Use the stable API instead.",
            )]
        return []

register_custom_rule(CompanyPolicyRule())
```

---

## Relationship with Sandbox / Filter / Telemetry / CodeExecutor

```
Tool 调用请求
     │
     ▼
┌─────────────────────────────┐
│  ToolSafetyFilter (本工具)   │  ← 静态扫描 + 策略判定，执行前拦截
└────────────┬────────────────┘
             │ allow / review
             ▼
┌─────────────────────────────┐
│  CodeExecutor (执行层)       │  ← 沙箱隔离（容器/进程级）
└────────────┬────────────────┘
             │
             ▼
┌─────────────────────────────┐
│  Telemetry (可观测)          │  ← span / metrics / logs
└─────────────────────────────┘
```

| 维度 | Safety Guard | 沙箱 |
|---|---|---|
| 作用时机 | 执行**前** | 执行**中** |
| 能力 | 静态模式匹配、策略判定 | 资源限制、文件系统/网络隔离 |
| 局限 | 无法检测全部动态构造 | 无法识别脚本意图 |

**结论**：本工具是第一道防线；沙箱是最后一道防线。两者必须配合使用。
