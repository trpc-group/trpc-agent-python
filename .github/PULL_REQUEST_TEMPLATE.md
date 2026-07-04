## Summary

Implements Issue #90 Tool Script Safety Guard as an opt-in pre-execution guard
for Python and Bash tool scripts.

## Issue #90 Acceptance Checklist

- [ ] Scans script/command text, command-line arguments, cwd, env, and tool metadata.
- [ ] Produces `allow`, `deny`, and `needs_human_review` decisions.
- [ ] Supports Python and Bash scanners.
- [ ] Supports YAML policy configuration, including strict validation.
- [ ] Emits structured reports with decision, risk type, rule, evidence, and recommendation.
- [ ] Writes sanitized audit JSONL events and OpenTelemetry attributes.
- [ ] Includes manifest-driven samples with high-risk detection >= 90%.
- [ ] Covers secret-read, dangerous-delete, and non-whitelist-network samples with no allow decisions.
- [ ] Keeps 500-line script scanning under 1 second in the safety test suite.
- [ ] Documents that static scanning is not a sandbox.
- [ ] Preserves default behavior for existing Tool and CodeExecutor paths.

## Code Path Mapping

- Scanner, rules, policy, reports: `trpc_agent_sdk/tools/safety/`
- CLI: `scripts/tool_safety_check.py`
- Manifest report generation: `scripts/tool_safety_manifest_report.py`
- Samples and policy: `examples/tool_safety/`
- Safety tests: `tests/tools/safety/`

## Validation

```bash
python -m pytest tests/tools/safety -q
python scripts/tool_safety_manifest_report.py --strict-policy
python scripts/tool_safety_check.py \
  examples/tool_safety/samples/safe_bash.sh \
  --language bash \
  --policy examples/tool_safety/policy.yaml
python scripts/tool_safety_check.py \
  examples/tool_safety/samples/bash_pipe_exfiltration.sh \
  --language bash \
  --policy examples/tool_safety/policy.yaml
```

## Sample Matrix

- Sample count: 52
- Decision matches: 52/52
- Required rule matches: 52/52
- Categories include safe, secret-read, dangerous-delete, non-whitelist-network,
  secret-exfiltration, dynamic-code, resource-exhaustion, and process execution.

## Compatibility

- `BashTool` safety guard remains disabled by default.
- `UnsafeLocalCodeExecutor` safety guard remains disabled by default.
- `needs_human_review` is not blocked unless `block_on_review=True`.

## Known Limitations

This is a deterministic static pre-execution guard, not a sandbox. It cannot
guarantee safety against obfuscation, generated code, external binary behavior,
runtime-only data flow, or interpreter/runtime bugs. Production deployments
still need filesystem isolation, network egress control, resource limits, and
runtime audit monitoring.
