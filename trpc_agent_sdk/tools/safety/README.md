# Tool Script Safety Guard 设计说明

## 概述

Tool Script Safety Guard 是 tRPC-Agent-Python 框架的安全检查系统，用于在 Tool、MCP Tool、Skill 和 CodeExecutor 执行脚本之前进行静态安全扫描。

## 规则体系

### 7 条风险规则

| 规则 ID | 风险类型 | 默认决策 | 默认风险等级 |
|---------|---------|---------|-------------|
| R001 | 危险文件操作 | DENY | CRITICAL |
| R002 | 敏感文件读取 | DENY | HIGH |
| R003 | 非白名单网络外连 | DENY | HIGH |
| R004 | 进程/系统命令执行 | DENY | HIGH |
| R005 | 依赖安装 | DENY | HIGH |
| R006 | 资源滥用 | DENY | MEDIUM |
| R007 | 敏感信息泄漏 | DENY | CRITICAL |

### 决策逻辑

```
CRITICAL/HIGH 匹配 → DENY (拦截执行)
MEDIUM 匹配       → NEEDS_HUMAN_REVIEW (需人工审核)
无匹配            → 默认策略 (默认: NEEDS_HUMAN_REVIEW)
LOW 匹配          → ALLOW (放行)
```

## 接入方式

### 方式一：Filter（推荐）

```python
from trpc_agent_sdk.tools import BashTool

# 在创建 Tool 时启用 safety_filter
tool = BashTool(filters_name=["safety_filter"])
```

SafetyFilter 自动注册为 `FilterType.TOOL`，通过 `BaseTool._run_filters()` 集成到执行链路中。高危脚本在 `_before()` 阶段被拦截，`FilterResult.is_continue` 设为 `False`。

### 方式二：Wrapper（独立封装）

```python
from trpc_agent_sdk.tools.safety import SafetyWrapper

wrapper = SafetyWrapper()
result = await wrapper.run_safe(
    tool_name="Bash",
    script_content="rm -rf /",
    execute_fn=lambda: run_command("rm -rf /"),
)
if result["blocked"]:
    print(f"Blocked: {result['error']}")
```

## 与现有系统的关系

### Safety Guard vs 沙箱隔离

| 维度 | Safety Guard | 沙箱隔离 (CodeExecutor 容器) |
|------|-------------|---------------------------|
| 时机 | 执行前静态扫描 | 执行时运行时隔离 |
| 方式 | 规则匹配 + AST 分析 | Docker 容器 / e2b 沙箱 |
| 作用 | 阻止已知危险模式 | 限制未知恶意行为的破坏范围 |
| 局限 | 无法检测混淆/动态代码 | 无法检测语义级别的恶意逻辑 |
| 关系 | **互补**：先过安检，再进沙箱 |

**Safety Guard 不能替代沙箱隔离**，因为：
- 静态分析无法检测编码、加密、反射调用等混淆手段
- 沙箱提供的是运行时的资源隔离（网络、文件系统、进程）
- 最佳实践是两者结合：Safety Guard 做前置拦截，CodeExecutor 容器做深度隔离

### Safety Guard vs Filter 系统

SafetyFilter 是 `FilterType.TOOL` 类型的一个 Filter 实现，通过 `BaseTool._run_filters()` 自动集成到 Tool 执行链路中。

### Safety Guard vs Telemetry

SafetyFilter 通过 `AuditEvent.to_otel_attributes()` 向 OpenTelemetry span 写入以下属性：

```
tool.safety.decision    = "DENY"
tool.safety.risk_level  = "CRITICAL"
tool.safety.rule_id     = "R001"
tool.safety.blocked     = "true"
tool.safety.masked      = "false"
tool.safety.duration_ms = "12.34"
```

### Safety Guard vs CodeExecutor

CodeExecutor 使用 Docker 容器或 e2b 沙箱进行代码执行。Safety Guard 可以扫描 CodeExecutor 要执行的代码，在送入沙箱之前先做安全检查。两者叠加提供纵深防御。

## 如何扩展新规则

1. 在 `tool_safety_policy.yaml` 的 `rules` 下新增一个规则块，定义 `enabled`, `decision`, `risk_level`, `patterns`
2. 如果新模式需要新的检测逻辑（非纯正则/AST），在 `_bash_scanner.py` 或 `_python_scanner.py` 中新增扫描方法
3. 在 `_scanner.py` 的 `SafetyScanner.scan()` 中调用新方法
4. 在 `_types.py` 的 `RiskCategory` 枚举中新增对应类别（可选）
5. 添加测试样本和单元测试

## 已知限制

1. **静态分析无法检测运行时行为**：混淆代码、动态生成命令、反射调用、base64 解码执行可绕过
2. **不能替代沙箱隔离**：这是前置检查，不是运行时隔离——沙箱（CodeExecutor 的容器模式）提供更深层的安全
3. **误报风险**：`while True` 可能是合法长轮询，`cat /etc/passwd` 可能是测试环境
4. **漏报风险**：编码后的 payload、base64 解码执行的命令、动态 import 无法静态检测
5. **仅覆盖脚本内容**：不检测 Tool 本身的行为，只检测 Tool 要执行的脚本/命令参数
6. **规则匹配是线性扫描**：复杂脚本可能触发多条规则，需要人工综合判断

## 配置策略文件

`tool_safety_policy.yaml` 位于项目根目录，修改后无需重启应用，调用 `SafetyPolicy.reload()` 即可热加载。

```yaml
# 修改白名单域名
allowed_domains:
  - "api.openai.com"
  - "my-company-internal-api.com"
```

## 模块结构

```
trpc_agent_sdk/tools/safety/
├── __init__.py              # 模块入口，导出公开 API
├── _types.py                # 数据模型（枚举 + 数据类）
├── _policy.py               # 策略文件加载器
├── _bash_scanner.py         # Bash 脚本扫描器（正则）
├── _python_scanner.py       # Python 脚本扫描器（AST）
├── _scanner.py              # 扫描编排引擎
├── _audit.py                # 审计日志 + OTel 埋点
├── _safety_filter.py        # SafetyFilter（Filter 集成）
├── _wrapper.py              # 独立 Wrapper 接入
├── tool_safety_policy.yaml  # 可配置策略文件
├── tool_safety_report.json  # 示例输出报告
├── tool_safety_audit.jsonl  # 示例审计日志
└── README.md                # 本设计文档
```