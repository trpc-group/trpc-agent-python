# Tool Script Safety Guard example

This directory is entirely scan-first: the CLI reads sample source but never executes it.

Validate all 12 public samples:

```bash
.venv/bin/python scripts/tool_safety_check.py \
  --manifest examples/tool_safety/sample_manifest.yaml \
  --policy examples/tool_safety/tool_safety_policy.yaml --json
```

Scan one file and write a redacted JSONL Audit outside the Git worktree:

```bash
.venv/bin/python scripts/tool_safety_check.py \
  examples/tool_safety/samples/safe_python.py \
  --policy examples/tool_safety/tool_safety_policy.yaml \
  --audit /tmp/trpc-tool-safety-audit.jsonl --json
```

Files:

- `tool_safety_policy.yaml`: strict example Policy; runtime limits are declarations only.
- `sample_manifest.yaml`: expected decision/risk/rules/blocked values for 12 public examples.
- `evaluation_corpus.py` and `evaluation_manifest.yaml`: independent deterministic acceptance corpus.
- `integration_example.py`: ordinary async callable/Skill-boundary adapter with Policy, Audit, and Monitor. It acknowledges source and does not execute it.
- `samples/`: dangerous inputs for static scanning only.

`SafetyCallable`, `SafetyProgramRunner`, and `SafetyMCPAdapter` protect distinct visible boundaries. Skill internals, framework staging, MCP discovery/stdio startup, and MCP Server internals are not automatically covered. See the [English](../../docs/mkdocs/en/tool_safety.md) or [中文](../../docs/mkdocs/zh/tool_safety.md) guide for all integration and security boundaries.
