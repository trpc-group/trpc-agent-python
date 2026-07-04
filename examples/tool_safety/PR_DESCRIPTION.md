# Tool Script Safety Guard - Issue #90

## Acceptance Mapping

- Scans script/command content, command-line args, cwd, env metadata, and tool metadata.
- Returns `allow`, `deny`, or `needs_human_review`.
- Supports Python AST/text checks and Bash token/text checks.
- Loads policy from YAML and supports strict policy validation.
- Emits structured reports with decision, risk type, rule, evidence, and recommendation.
- Writes sanitized audit JSONL and records OpenTelemetry safety attributes.
- Provides a manifest-driven sample corpus with at least 12 samples.
- Maintains high-risk detection at or above 90%.
- Keeps secret-read, dangerous-delete, and non-whitelisted-network samples from allowing execution.
- Keeps 500-line Bash and Python scripts under 1 second in the safety test suite.
- Documents that static scanning is not a sandbox.
- Keeps existing Tool and CodeExecutor behavior unchanged unless explicitly enabled.

## Code Path Mapping

- Scanner: `trpc_agent_sdk/tools/safety/_scanner.py`, `trpc_agent_sdk/tools/safety/_rules.py`
- Policy: `trpc_agent_sdk/tools/safety/_policy.py`
- Input extraction: `trpc_agent_sdk/tools/safety/_extractors.py`
- Filter/Wrapper: `trpc_agent_sdk/tools/safety/_filter.py`, `trpc_agent_sdk/tools/safety/_wrapper.py`
- BashTool integration: `trpc_agent_sdk/tools/file_tools/_bash_tool.py`
- UnsafeLocalCodeExecutor integration: `trpc_agent_sdk/code_executors/local/_unsafe_local_code_executor.py`
- CLI: `scripts/tool_safety_check.py`
- Manifest report: `scripts/tool_safety_manifest_report.py`
- Manifest and samples: `examples/tool_safety/samples/manifest.yaml`, `examples/tool_safety/samples/`
- Reports: `examples/tool_safety/all_reports.json`
- Audit: `trpc_agent_sdk/tools/safety/_audit.py`
- OTel: `trpc_agent_sdk/tools/safety/_telemetry.py`
- Custom rules API: `trpc_agent_sdk/tools/safety/_custom_rules.py`
- Tests: `tests/tools/safety/`

## Sample Corpus

Current manifest size: 52 samples.

Category counts:

- `dangerous_delete`: 5
- `denied_path_write`: 1
- `dependency_install`: 1
- `dynamic_code`: 2
- `dynamic_delete`: 1
- `dynamic_network`: 1
- `network_non_whitelist`: 7
- `network_whitelist`: 2
- `process_control`: 1
- `process_execution`: 1
- `resource_exhaustion`: 5
- `safe_local`: 7
- `secret_exfiltration`: 8
- `secret_output`: 2
- `secret_read`: 6
- `shell_features`: 1
- `shell_injection`: 1

## Validation Commands

```bash
pytest tests/tools/safety
python scripts/tool_safety_manifest_report.py --strict-policy
python scripts/tool_safety_check.py \
  examples/tool_safety/samples/dangerous_delete.sh \
  --language bash \
  --policy examples/tool_safety/tool_safety_policy.yaml
python scripts/tool_safety_check.py \
  examples/tool_safety/samples/safe_python.py \
  --language python \
  --policy examples/tool_safety/tool_safety_policy.yaml
```

## Default Compatibility

- `BashTool` does not enable the safety guard by default.
- `UnsafeLocalCodeExecutor` does not enable the safety guard by default.
- Filter, Wrapper, and Skill-like callable examples are opt-in.
- MCP-like payloads can be protected through the generic Filter/Wrapper examples.
- `needs_human_review` is not blocked by default unless `block_on_review=true`.

## Known Limitations

This is a deterministic static pre-execution guard, not a sandbox.

It does not replace process sandboxing, least-privilege filesystem permissions,
network egress controls, resource limits, or runtime audit and monitoring.

Obfuscation, generated code, dynamic imports, external binary behavior, and
environment-dependent behavior are handled conservatively where possible and may
require human review.
