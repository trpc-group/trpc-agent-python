---
name: code-review
description: Review unified diffs with deterministic rules, sandboxed scripts, explainable noise suppression, redaction, and structured audit output.
allowed-tools:
  - skill_run
---

# Code Review Skill

Use this Skill for a unified diff, PR patch, or a diff produced from a local Git worktree. The host is responsible for staging a **redacted** input diff at `$WORK_DIR/inputs/input.diff`; scripts must never receive an unredacted secret-bearing copy.

## Tools

- `skill_run`

## Workspace environment contract

The workspace runtime supplies these absolute paths:

| Variable | Contract |
| --- | --- |
| `$WORKSPACE_DIR` | Root of the isolated review workspace |
| `$SKILLS_DIR` | Read-only staged Skills root; this Skill is `$SKILLS_DIR/code-review` |
| `$WORK_DIR` | Mutable working data; inputs belong under `$WORK_DIR/inputs` |
| `$OUTPUT_DIR` | The only directory for declared, collectable outputs |
| `$RUN_DIR` | Per-run scratch/log directory; do not treat it as durable output |

Never read or write outside these roots. Do not resolve `..`, host-absolute paths, `.env`, SSH keys, or unrelated repository data. The host Filter is authoritative even if a requested command appears in this document.

## Deterministic workflow

1. Confirm `$WORK_DIR/inputs/input.diff` exists and is redacted.
2. Parse changed files, hunks, and line counts:

   ```bash
   python3 "$SKILLS_DIR/code-review/scripts/parse_diff.py" \
     "$WORK_DIR/inputs/input.diff" \
     "$OUTPUT_DIR/diff_summary.json"
   ```

3. Produce deterministic findings:

   ```bash
   python3 "$SKILLS_DIR/code-review/scripts/static_rules.py" \
     "$WORK_DIR/inputs/input.diff" \
     "$OUTPUT_DIR/static_findings.json"
   ```

4. Return only the two declared JSON outputs. The host merges them with in-process findings, applies AST/hunk context suppressions, deduplicates, buckets by confidence, redacts again, and persists the audit bundle.

   Every model-driven `skill_run` must use the declarative `outputs` object
   with explicit positive `max_files`, `max_file_bytes`, and
   `max_total_bytes`, `inline: true`, and `save: false`. Legacy
   `output_files`, implicit `out/**` export, and direct artifact saving are
   disabled by the review runtime.

Do not install packages or access the network. If `python3` is unavailable, the host may choose an already-allowlisted Python executable; the Skill must not download one.

## Finding contract

Every finding must contain:

- `severity`: `critical`, `high`, `medium`, `low`, or `info`;
- `category`: `security`, `async_error`, `async_resource`, `resource_leak`, `testing`, `sensitive_info`, `db_lifecycle`, or `sandbox`;
- `file` and candidate new-file `line`;
- `title`, redacted `evidence`, executable `recommendation`, `confidence`, and `source`;
- `disposition`: confident finding, warning, or `needs_human_review`.

The host deduplicates on `(file, line, category)`. Lower-confidence signals and test-coverage gaps must stay in warnings/manual review. Never raise confidence merely to cross a reporting threshold.

## Safety and governance

- Every command must receive a Filter `allow` decision before execution.
- `deny` and `needs_human_review` decisions are audit results, not invitations to retry with alternate syntax.
- Network, package installation, destructive filesystem operations, privilege escalation, SSH, Docker-in-Docker, and curl-pipe-shell are prohibited by default.
- Respect the host timeout, output-byte limit, environment allowlist, and requested output manifest.
- Never place a credential in evidence, stdout, stderr, artifacts, report text, telemetry attributes, or database fields. Use `<REDACTED>`.
- A sandbox failure must become a structured manual-review item; it must not abort report generation.

## Rule catalogue

- [Security](rules/security.md)
- [Async errors and clients](rules/async_error.md)
- [Resource leaks](rules/resource_leak.md)
- [Database lifecycle](rules/db_lifecycle.md)
- [Sensitive information](rules/sensitive_info.md)
- [Testing gaps](rules/testing.md)
