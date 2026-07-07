# Tool Safety Guard Quickstart

This quickstart is a small project-shaped example for the tool script safety guard. It mirrors the structure used by other quickstart examples: `run_agent.py` is the entry point, `agent/` contains the reusable application modules, `policy.yaml` contains policy knobs, and `scripts/` contains representative tool payloads.

## Directory

```text
examples/tool_safety_guard/quickstart/
|-- run_agent.py
|-- policy.yaml
|-- design.md
|-- agent/
|   |-- __init__.py
|   |-- agent.py
|   |-- config.py
|   `-- tools.py
`-- scripts/
    |-- safe_report.py
    |-- external_upload.py
    |-- read_secret.py
    |-- review_subprocess.py
    `-- dangerous_cleanup.sh
```

`scripts/` is not meant to be executed directly. The runner feeds each file into the guard and uses dry-run delegates to show where a real tool or CodeExecutor would be blocked.

## Run

From the repository root:

```bash
python examples/tool_safety_guard/quickstart/run_agent.py
```

Or with the Windows launcher used in this workspace:

```bash
py -3.14 examples/tool_safety_guard/quickstart/run_agent.py
```

The run writes:

```text
examples/tool_safety_guard/quickstart/out/
|-- quickstart_report.json
`-- audit.jsonl
```

Expected decisions:

| case | expected decision | why |
| --- | --- | --- |
| `safe_report` | `allow` | Local bounded file write under the project output directory. |
| `external_upload` | `deny` | Network egress to a domain not in `allowed_domains`. |
| `read_secret` | `deny` | Reads `.env`, which is configured as a denied path. |
| `review_subprocess` | `needs_human_review` | Starts a subprocess, which is review-worthy but not always unsafe. |
| `dangerous_cleanup` | `deny` | Recursive delete pattern. |

## What This Demonstrates

The same `ToolSafetyGuard` is used in three places from `agent/agent.py`:

- Direct scan: `guard.scan(ToolSafetyScanRequest(...))` returns a structured report.
- Tool Filter: `ToolSafetyFilter` blocks script-like tool arguments before the tool body runs.
- CodeExecutor wrapper: `SafetyGuardedCodeExecutor` scans code blocks before delegating to an executor.

This is the intended production shape: keep execution code simple, put static pre-execution policy in one reusable guard, and write audit events for every decision.

## Policy Knobs

`policy.yaml` controls the main behavior without code changes:

- `allowed_domains`: network egress allowlist.
- `allowed_commands`: command allowlist for shell-like command scans.
- `denied_paths`: sensitive path patterns.
- `system_write_paths`: protected roots.
- resource limits such as `max_sleep_seconds`, `max_loop_iterations`, and `max_parallel_tasks`.
- `deny_risk_level`, `review_risk_level`, and `block_on_review`.

Try adding `evil.example.net` to `allowed_domains`; `external_upload.py` will no longer be denied for non-allowlisted egress, although it still records network-related findings.

## Relationship To The Sample Matrix

The parent `examples/tool_safety_guard/samples/` directory remains the acceptance matrix with 12 focused cases. This quickstart is the narrative example: it shows how the scanner, filter, executor wrapper, report, and audit event fit together in a minimal application.
