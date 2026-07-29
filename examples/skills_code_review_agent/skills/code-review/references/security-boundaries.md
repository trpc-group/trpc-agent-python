# Security boundaries

Read this reference before staging or executing any code-review script. The
governance posture is fail-closed: missing proof becomes a Filter decision, not
an optimistic sandbox run.

## Boundary map

| Stage | Data allowed | Trust change | Allowed output |
|---|---|---|---|
| Host acquisition | raw diff, repository text, snapshots | untrusted input enters controlled task memory | `ChangeSet` plus sanitized warnings |
| Task staging | minimum files required for one registered script | controlled host to isolated workspace | staged input and hash-verified Skill files |
| Sandbox execution | staged input, constructed environment, registered entrypoint | isolated code processes untrusted content | bounded `parsed.json` or redacted `findings.json` |
| Host post-processing | sandbox artifacts after first redaction | untrusted output returns to host | field-redacted canonical objects |
| Persistence/reporting | complete object after exit scan | sanitized data crosses persistence boundary | report, database records, metrics, audit summaries |

Raw content may exist only in controlled host memory, the task temporary
directory, and the isolated workspace. Store metadata and redacted evidence;
keep raw diffs, code lines, environment values, and absolute host paths out of
logs, telemetry, LLM input, reports, and databases.

## Filter decision order

Apply these checks before every sandbox run:

1. Accept `script_id + structured_args`; reject unknown scripts and command
   strings.
2. Resolve the entrypoint and argument schema from `scripts/manifest.json`;
   reject unknown, repeated, malformed, overlong, or out-of-enum arguments.
3. Resolve the staged entrypoint real path inside the Skill root and compare
   its SHA-256 with the manifest.
4. Resolve every input/output path inside the task workspace; reject absolute
   host paths, parent traversal, symlink, junction, and reparse-point escape.
5. Require `requires_network=false` and the locked network policy `deny`.
6. Construct the environment from the allowlist and scan values for secret
   patterns.
7. Preflight run count, per-run time/output, cumulative sandbox time/output,
   and the remaining review deadline.
8. Scan the registered script for high-risk execution patterns as defense in
   depth.
9. Verify runtime-specific network proof from the effective instance
   configuration.

`ALLOW` proceeds. `DENY` and `NEEDS_HUMAN_REVIEW` stop before sandbox creation,
produce zero execution side effects, and become sanitized Filter audit events.
A Filter review decision is separate from a finding's human-review bucket.

## Runtime policy

The network policy is deny for every registered script in this release.

| Runtime | Decision rule |
|---|---|
| `container` | Production default. Allow only when the effective instance configuration proves `network_mode=none`; an override or missing proof is deny. |
| `cube` | Default deny because current SDK capabilities do not prove instance-level egress control. A user assertion is not machine-verifiable proof. |
| `local` | Allow only after explicit user or evaluation selection; add a warning that isolation and network denial cannot be enforced like a sandbox. |

Runtime capability metadata describes what a backend may support; it is not
proof of the effective network state of this run. A sandbox failure stays a
sandbox failure and never triggers host-side rule execution.

## Budgets and environment

| Limit | Locked default |
|---|---:|
| sandbox runs per review | 10 |
| time per run | 30 seconds |
| cumulative sandbox time | 90 seconds |
| total review deadline | 110 seconds |
| output per run | 1 MiB |
| output per review | 2 MiB |

Preflight budgets before execution. Runtime timeout and output truncation remain
defense-in-depth controls, not substitutes for admission control.

Construct the environment instead of inheriting it. Allow only non-sensitive
locale and buffering variables such as `LANG`, `LC_ALL`, and
`PYTHONUNBUFFERED`; construct `PATH` and `PYTHONPATH` inside the sandbox; let the
runtime inject workspace directory variables. Keep model keys, credentials,
tokens, passwords, and all unrelated host variables outside the workspace.

## Data handling

Detection needs controlled access to original content, so preserve raw input
until deterministic secret detection completes. Protect every outward path in
this order:

1. **Sandbox output redaction:** `run_checks.py` redacts finding text with the
   same pattern table used for detection before writing `findings.json`.
2. **Host field redaction:** redact evidence, recommendations, Filter reasons,
   stdout/stderr summaries, exceptions, and report fields after reading sandbox
   output.
3. **Complete exit scan:** scan the full JSON, Markdown, database-bound object,
   log capture, telemetry attributes, and audit summaries before persistence.

A plaintext hit at the final gate blocks report/database persistence and marks
the task failed with a sanitized error code. Cleanup runs in `finally`; cleanup
failure adds a sanitized warning without exposing the task path.

## Failure semantics

| Event | Recorded result | Review outcome when a report remains possible |
|---|---|---|
| Filter `DENY` / `NEEDS_HUMAN_REVIEW` | Filter event plus blocked sandbox summary | `completed_with_warnings` |
| timeout | sandbox run with `timed_out=true` and error type | `completed_with_warnings` |
| nonzero exit or runtime error | sandbox run with sanitized stderr/error type | `completed_with_warnings` |
| output limit | sandbox run with truncation marker | `completed_with_warnings` |
| cleanup failure | warning with sanitized error type | `completed_with_warnings` |
| input parse, DB critical write, or report generation failure | fatal task error | `failed` |
| final plaintext scan hit | persistence blocked and sanitized fatal error | `failed` |

## Completion checklist

Before returning control to the review pipeline, verify all of the following:

- the manifest entry and staged file hashes match;
- every staged path resolves inside the task workspace;
- the Filter decision occurred before runtime execution;
- the effective runtime policy has the required machine-verifiable proof;
- run and review budgets remain within their locked limits;
- the sandbox environment contains only constructed allowlisted values;
- sandbox output redaction, host field redaction, and complete exit scan all
  completed;
- every failure is represented as sanitized data;
- task workspace cleanup was attempted in `finally`.
