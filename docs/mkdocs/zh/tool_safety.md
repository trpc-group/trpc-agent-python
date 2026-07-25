# Tool 脚本安全

`trpc_agent_sdk.tools.safety` 为 Python 和 Bash 提供执行前静态扫描、策略决策、
Tool Filter 与 CodeExecutor 接入、审计事件和 OpenTelemetry 属性。

## 架构

安全守卫由四个独立部分组成：

1. `ToolSafetyScanner` 将 `SafetyScanRequest` 转换为结构化 `SafetyReport`。
2. `ToolScriptSafetyFilter` 在 Tool、MCP Tool 或 Skill 的实现运行前拦截危险参数。
3. `SafetyGuardedCodeExecutor` 在委托现有本地、容器或远程执行器前扫描代码块。
4. 审计 Sink 与 span attributes 将决策提供给监控系统。

默认决策优先级为 `deny`、`needs_human_review`、`allow`。人工复核结果默认阻断；
应用只有在核对准确的脚本哈希和报告后，才能显式继续执行。

## 配置

加载严格校验的 YAML 策略：

```python
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import ToolSafetyScanner

policy = ToolSafetyPolicy.from_yaml("tool_safety_policy.yaml")
scanner = ToolSafetyScanner(policy)
```

策略可配置白名单域名与命令、禁止路径、超时与输出限制、资源风险阈值、禁用规则，
以及逐规则的决策和风险等级覆盖。完整示例见
[`examples/tool_safety_guard/tool_safety_policy.yaml`](https://github.com/trpc-group/trpc-agent-python/tree/main/examples/tool_safety_guard/tool_safety_policy.yaml)。

## 接入

通过 Tool 的 `filters` 列表挂载 `ToolScriptSafetyFilter`。默认提取器识别
`script`、`code`、`command`、`cwd`、`env`、`timeout` 等常见执行参数；
其他参数结构可提供自定义提取器。

使用 `SafetyGuardedCodeExecutor` 包装 CodeExecutor。wrapper 会保留委托执行器的
配置，在委托前拒绝不允许的代码，为每个扫描代码块输出事件，并限制返回内容大小。

完整可运行示例、12 个公开扫描样本、结构化报告与审计日志位于
[`examples/tool_safety_guard`](https://github.com/trpc-group/trpc-agent-python/tree/main/examples/tool_safety_guard)。

## 验收验证

在仓库根目录运行公开扫描器与专项测试：

```bash
python examples/tool_safety_guard/tool_safety_check.py \
  --report /tmp/tool_safety_report.json \
  --audit /tmp/tool_safety_audit.jsonl
pytest -q tests/tools/safety
```

12 个公开样本的结果为 2 个 `allow`、7 个 `deny` 和 3 个
`needs_human_review`。专项测试共 47 条，其中包含 500 行脚本的扫描性能断言。

## 安全边界

静态扫描不能替代沙箱。混淆、动态生成代码、运行时下载、原生二进制、复杂 shell
展开、符号链接竞态、DNS rebinding 或依赖内部行为都可能绕过规则，规则也可能产生
误报。

生产环境仍必须隔离网络、挂载目录、凭据、进程身份和系统调用，并限制 CPU、内存、
磁盘、PID、时间和输出。安全守卫用于提前减少明显危险执行并提高可解释性与可审计性；
沙箱负责约束只能在运行时观察到的行为。
