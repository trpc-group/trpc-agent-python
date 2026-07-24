# Script Safety Guard — 完整复现提示词

> **用途**：将此提示词交给 AI 编程助手，即可从零复现 Script Safety Guard 模块的完整实现。
> **目标框架**：trpc-agent-python SDK
> **模块路径**：`trpc_agent_sdk/tools/safety/`

---

## 任务总述

请在 `trpc_agent_sdk/tools/safety/` 下实现一个 **Script Safety Guard** 模块，作为 AI Agent 运行时的脚本安全护栏。该模块在 LLM 生成的代码**执行前**进行静态分析，产出三级决策（ALLOW / NEEDS_HUMAN_REVIEW / DENY），拦截已知危险模式。

---

## 一、目录结构要求

请严格按照以下目录结构创建文件：

```
trpc_agent_sdk/tools/safety/
├── __init__.py              # 公开 API 统一导出
├── models.py                # Pydantic 数据模型
├── guard.py                 # ScriptSafetyGuard 编排引擎（核心入口）
├── policy.py                # PolicyConfig + YAML 加载 + 自动发现 + 合并逻辑
├── _metrics.py              # OTel 指标录入（Counter / Histogram）
├── scanner/
│   ├── __init__.py
│   ├── python_scanner.py    # Python AST 解析工具库
│   └── bash_scanner.py      # Bash 正则扫描工具库
├── rules/
│   ├── __init__.py          # 导入所有规则模块触发注册
│   ├── _base.py             # BaseRule ABC + RuleRegistry 单例 + @register_rule
│   ├── file_ops.py          # FS-001, FS-002
│   ├── network.py           # NET-001, NET-002
│   ├── process.py           # PROC-001, PROC-002
│   ├── dependency.py        # DEP-001, DEP-002
│   ├── resource.py          # RES-001, RES-002
│   └── secrets.py           # SEC-001, SEC-002
└── adapters/
    ├── __init__.py
    ├── filter_adapter.py    # ScriptSafetyFilter — Filter Chain 适配器
    └── wrapper_adapter.py   # SafeCodeExecutor — CodeExecutor Wrapper 适配器
```

---

## 二、核心设计原则（必须遵守）

### 2.1 三级决策模型
- `Decision` 枚举：`ALLOW`、`NEEDS_HUMAN_REVIEW`、`DENY`
- **Strictest-Wins 聚合**：所有 Finding 中最严格的决策为最终决策
  - DENY > NEEDS_HUMAN_REVIEW > ALLOW
  - 无 Finding 时 = ALLOW

### 2.2 Fail-Open 原则
- Guard 自身的任何异常（规则崩溃、OTel 不可用、文件写入失败）**绝不能阻断**主业务流程
- 规则 `scan()` 抛异常 → catch → 生成 `NEEDS_HUMAN_REVIEW` 的 error finding → 继续执行后续规则
- OTel SDK 缺失 → ImportError 被 catch → 静默跳过
- 报告/审计文件写入失败 → `logger.warning` → 不 raise

### 2.3 零配置可用
- 内置合理默认策略，无需外部文件即可工作
- 策略文件可选，采用自动发现机制

### 2.4 线程安全
- `ScriptSafetyGuard` 实例在 `__init__` 之后无可变状态，可跨线程共享
- `RuleRegistry` 是模块级单例，规则在 import 时一次性注册

### 2.5 Evidence 脱敏
- 审计日志中 evidence 截断 200 字符
- 正则 mask 敏感值（key/token/secret/password 模式 → `****`）
- 永远不在日志中暴露完整脚本内容

### 2.6 原子写入
- Report 文件使用 `tempfile.mkstemp()` + `os.replace()` 原子替换

---

## 三、models.py — 数据模型

使用 Pydantic BaseModel 实现以下模型：

### 枚举类型

```python
class RiskCategory(str, Enum):
    FILE_OPERATIONS = "file_operations"
    NETWORK = "network"
    PROCESS = "process"
    DEPENDENCY = "dependency"
    RESOURCE = "resource"
    SECRETS = "secrets"

class Severity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"

class Language(str, Enum):
    PYTHON = "python"
    BASH = "bash"
```

### 数据模型

1. **ToolMetadata** — 触发安全检查的工具元数据
   - `tool_name: str = ""`
   - `skill_name: str = ""`
   - `invocation_id: str = ""`
   - `agent_name: str = ""`
   - `user_id: str = ""`
   - `parameters: dict[str, Any] = {}`

2. **Finding** — 单条风险发现
   - `rule_id: str` — 如 "FS-001"
   - `category: RiskCategory`
   - `severity: Severity`
   - `decision: Decision`
   - `confidence: float = 1.0` (0~1)
   - `evidence: str = ""`
   - `line_number: int = 0`
   - `description: str = ""`
   - `recommendation: str = ""`

3. **SafetyCheckInput** — guard.check() 的输入
   - `script_content: str`
   - `language: Language`
   - `command_args: list[str] = []`
   - `working_directory: str = ""`
   - `environment_variables: dict[str, str] = {}`
   - `tool_metadata: ToolMetadata = ToolMetadata()`

4. **SafetyCheckResult** — guard.check() 的输出
   - `decision: Decision`
   - `findings: list[Finding] = []`
   - `scan_duration_ms: float = 0.0`
   - `scanned_language: Language`
   - `tool_name: str = ""`
   - `invocation_id: str = ""`
   - 属性 `max_severity` → 返回最高严重级别或 "none"
   - 属性 `is_blocked` → `decision == Decision.DENY`
   - 方法 `to_report_dict()` → 完整报告字典（含每条 finding 展开）
   - 方法 `to_audit_dict()` → 紧凑审计字典（event, tool_name, decision, risk_level, rule_ids, duration_ms, is_desensitized=True, is_blocked, findings_count）

5. **ScanContext** — 传给每条规则的上下文
   - `source_code: str`
   - `language: Language`
   - `ast_tree: Optional[Any] = None` (Python 的 ast.Module，Bash 为 None)
   - `lines: list[str] = []`
   - `working_directory: str = ""`
   - `environment_variables: dict[str, str] = {}`
   - `tool_metadata: ToolMetadata = ToolMetadata()`
   - `model_config = {"arbitrary_types_allowed": True}`
   - 类方法 `from_input(check_input, ast_tree=None)` → 从 SafetyCheckInput 构造

---

## 四、policy.py — 策略配置系统

### 子策略模型

1. **NetworkPolicy** — `allowed_domains: list[str]`, `override: bool = False`
   - **唯一具备白名单直通语义**：命中 = 不产生 Finding
2. **ProcessPolicy** — `allowed_commands: list[str]`, `override: bool = False`
   - 规则参数（非直通白名单）
3. **FileOperationsPolicy** — `forbidden_paths: list[str]`, `override: bool = False`
   - 规则参数
4. **ResourcePolicy** — `max_timeout_seconds: int = 300`, `max_output_size_mb: int = 100`
5. **ReportOutputConfig** — `enabled: bool = True`, `dir: str = "./.safety_reports"`, `filename_template: str = "{tool_name}_{timestamp}_report.json"`
6. **AuditOutputConfig** — `enabled: bool = True`, `file: str = "./.safety_reports/audit.jsonl"`
7. **OutputConfig** — `report: ReportOutputConfig`, `audit: AuditOutputConfig`
8. **PolicyConfig** — 顶层，组合上述所有子策略，`model_config = {"extra": "ignore"}`，`version: str = "1.0"`

### 自动发现逻辑

函数 `_auto_discover_policy() -> Optional[Path]`，优先级：
1. 环境变量 `TOOL_SAFETY_POLICY_PATH`
2. CWD/tool_safety_policy.yaml (或 .yml)
3. CWD/.safety/tool_safety_policy.yaml
4. CWD/config/tool_safety_policy.yaml

### 内置默认策略

函数 `_default_policy() -> PolicyConfig`：
```python
network.allowed_domains = [
    "api.openai.com", "*.openai.com", "*.googleapis.com", "*.anthropic.com",
    "*.githubusercontent.com", "github.com", "pypi.org", "*.python.org",
    "registry.npmjs.org", "*.huggingface.co"
]
process.allowed_commands = [
    "python3", "python", "node", "cat", "ls", "find", "grep",
    "echo", "head", "tail", "wc", "sort", "mkdir", "cp", "mv"
]
file_operations.forbidden_paths = [
    "/etc/", "~/.ssh/", "~/.aws/", "~/.gnupg/", "~/.config/", "~/.env", "/root/", "/var/log/"
]
resources.max_timeout_seconds = 300
resources.max_output_size_mb = 100
```

### 合并逻辑

函数 `_merge_list(default_list, user_list, override) -> list[str]`:
- `override=True` → 完全替换
- `override=False` → 追加+去重（保持顺序）

函数 `_merge_policies(default, user) -> PolicyConfig`：
- 每个列表字段独立合并
- 标量字段：用户值非默认时覆盖

### 公开 API

```python
def load_policy(path: Optional[str | Path] = None) -> PolicyConfig:
```
- path=None → 自动发现 → 未找到返回默认
- path 指定但不存在 → warning → 返回默认
- 解析失败 → warning → 返回默认
- 使用 `yaml.safe_load` (安全反序列化)
- 成功 → 合并用户策略与默认策略

---

## 五、scanner/ — 扫描工具库

### python_scanner.py

提供以下函数：

1. `safe_parse(source: str) -> Optional[ast.Module]` — 安全解析 AST，失败返回 None
2. `extract_calls(tree) -> list[ast.Call]` — 提取所有 Call 节点
3. `extract_imports(tree) -> list[tuple[str, Optional[str]]]` — 提取导入
4. `get_call_name(call: ast.Call) -> str` — 提取函数调用全限定名（如 "os.system"、"subprocess.run"）
5. `get_string_args(call: ast.Call) -> list[str]` — 提取字符串字面量参数
6. `get_string_value(node) -> Optional[str]` — 从 AST 表达式提取字符串值
7. `find_function_calls(tree, func_names: set[str]) -> list[ast.Call]` — 按名称集合查找调用
8. `find_string_assignments(tree) -> dict[str, str]` — 查找 `name = "string"` 形式的赋值

### bash_scanner.py

提供以下类型和函数：

1. **PatternMatch** (dataclass) — `line_number`, `line_content`, `matched_text`, `pattern_name`
2. **CompiledPatternSet** 类 — 构造时传入 `{name: regex_str}` 字典，预编译所有正则
   - `__init__(patterns: dict[str, str], flags=re.IGNORECASE)`
   - `match_line(line: str) -> list[tuple[str, re.Match]]`
3. `is_comment_line(line: str) -> bool` — 判断是否为 `#` 注释行
4. `strip_inline_comment(line: str) -> str` — 剥离行内注释（处理引号内的 #）
5. `scan_lines(source: str, patterns: CompiledPatternSet, skip_comments=True) -> list[PatternMatch]` — 逐行扫描
6. `extract_urls_from_line(line: str) -> list[str]` — 提取 http/https/ftp URL
7. `extract_domain_from_url(url: str) -> Optional[str]` — 从 URL 提取域名

---

## 六、rules/ — 规则系统

### _base.py — 基类与注册表

```python
class BaseRule(ABC):
    rule_id: str = ""
    category: RiskCategory = RiskCategory.PROCESS
    severity: Severity = Severity.MEDIUM
    languages: list[Language] = []  # 空=所有语言适用
    description: str = ""

    @abstractmethod
    def scan(self, ctx: ScanContext, policy: PolicyConfig | None = None) -> list[Finding]: ...

    def supports_language(self, language: Language) -> bool:
        if not self.languages:
            return True
        return language in self.languages


class RuleRegistry:  # 单例（__new__ 实现）
    def register(self, rule: BaseRule) -> None: ...
    def unregister(self, rule_id: str) -> None: ...
    def get_all(self) -> list[BaseRule]: ...
    def get_by_language(self, language: Language) -> list[BaseRule]: ...
    def get_by_category(self, category: RiskCategory) -> list[BaseRule]: ...
    def get_by_id(self, rule_id: str) -> BaseRule | None: ...
    def clear(self) -> None: ...  # 测试用
    @property
    def count(self) -> int: ...

rule_registry = RuleRegistry()  # 模块级单例

def register_rule(cls: type[BaseRule]) -> type[BaseRule]:
    """类装饰器：实例化并注册规则"""
```

### rules/__init__.py

导入所有规则模块触发 `@register_rule` 执行：
```python
from trpc_agent_sdk.tools.safety.rules import file_ops, network, process, dependency, resource, secrets
```

---

### 规则实现详情

#### file_ops.py

**FS-001 ForbiddenPathRule** (HIGH → DENY):
- 检测 Python 文件操作函数（open, os.remove, os.unlink, os.rmdir, os.removedirs, os.rename, os.replace, shutil.rmtree, shutil.move, shutil.copy, shutil.copy2, shutil.copytree, pathlib.Path.unlink/rmdir/write_text/write_bytes）的字符串参数
- 使用 `os.path.expanduser/expandvars` 展开路径后与 `policy.file_operations.forbidden_paths` 前缀匹配
- Bash: 逐行扫描是否包含禁止路径字符串

**FS-002 DestructiveFileOpRule** (MEDIUM → NEEDS_HUMAN_REVIEW):
- Python: 检测 os.remove, os.unlink, os.rmdir, os.removedirs, shutil.rmtree, pathlib.Path.unlink/rmdir
- Bash: 使用正则检测 `rm -rf`, `rm --recursive`, `rm ... / $`, `dd of=/dev/`, `mkfs`, `format`
- Bash 中 `rm_root`/`dd_of`/`mkfs` 升级为 HIGH+DENY

#### network.py

**NET-001 NetworkRequestRule** (HIGH → NEEDS_HUMAN_REVIEW):
- Python: 检测 requests.get/post/put/delete/patch/head/request, urllib.request.urlopen/urlretrieve, httpx.get/post/put/delete/Client/AsyncClient, aiohttp.ClientSession, http.client.HTTPConnection/HTTPSConnection
- 从字符串参数中提取域名，与 `policy.network.allowed_domains` 做 fnmatch 匹配
- 域名匹配白名单 → 跳过（**唯一的白名单直通语义**）
- 域名不匹配 → 产生 Finding
- 无法静态提取域名 → confidence=0.6 的 MEDIUM 级 Finding
- Bash: 使用 `extract_urls_from_line` 提取 URL，`extract_domain_from_url` 提取域名后同样比对白名单

**NET-002 RawSocketRule** (MEDIUM → NEEDS_HUMAN_REVIEW):
- Python: 检测 socket.socket, socket.create_connection
- Bash: 检测 nc, netcat, telnet（正则）

#### process.py

**PROC-001 ProcessExecutionRule** (HIGH → NEEDS_HUMAN_REVIEW):
- Python: 检测 os.system/popen/exec*/spawnl*/subprocess.run/call/check_call/check_output/Popen
- 从参数提取命令名（第一个字符串参数的第一个词），与 `policy.process.allowed_commands` 比对
- 不在允许列表中 → Finding
- 无法静态确定命令 → confidence=0.7 的 Finding
- Bash: 检测 eval, exec, source_remote(`source <(`), bash -c, sh -c, nohup, crontab, at, sudo, su, chmod suid

**PROC-002 ShellInjectionRule** (HIGH → DENY for eval/exec):
- Python:
  - os.system / os.popen → NEEDS_HUMAN_REVIEW（总是 shell 语义）
  - subprocess.* with `shell=True` → NEEDS_HUMAN_REVIEW
  - eval / exec / compile → **DENY**
- Bash:
  - eval → **DENY**
  - backtick 命令替换 → NEEDS_HUMAN_REVIEW (confidence=0.7)
  - 未引用变量展开 `$VAR` 在命令中 → 不生成 finding（过于常见）

#### dependency.py

**DEP-001 PackageInstallRule** (MEDIUM → NEEDS_HUMAN_REVIEW):
- Python: 在 subprocess/os.system 调用的字符串参数中搜索安装关键词（pip install, pip3 install, conda install, npm install/i, yarn add, pnpm add, apt install, apt-get install, yum install, brew install, gem install, cargo install）
- Bash: 正则检测对应命令模式

**DEP-002 UntrustedSourceRule** (HIGH → DENY for curl|bash):
- Python: pip install 参数中含 http://, https://, git+, --index-url, --extra-index-url → DENY
- Bash 正则:
  - pip install + URL → NEEDS_HUMAN_REVIEW
  - pip install + git+ → NEEDS_HUMAN_REVIEW
  - pip install + --index-url/--extra-index-url → NEEDS_HUMAN_REVIEW
  - `curl ... | bash/sh/zsh` → **DENY**
  - `wget ... | bash/sh/zsh` → **DENY**
  - npm install + URL → NEEDS_HUMAN_REVIEW

#### resource.py

**RES-001 ForkBombRule** (HIGH → DENY for fork bomb):
- Python:
  - `while True:` 无 break/return → NEEDS_HUMAN_REVIEW
  - `os.fork()` → **DENY**
- Bash 正则:
  - fork bomb 模式 `:(){ :|:& };:` → **DENY**
  - `while true/1/: ; do` → NEEDS_HUMAN_REVIEW
  - `yes |` → NEEDS_HUMAN_REVIEW
  - `dd if=/dev/zero`/`dd if=/dev/urandom` → NEEDS_HUMAN_REVIEW
  - `head -c NNg /dev/` → NEEDS_HUMAN_REVIEW

**RES-002 ResourceConsumptionRule** (MEDIUM → NEEDS_HUMAN_REVIEW):
- Python:
  - 乘法运算右操作数 > 10,000,000 → NEEDS_HUMAN_REVIEW
  - multiprocessing.Process / threading.Thread / os.fork → LOW + ALLOW (confidence=0.5, 仅提示)
- Bash: dd bs=NNg, fallocate -l NNg, truncate -s NNg

#### secrets.py

**SEC-001 HardcodedSecretsRule** (HIGH → DENY):
- Python:
  - 检查所有字符串赋值：变量名匹配密钥模式（password, secret, token, api_key, access_key, auth, private_key, signing_key, encryption_key, credentials, client_secret, db_pass/password/uri/url, connection_string）且值 >= 8 字符 → DENY
  - 遍历所有字符串常量：匹配已知格式（AWS key `AKIA...`, GitHub token `ghp_...`/`github_pat_...`, Slack token `xox[bpors]-...`, Generic key `sk-...`(32+), JWT `eyJ...`, Private key header, Basic auth header, Bearer token(20+)）→ DENY
  - 排除明显占位符（xxx, placeholder, your_, changeme, todo, fixme, example）和短于 8 字符的值
- Bash: 正则检测 PASSWORD/TOKEN/SECRET/API_KEY/ACCESS_KEY 的赋值、curl --user、export 敏感变量；同时逐行做值模式匹配

**SEC-002 EnvLeakageRule** (MEDIUM → NEEDS_HUMAN_REVIEW):
- Python: 检测 print/logging.info/debug/logger.info/debug 参数中包含 `os.environ`
- Bash: 检测 `echo $PASSWORD`/`$TOKEN` 等、`printenv`、`env` 单独一行、`set` 单独一行

---

## 七、guard.py — 编排引擎

```python
class ScriptSafetyGuard:
    def __init__(self, policy: Optional[PolicyConfig] = None):
        self._policy = policy if policy is not None else load_policy()

    @property
    def policy(self) -> PolicyConfig: ...

    def check(self, input: SafetyCheckInput) -> SafetyCheckResult:
        """完整管道：
        1. Parse（Python → AST, Bash → None）
           - Python AST 失败 → 生成 GUARD-001 Finding (LOW, NEEDS_HUMAN_REVIEW, confidence=0.8)
        2. 构建 ScanContext
        3. 从 rule_registry.get_by_language() 获取适用规则
        4. 逐条执行 rule.scan(ctx, self._policy)
           - 异常 → catch → 生成 NEEDS_HUMAN_REVIEW Finding (MEDIUM, confidence=0.5)
        5. _aggregate_decision (strictest-wins)
        6. 计算耗时
        7. 构建 SafetyCheckResult
        8. _emit_audit_log (Python logger JSON)
        9. _record_otel (span attributes + metrics)
        10. _write_report_and_audit (文件输出，由 policy.output 控制)
        """
```

### 辅助函数

- `_aggregate_decision(findings) -> Decision` — strictest-wins
- `_emit_audit_log(input, result)` — 输出到 logger `trpc_agent_sdk.tools.safety.audit`
- `_record_otel(input, result)` — OTel span 属性前缀 `trpc.python.agent.tool.safety.*`；metrics 通过 `_metrics` 模块
- `_truncate(text, max_len=200)` — 截断
- `_sanitize_evidence(evidence)` — 截断 + 正则 mask（`key|token|secret|password|passwd|api_key|apikey|auth` 后跟 `=` 或 `:` + 8+字符 → `****`）
- `_write_report_and_audit(policy, input, result)` — 原子写入报告 + 追加审计 JSONL

---

## 八、_metrics.py — OTel 指标

- Meter name: `trpc.python.agent`
- 懒初始化：首次调用时 import opentelemetry，失败则静默禁用
- 三个指标：
  - `tool.safety.check_count` (Counter, attributes: decision, language, tool_name)
  - `tool.safety.scan_duration` (Histogram, unit=ms, attributes: language, decision)
  - `tool.safety.rule_hit_count` (Counter, attributes: rule_id, category, severity)
- 公开函数：`record_check()`, `record_scan_duration()`, `record_rule_hit()`

---

## 九、adapters/ — 适配器层

### filter_adapter.py — ScriptSafetyFilter

```python
@register_tool_filter("script_safety")
class ScriptSafetyFilter(BaseFilter):
    def __init__(self, policy=None, block_on_review=False): ...
    async def _before(self, ctx, req, rsp): ...
    async def _after(self, ctx, req, rsp): return None  # no-op
```

行为：
- req 不是 dict → 直接 return（防御性编程）
- 从 req 提取 script：搜索 key `script_content` > `script` > `code` > `source_code` > `source`
- 从 req 提取 language：搜索 key `language` > `lang` > `script_language`，默认 python
- 从 ctx 提取 ToolMetadata
- 调用 `guard.check()`
  - 异常 → log + return（fail-open）
- Decision.DENY → `rsp.is_continue = False`, `rsp.error = SafetyCheckBlockedError(result)`
- NEEDS_HUMAN_REVIEW + block_on_review=True → 同样阻断

自定义异常类 `SafetyCheckBlockedError(Exception)`，携带 result。

### wrapper_adapter.py — SafeCodeExecutor

```python
class SafeCodeExecutor(BaseCodeExecutor):  # Pydantic model
    inner: BaseCodeExecutor
    policy: Optional[PolicyConfig] = None
    block_on_review: bool = False

    async def execute_code(self, invocation_context, code_execution_input) -> CodeExecutionResult:
        # 逐 code_block 检查
        # 任一 DENY → 返回 error CodeExecutionResult（不执行）
        # 全部通过 → await self.inner.execute_code(...)
```

- 依赖框架类型：`BaseCodeExecutor`, `CodeExecutionInput`, `CodeBlock`, `CodeExecutionResult`, `InvocationContext`
- 从 `code_execution_input.code_blocks` 或退化到 `code_execution_input.code` 获取代码块
- 语言标准化：bash/sh/shell/zsh → BASH，其他 → PYTHON
- 阻断消息格式：
  ```
  Script Safety Guard blocked code execution.
  Decision: DENY
  Findings (N):
    - [RULE-ID] SEVERITY: description
    ...
  ```

---

## 十、__init__.py — 公开 API

统一导出：
```python
__all__ = [
    "ScriptSafetyGuard",
    "ScriptSafetyFilter", "SafeCodeExecutor",
    "Decision", "Finding", "Language", "RiskCategory",
    "SafetyCheckInput", "SafetyCheckResult", "ScanContext", "Severity", "ToolMetadata",
    "ENV_POLICY_PATH", "AuditOutputConfig", "FileOperationsPolicy", "NetworkPolicy",
    "OutputConfig", "PolicyConfig", "ProcessPolicy", "ReportOutputConfig", "ResourcePolicy",
    "load_policy",
]
```

---

## 十一、测试要求

在 `tests/tools/safety/` 下创建以下测试文件：

1. **test_guard.py** — Guard 初始化、check() 管道、决策聚合、审计日志、OTel、helper 函数、规则异常处理
2. **test_policy.py** — 默认策略完整性、列表合并、策略覆盖、YAML 加载（正常/异常/空/非法）、自动发现优先级、环境变量
3. **test_python_scanner.py** — safe_parse、find_function_calls、get_string_args
4. **test_bash_scanner.py** — is_comment_line、strip_inline_comment、CompiledPatternSet、scan_lines、extract_urls
5. **test_rules_base.py** — BaseRule 抽象、RuleRegistry CRUD、语言/类别过滤
6. **test_models.py** — Pydantic 模型构造、序列化、to_report_dict、to_audit_dict
7. **test_filter_adapter.py** — Filter 拦截逻辑、script 提取、language 检测、block_on_review
8. **test_wrapper_adapter.py** — Wrapper 执行逻辑、多 block 检查、阻断结果生成
9. **test_integration.py** — 端到端场景（无 mock）：
   - 安全脚本放行（print, requests 白名单域名, 允许命令）
   - 危险脚本拦截（rm -rf, eval, hardcoded key, fork bomb）
   - 自定义策略生效（添加/替换白名单）
   - 多规则联合触发
   - Bash 脚本全链路
   - 适配器集成
   - 空脚本/语法错误/大脚本

测试 fixtures 放在 `tests/tools/safety/fixtures/`：
- `sample_policy.yaml` — 标准扩展策略
- `strict_policy.yaml` — 严格策略（override: true，最小白名单）

---

## 十二、示例文件

在 `examples/safety/` 下创建：
1. `tool_safety_policy.yaml` — 带完整中英文注释的策略配置示例
2. `tool_safety_report.json` — Guard 结构化报告输出示例
3. `tool_safety_audit.jsonl` — JSONL 审计日志输出示例

---

## 十三、文档

在 `docs/mkdocs/zh/script_safety_guard.md` 和 `docs/mkdocs/en/script_safety_guard.md` 下编写完整设计文档，包含：
1. 定位与目标
2. 整体架构图（ASCII）
3. 组件详细说明
4. 策略配置系统
5. 规则开发规范
6. 适配器集成模式
7. 可观测性设计
8. 安全纵深模型（Guard 与 Sandbox / Filter / Telemetry / CodeExecutor 的关系）
9. 使用示例

---

## 十四、依赖项

- `pydantic` (已在 SDK 中)
- `pyyaml` (策略加载)
- `opentelemetry-api` (可选，OTel 指标/span，缺失时静默降级)

---

## 十五、安全注意事项

- **Guard 代码本身**：不能 eval、不能 exec、不反序列化不可信数据
- `policy.py` 使用 `yaml.safe_load`（防止 YAML 反序列化攻击）
- Evidence 输出始终脱敏（不在日志/报告中暴露完整脚本或真实密钥）
- Filter adapter 对 non-dict 的 req 参数直接 return（防御性编程）
- 报告文件使用原子写入（tempfile + os.replace），避免竞争条件下的半截文件

---

## 十六、关键实现细节备忘

### guard.py 中的 GUARD-001 生成条件
- 仅在 `language == PYTHON` 且 `script_content.strip()` 非空但 `safe_parse()` 返回 None 时生成
- severity=LOW, decision=NEEDS_HUMAN_REVIEW, confidence=0.8

### 规则 scan() 签名
```python
def scan(self, ctx: ScanContext, policy: PolicyConfig | None = None) -> list[Finding]:
```
注意 policy 是可选参数（某些规则不依赖策略）。

### Bash scanner 的 `CompiledPatternSet` 默认 flags=re.IGNORECASE
- 所有 Bash 正则匹配默认大小写不敏感

### filter_adapter 使用框架 API
```python
from trpc_agent_sdk.abc import FilterResult, FilterType
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.filter import BaseFilter, register_tool_filter
```

### wrapper_adapter 使用框架 API
```python
from trpc_agent_sdk.code_executors._base_code_executor import BaseCodeExecutor
from trpc_agent_sdk.code_executors._types import CodeExecutionInput, CodeBlock, create_code_execution_result
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.types import CodeExecutionResult
```

---

## 十七、验收标准

1. `pytest tests/tools/safety/ -v` 全部通过
2. Guard 无外部策略文件时，使用内置默认策略正常工作
3. 安全脚本（如 `print("hello")`）→ ALLOW
4. 危险脚本（如 `eval(input())`）→ DENY
5. 规则异常时 → 不崩溃，生成 NEEDS_HUMAN_REVIEW
6. OTel 不可用时 → 静默降级
7. 文件写入失败时 → 不阻断
8. 两种适配器模式均可正常阻断/放行
