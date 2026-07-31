# Runtime And Sandbox Review Rules

## Script Execution Boundaries

Rule: review scripts must run in a governed workspace with explicit command,
working directory, timeout, output limit, and environment allowlist.

Example:

```bash
python scripts/rule_runner.py --input parsed_input.json --manifest skill_manifest.json
```

Trigger conditions:

- A proposed command reads outside the staged workspace.
- Runtime selection requests local execution without explicit approval.
- Environment variables, network hosts, or writable paths are not allowlisted.

## Failure Recording

Rule: timeout, denied execution, missing runtime, parser errors, and command
failures must be recorded as diagnostics or governance events rather than
discarded.

Example:

```json
{"status": "timeout", "timeout_sec": 30, "stderr": "..."}
```

Trigger conditions:

- Changed code catches sandbox failures and returns success.
- Output truncation is not surfaced to reports or metrics.
- Failure evidence may contain credentials and needs redaction before storage.

## Output Discipline

Rule: rule and sandbox output must stay structured, size-limited, and safe for
JSON, SQLite, and Markdown rendering.

Example:

```json
{"schema_version": "code-review.rules.v1", "findings": []}
```

Trigger conditions:

- Scripts print ad hoc prose instead of JSON.
- Large stdout/stderr is stored without truncation metadata.
- Findings omit severity, category, file, evidence, recommendation, confidence,
  or source.
