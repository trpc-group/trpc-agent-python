# Issue #90 Usage Guide: Tool Safety Scanner

## Quick Start

### 1. Basic Command Scanning

```python
from trpc_agent_sdk.tools.safety import scan, default_policy, Request, DECISION_DENY

policy = default_policy()

# Safe command → ALLOW
report = scan(Request(
    tool_name="workspace_exec",
    backend="workspaceexec",
    command="go test ./...",
), policy)
assert report.decision == "allow"

# Dangerous command → DENY
report = scan(Request(
    tool_name="workspace_exec",
    backend="workspaceexec",
    command="rm -rf /",
), policy)
assert report.decision == "deny"
assert report.rule_id == "dangerous.rm_rf"
```

### 2. Code Block Scanning

```python
from trpc_agent_sdk.tools.safety import scan, default_policy, Request, CodeBlock

policy = default_policy()

report = scan(Request(
    tool_name="execute_code",
    backend="codeexec",
    code_blocks=[CodeBlock(
        language="python",
        code="import subprocess; subprocess.run(['ls'])",
    )],
), policy)
assert report.decision == "needs_human_review"
assert report.rule_id == "codeexec.host_command_bridge"
```

### 3. Using ToolSafetyFilter (Recommended)

```python
from trpc_agent_sdk.tools.safety import ToolSafetyFilter, default_policy
from trpc_agent_sdk.filter import register_tool_filter

# Register the safety filter
filter_instance = ToolSafetyFilter(policy=default_policy())

# Or with a custom policy file:
# filter_instance = ToolSafetyFilter(policy=load_policy("my_policy.yaml"))
```

The filter automatically intercepts `workspace_exec`, `exec_command`, and
`execute_code` tool calls before execution. No additional configuration needed.

### 4. Custom Policy (YAML)

```yaml
# my_policy.yaml
denied_commands:
  - rm
  - sudo
  - shutdown
  - curl
  - wget

denied_paths:
  - /etc
  - ~/.ssh
  - .env

network_allowlist:
  - api.github.com
  - pypi.org

review_commands:
  - pip install
  - npm install

max_timeout_seconds: 120
max_output_bytes: 1048576  # 1MB
```

```python
from trpc_agent_sdk.tools.safety import load_policy

policy = load_policy("my_policy.yaml")
```

### 5. Secret Redaction

```python
report = scan(Request(
    tool_name="workspace_exec",
    backend="workspaceexec",
    command="echo OPENAI_API_KEY=sk-1234567890abcdef",
), policy)

assert report.redacted == True
# "sk-1234567890abcdef" → "[REDACTED_SECRET]"
```

## Running Tests

```bash
# All safety tests (34 tests)
python -m pytest tests/tools/safety/ -v

# Types only (16 tests)
python -m pytest tests/tools/safety/test_types.py -v

# Scanner only (18 tests)
python -m pytest tests/tools/safety/test_scanner.py -v
```

## Reproducing Results

```bash
# 1. Clone and set up
git clone https://github.com/trpc-group/trpc-agent-python
cd trpc-agent-python
pip install -e ".[dev]"

# 2. Run full safety test suite
python -m pytest tests/tools/safety/ -v --tb=short

# Expected output:
# ================== 34 passed in 0.42s ==================
```

## Decision Reference

| Decision | Meaning | Filter Behavior |
|----------|---------|-----------------|
| `allow` | Safe, execute normally | Pass through |
| `deny` | Dangerous, block execution | `PermissionError`, filter chain halted |
| `ask` | Suspicious, prompt user | Application-level handling |
| `needs_human_review` | Requires manual approval | Logged, application decides |

## Risk Level Reference

| Level | Description | Example |
|-------|-------------|---------|
| `low` | No risk detected | `echo hello` |
| `medium` | Needs attention | Pipeline command, dependency install |
| `high` | Potentially dangerous | Shell bypass, denied network |
| `critical` | Immediately dangerous | `rm -rf /`, secret leak |
