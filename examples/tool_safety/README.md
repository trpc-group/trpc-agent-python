# Tool Safety Guard 示例

本示例说明 Tool Safety Guard 的设计目标、使用方式和交付物。它用于在工具调用、代码执行、技能执行、脚本扫描等入口执行确定性的安全检查，并输出结构化结果、Telemetry 属性和审计记录。

## 背景与设计目标

Agent 工具通常可以执行文件操作、Shell 命令、网络请求或依赖安装。此类能力很有用，但也会带来误删文件、泄露密钥、访问非预期网络、无限循环或资源滥用等风险。

Tool Safety Guard 的目标是：

- 在高风险操作执行前给出确定性判断。
- 让工具调用、CodeExecutor、Skill 执行和 CLI 扫描复用同一套审查逻辑。
- 为 CI 和开发流程提供结构化输出与明确退出码。
- 记录可观测属性和审计事件，便于排查与合规留痕。
- 保持轻量：不替代沙箱，不引入新的 Telemetry 框架。

## 整体架构

```text
SafetyReviewer
  ↓
Rule
  ↓
Policy
  ↓
ToolSafetyFilter
CodeExecutor Wrapper
Skill Wrapper
  ↓
Telemetry
Audit
```

- `SafetyReviewer` 是统一入口，接收待检查文本、动作类型和工具名，返回结构化 review。
- `Rule` 提供确定性模式匹配。
- `Policy` 提供 allowlist、blocked path 和风险等级配置。
- `ToolSafetyFilter` 用于已有工具过滤器链。
- `CodeExecutor Wrapper` 和 `Skill Wrapper` 用于没有 Filter 能力的执行入口。
- Telemetry 将安全判断写入当前 OpenTelemetry span。
- Audit 用于保存离线审计记录。

## Rule 分类

### 文件操作

文件类规则关注破坏性删除、敏感路径读取和大文件写入。例如删除目录、访问 `.env`、访问 SSH 私钥路径，或写入异常大的文件内容。

### 网络访问

网络类规则关注直接访问外部域名、使用非 allowlist 域名、`wget`、原始 socket、`aiohttp` 客户端等行为。允许访问的域名应通过 Policy 显式配置。

### 系统命令

系统命令类规则关注 `os.system`、Python 子进程调用、Shell 管道、命令串联、`sudo`、`systemctl`、部署或生产环境关键字等高风险模式。

### 依赖安装

依赖安装类规则关注 `pip install`、`npm install`、`apt install` 等会修改环境的命令。默认结果通常是 `needs_human_review`，由人确认是否允许继续。

### 资源滥用

资源类规则关注无限循环、过高并发、过大文件写入、递归进程生成等可能导致资源耗尽的行为。

### 敏感信息泄露

敏感信息类规则关注打印环境变量、token、password、secret、api key 等内容，避免工具输出把凭据带入模型上下文、日志或审计系统。

## Policy 配置说明

示例 Policy 位于 [tool_safety_policy.yaml](./tool_safety_policy.yaml)。

常用字段：

- `allowed_domains`：允许访问的网络域名。域名会做规范化处理，子域名可被匹配。
- `blocked_paths`：按规则 ID 配置禁止读取或访问的路径片段。
- `allowed_commands`：保留给调用方或上层执行器使用的命令 allowlist。
- `max_timeout`：保留给调用方或上层执行器使用的最大超时配置。
- `max_output_size`：保留给调用方或上层执行器使用的最大输出大小配置。
- `risk_levels`：按规则 ID 覆盖风险等级。

最小示例：

```yaml
allowed_domains:
  - api.example.com

blocked_paths:
  read_dotenv:
    - ".env"
  read_ssh:
    - "~/.ssh"

risk_levels:
  network_not_allowlisted: critical
```

## CLI 使用示例

独立扫描命令位于 `scripts/tool_safety_check.py`。

扫描 Python 脚本：

```bash
python scripts/tool_safety_check.py example.py
```

扫描 Bash 脚本：

```bash
python scripts/tool_safety_check.py example.sh
```

指定 Policy：

```bash
python scripts/tool_safety_check.py example.sh --policy examples/tool_safety/tool_safety_policy.yaml
```

输出 text 格式：

```bash
python scripts/tool_safety_check.py example.sh --format text
```

写入 JSON report 文件：

```bash
python scripts/tool_safety_check.py example.sh --output tool_safety_report.json
```

退出码约定：

| Decision | Exit Code | 含义 |
| --- | ---: | --- |
| `allow` | 0 | 可继续执行 |
| `deny` | 1 | 阻断，CI 应失败 |
| `needs_human_review` | 2 | 需要人工审核 |

## 如何新增 Rule

新增 Rule 时应保持规则小而明确：

1. 明确风险场景和期望 decision。
2. 添加稳定的 `rule_id`、`finding`、`recommendation` 和匹配模式。
3. 在 Policy 的 `risk_levels` 中补充默认风险等级。
4. 增加 allow、deny 或 `needs_human_review` 的单元测试。
5. 确认 CLI、Filter、Wrapper 都通过 `SafetyReviewer` 自动复用该规则。

Rule 不应承担执行隔离职责，也不应读取系统状态。它只做输入文本审查。

## Tool Filter 与 Wrapper 的区别

`ToolSafetyFilter` 用于已经接入框架工具过滤器链的 `BaseTool`。它在工具执行前运行，命中阻断时返回结构化工具错误。

Wrapper 用于没有 Filter 能力的入口，例如直接调用 `CodeExecutor.execute_code()`，或直接运行某个 Skill runner。Wrapper 通过组合方式包住原执行入口，不改变底层执行器。

两者的共同点：

- 都复用 `SafetyReviewer`。
- 都复用同一套 Rule 和 Policy。
- 都输出相同风格的安全 decision。
- 都写入相同的 Telemetry attributes。

## Telemetry

安全检查完成后，会向当前 OpenTelemetry span 写入以下 attributes：

- `tool.safety.decision`
- `tool.safety.risk_level`
- `tool.safety.rule_id`

如果当前环境未启用 OpenTelemetry，写入会退化为 no-op，不影响工具执行或 CLI 扫描。

## Audit

示例审计文件位于 [tool_safety_audit.jsonl](./tool_safety_audit.jsonl)。每行是一条 JSON 记录，便于流式写入和日志系统采集。

稳定字段包括：

- `tool_name`
- `decision`
- `risk_level`
- `rule_id`
- `blocked`
- `latency`
- `timestamp`
- `input_sha256`

示例 report 位于 [tool_safety_report.json](./tool_safety_report.json)，包含 `allow`、`deny`、`needs_human_review` 三类结果，可作为 README 或 Issue 的结构化输出示例。

## 已知限制

### 误报

规则基于确定性模式匹配，可能把安全的命令片段判为高风险。例如文档中展示的危险命令、测试字符串或被转义的示例代码。

### 漏报

规则无法覆盖所有语言语义、动态拼接、编码混淆、间接调用或运行时生成命令。复杂攻击可能绕过静态文本匹配。

### 绕过风险

模型或用户可以尝试通过变量拼接、base64、下载后执行、跨文件组合等方式绕过规则。Policy allowlist 也可能因配置过宽降低防护效果。

## 为什么 Safety Guard 不能替代 Sandbox

Safety Guard 是执行前的静态审查层，适合快速阻断明显风险和输出可观测证据。它不能提供进程隔离、文件系统隔离、网络隔离、权限隔离或资源配额。

生产环境仍应使用 Sandbox、容器、只读挂载、网络策略、最小权限凭据、资源限制和人工审核流程。Safety Guard 应作为 Sandbox 之前的一层防线，而不是 Sandbox 的替代品。
