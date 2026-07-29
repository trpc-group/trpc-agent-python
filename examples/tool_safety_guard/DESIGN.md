# Tool Script Safety Guard 设计文档

本文档说明 Tool Script Safety Guard 的架构、规则体系和各组件之间的关系。

## 架构总览

```text
Tool / Skill / MCP Tool / CodeExecutor 请求
        │
        ▼
  extract_tool_safety_context()  ── _extractors.py
        │
        ▼
  ScanRequest (script + language + cwd + env + metadata)
        │
        ▼
  SafetyScanner.scan()
        │
        ├── 语言归一化: Python / Bash
        ├── 脱敏检测: sanitize_text() 对 script 和 env 中的 key/token/password 脱敏
        ├── PythonParser: AST NodeVisitor + regex fallback
        │     ├── 别名解析: from os import system → system → os.system
        │     ├── getattr 检测: getattr(__builtins__, 'eval')
        │     ├── 危险调用: open/subprocess/requests/eval/exec 等
        │     └── 解析失败: 保留 regex 证据并 fail-closed 为 DENY
        ├── BashParser: 正则 + shlex + 域名白名单检查
        │     └── 危险命令: rm/curl/sudo/pip install/fork bomb 等
        ├── _scan_context_safety(): args/cwd/metadata 超限检查
        └── _deduplicate_findings(): (rule_id, line) 去重
        │
        ▼
  List[SafetyFinding] → max_risk_level() + aggregate_decision()
        │
        ▼
  SafetyReport + AuditLogger + set_safety_telemetry()
        │
        ▼
  执行边界判断: ALLOW → 执行 / DENY → 阻断 / NEEDS_HUMAN_REVIEW → 默认放行
```

## 核心组件

| 组件 | 模块 | 说明 |
|---|---|---|
| `SafetyScanner` | `_scanner.py` | 统一扫描入口，编排 env 检查→解析→上下文→去重→聚合→报告 |
| `PythonParser` | `_python_parser.py` | AST 解析 + regex fallback，含别名解析和 getattr 检测 |
| `BashParser` | `_bash_parser.py` | 正则逐行扫描 + shlex 命令解析 + 域名白名单 |
| `PolicyConfig` | `_policy.py` | YAML 可配置策略，含白名单/黑名单/资源限制/密钥模式 |
| `ToolSafetyFilter` | `_filter.py` | BaseFilter 实现，在 tool 执行前拦截 |
| `SafeCodeExecutor` | `_wrapper.py` | BaseCodeExecutor 包装器，扫描每个 code block |
| `SafetyWrappedToolSet` | `_wrapper.py` | ToolSetABC 包装器，为动态工具注入 filter |
| `AuditLogger` | `_audit.py` | JSONL 审计日志写入 |
| `set_safety_telemetry()` | `_telemetry.py` | OpenTelemetry span attributes 注入 |

## 决策聚合规则

`aggregate_decision()` 按最高风险级别决定最终决策：

| 命中最高风险 | 最终 decision | 默认 blocked |
|---|---|---|
| 无 finding | `allow` | `false` |
| LOW | `allow` | `false` |
| MEDIUM | `needs_human_review` | `false` |
| HIGH / CRITICAL | `deny` | `true` |

`set_blocked()` 可在决策后显式覆盖 `blocked` 字段。`ToolSafetyFilter` 和 `SafeCodeExecutor` 的 `block_on_review` 参数控制 `NEEDS_HUMAN_REVIEW` 是否也阻断。
CLI 中的 `--block-on-review` 同样会把 `NEEDS_HUMAN_REVIEW` 标记为 `blocked=true`，并以退出码 1 作为 CI 阻断信号。

## 风险类型与规则体系

### R001 — 危险文件操作
- Bash: `rm -rf`、`find -delete`、`xargs rm`、敏感路径 (`~/.ssh`, `.env`, `/etc` 等)
- Python: `open`、`shutil.rmtree`、`os.remove`、`Path.unlink`、敏感路径字符串

### R002 — 网络外连
- Bash: `curl`/`wget`/`nc`/`socat` 访问非白名单域名
- Python: `requests`/`httpx`/`aiohttp`/`urllib`/`socket` 导入和调用

### R003 — 进程和系统命令
- Bash: `sudo`/`bash -c`/`sh -c`/`eval`/管道/后台执行 (`&`)/`chmod`/`chown`
- Python: `subprocess.*`/`os.system`/`os.popen`/`eval`/`exec`/`compile`/`__import__`/`shell=True`/`getattr` 动态调用

### R004 — 依赖安装
- Bash: `pip install`/`npm install`/`apt install`/`yum install`/`brew install` 等
- Python: 文本匹配上述模式

### R005 — 资源滥用
- Bash: fork bomb (`:(){ :|:& };:`) / `while true` / `until` / 长时间 `sleep` / `xargs -P`
- Python: `while True:` / `time.sleep` 超限 / 大文件写入

### R006 — 敏感信息泄漏
- Bash: `echo $TOKEN` / `curl -d $PASSWORD`
- Python: 硬编码 API Key / Token / Password / Private Key 字符串

## 接入方式

### 方式 1: Filter（推荐）

```python
from trpc_agent_sdk.tools.safety import add_tool_safety_filter
add_tool_safety_filter(my_tools, block_on_review=False)
```

### 方式 2: opt-in 参数

```python
from trpc_agent_sdk.tools import BashTool
tool = BashTool(enable_safety_guard=True)

from trpc_agent_sdk.code_executors import UnsafeLocalCodeExecutor
executor = UnsafeLocalCodeExecutor(enable_safety_guard=True)
```

### 方式 3: CodeExecutor 包装器

```python
from trpc_agent_sdk.tools.safety import SafeCodeExecutor
executor = SafeCodeExecutor(inner_executor=my_executor)
```

### 方式 4: 动态 ToolSet 包装器

```python
from trpc_agent_sdk.tools.safety import SafetyWrappedToolSet
toolset = SafetyWrappedToolSet(inner=mcp_toolset, block_on_review=True)
```

### 方式 5: 直接扫描

```python
from trpc_agent_sdk.tools.safety import SafetyScanner, ScanRequest, PolicyConfig
scanner = SafetyScanner(PolicyConfig.default())
report = scanner.scan(ScanRequest(script="rm -rf /", language="bash", tool_name="my_tool"))
```

### 方式 6: CLI 工具

```bash
python scripts/tool_safety_check.py script.sh              # exit 0/1/2
python scripts/tool_safety_check.py --block-on-review script.sh  # review exits 1
python scripts/tool_safety_check.py --json script.py       # JSON output
echo "rm -rf /" | python scripts/tool_safety_check.py --stdin
```

## 与沙箱的关系

Safety Guard 执行 **静态分析**，不是运行时沙箱。生产环境应组合使用：

- **Safety Guard** — 第一道防线，静态扫描拦截明显危险操作
- **沙箱（ContainerCodeExecutor / CubeSandbox）** — 最后一道防线，运行时隔离

静态分析无法替代运行时隔离：混淆代码、动态字符串拼接、间接调用可能绕过静态规则。

## 已知限制

- **误报**：安全的 `subprocess.run` 调用可能被标记。通过 `allowed_commands` 和 `network_allowlist` 策略调整。
- **漏报**：混淆代码（字符串拼接、base64 编码）、间接调用可绕过静态规则。
- **别名导入**：`from os import system` 现已检测。Python 解析失败不再降级放行，而是返回 `R007_PARSE_FAILURE` 并 fail-closed。
- **动态 URL**：通过字符串格式化构造的 URL 无法检查白名单。

## 扩展规则

### Python 规则

编辑 `trpc_agent_sdk/tools/safety/_rules.py`：
- `PYTHON_DANGEROUS_FILE_CALLS`、`PYTHON_DELETE_CALLS`、`PYTHON_NETWORK_CALLS`、`PYTHON_SYSTEM_CALLS`、`PYTHON_DYNAMIC_EXEC_CALLS`
- `PYTHON_INSTALL_PATTERNS`、`PYTHON_RESOURCE_PATTERNS`

AST 级检查：在 `_python_parser.py` 的 `_PythonVisitor` 中增加 `visit_*` 方法。

### Bash 规则

编辑 `trpc_agent_sdk/tools/safety/_rules.py`：
- `BASH_DANGEROUS_DELETE_PATTERNS`、`BASH_NETWORK_PATTERNS`、`BASH_SYSTEM_PATTERNS`、`BASH_RESOURCE_PATTERNS`、`BASH_SECRET_PATTERNS`

每条规则为 `(compiled_regex, rule_id, risk_level)` 元组。

### 上下文检查

扩展 `_scanner.py` 中的 `_scan_context_safety()` 方法。

### 脱敏模式

编辑 `_rules.py` 中的 `SECRET_VALUE_RE` 和 `SECRET_KEY_VALUE_RE`，或在 `tool_safety_policy.yaml` 的 `secret_patterns` 中添加自定义正则。
