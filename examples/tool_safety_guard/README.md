# Tool Safety Guard

This example shows how to add a static safety scan before a tool or code executor runs script-like input.

## Tool Filter

```python
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools.safety import ToolSafetyFilter


def run_command(command: str):
    return {"ran": command}


tool = FunctionTool(
    run_command,
    filters=[
        ToolSafetyFilter(policy_path="examples/tool_safety_guard/tool_safety_policy.yaml"),
    ],
)
```

If you prefer `filters_name=["tool_safety_guard"]`, import `trpc_agent_sdk.tools.safety` first so the registered filter is available.

## Code Executor Wrapper

```python
from trpc_agent_sdk.code_executors.local import UnsafeLocalCodeExecutor
from trpc_agent_sdk.tools.safety import SafetyGuardedCodeExecutor


code_executor = SafetyGuardedCodeExecutor(
    delegate=UnsafeLocalCodeExecutor(),
    policy_path="examples/tool_safety_guard/tool_safety_policy.yaml",
)
```

## What It Checks

The guard scans Python and Bash-like inputs for dangerous file operations, secret file access, non-whitelisted network egress, subprocess and shell patterns, dependency installation, resource abuse, and sensitive output.

Reports include `decision`, `risk_level`, `rule_id`, `evidence`, and `recommendation`. Audit events are written as JSONL and the filter sets current OpenTelemetry span attributes such as `tool.safety.decision` and `tool.safety.rule_ids`.

## Limits

This guard is a pre-execution static scan. It can have false positives, false negatives, and bypasses through obfuscation or dynamic code construction. It does not replace sandboxing, container isolation, OS permissions, network controls, timeouts, or resource limits. Use it as an early policy and observability layer before a properly isolated runtime.
