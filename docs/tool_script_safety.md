# Tool Script Safety Guard

`trpc_agent_sdk.tools.safety` 在脚本执行前进行快速静态扫描，支持 Python 与 Bash，输出
`allow`、`deny` 或 `needs_human_review`。规则覆盖危险文件操作、敏感路径、网络外连、进程创建、
依赖安装、资源滥用及秘密泄漏。规则命中包含风险类型、级别、规则 ID、证据、行号和处理建议。

## 使用与接入

```python
from trpc_agent_sdk.tools.safety import JsonlAuditSink, ToolSafetyGuard
from trpc_agent_sdk.tools.safety import ToolSafetyRequest, ToolScriptSafetyScanner

scanner = ToolScriptSafetyScanner.from_policy_file("tool_safety_policy.yaml")
guard = ToolSafetyGuard(scanner, JsonlAuditSink("tool_safety_audit.jsonl"))
result = await guard.run(
    ToolSafetyRequest(tool_name="python", script=code, language="python"),
    lambda: execute_in_sandbox(code),
)
```

所有 `BaseTool` 均经过 Filter 链。向 Tool、MCP Tool 或 Skill 背后的 Tool 添加注册名
`tool_script_safety`，即可在 `_run_async_impl` 或 CodeExecutor 真正运行前检查 `script`、`code` 或
`command` 参数。`needs_human_review` 默认暂停执行；调用方完成审批后可传
`human_approved=True`。命令行可运行：

```bash
python scripts/tool_safety_check.py --file job.py --language python
python scripts/tool_safety_check.py --command "curl https://api.example.com/health"
```

策略 YAML 可直接修改域名白名单、允许命令、禁止路径、超时和最大输出大小，无需改代码。
每次 Guard 检查可写 JSONL 审计事件，并在当前 OpenTelemetry span 设置
`tool.safety.decision`、`tool.safety.risk_level`、`tool.safety.rule_id`、耗时和拦截状态。

## 安全边界与扩展

这是执行前的 Filter，不是沙箱。静态规则会有误报，也可能被编码、动态拼接、别名、间接导入、
运行时下载或未知解释器绕过；它不能限制 CPU、内存、文件系统、网络和子进程。生产环境仍应让
CodeExecutor 在最小权限沙箱内运行，并同时设置进程超时、输出上限、网络策略和凭据隔离。
新增规则时在 scanner 中追加窄匹配并返回稳定 rule ID，同时添加安全与危险对照样本；高风险规则
应优先拒绝，不确定行为应进入人工复核。
