# Automatic Code-Review Agent (Skills + Sandbox + Database)

> 中文文档: [README.md](README.md) · Design note (Chinese): [DESIGN.zh_CN.md](DESIGN.zh_CN.md)

A verifiable automatic code-review (CR) agent prototype built on the
tRPC-Agent SDK: it reads a git diff / PR patch / local changes, loads rules
and scripts through the **code-review Skill**, executes static checks in a
**sandbox** after the **Filter policy** approves, persists structured
findings, block records, sandbox logs and monitoring metrics to a **SQL
database**, and renders `review_report.json` + `review_report.md`.

## Highlights

- **CR Skill** (`skills/code-review/`): SKILL.md + rule docs + sandbox
  scripts covering six categories — security risk, async errors, resource
  leaks, missing tests, secret leakage, DB transaction/connection lifecycle
  (the issue requires four).
- **One implementation, both sides**: the diff parser, rule engine and secret
  pattern table live in `skills/code-review/scripts/lib/` (stdlib-only). The
  sandbox executes it directly; the host loads the very same files via
  importlib — detection and redaction can never drift apart.
- **Sandbox execution**: `--sandbox` defaults to **auto** — **container**
  (Docker, native isolation) when Docker is available, otherwise a clearly
  logged fallback to **local**; **cube** (Cube/E2B) is supported. **local**
  is a development fallback only (tests/dry-run pin it) and is hardened with
  `EnvWhitelistLocalProgramRunner` so host secrets never enter the sandbox.
- **Safety boundary**: per-run timeout, stdout/stderr size cap, env
  whitelist, in-sandbox evidence redaction; timeouts/failures/exceptions are
  recorded as data (`cr_sandbox_run` rows) and the pipeline falls back to the
  in-process rule engine — **a sandbox crash never kills a review**.
- **Filter governance**: `SandboxGovernanceFilter` (real SDK `BaseFilter` +
  `run_filters` chain) pre-blocks risky scripts, non-whitelisted commands,
  forbidden paths, network access and over-budget runs. On
  `deny`/`needs_human_review` the terminal handler is never invoked; reasons
  land in the report and `cr_filter_event`.
- **Dedup & noise control**: one report per `(file, line, category)` (highest
  severity wins, merged rule ids preserved); findings with confidence < 0.7
  go to `needs_human_review` and never mix into high-confidence findings.
- **Storage**: five tables behind the `ReviewStore` ABC; switching to
  MySQL/PostgreSQL is just a different SQLAlchemy URL.
- **Monitoring**: total/sandbox duration, tool calls, filter decisions,
  finding counts, severity distribution, exception types — all DB-queryable,
  plus OpenTelemetry tracer spans per phase.
- **Offline by default**: `--dry-run` (fake model + local sandbox) needs no
  API key and finishes in seconds (acceptance limit: 2 minutes).

## Usage

```bash
cd examples/skills_code_review_agent

python run_agent.py review --fixture security_issue --dry-run   # no API key
python run_agent.py review --diff-file my.patch
python run_agent.py review --repo-path /path/to/repo
python run_agent.py review --files a.py b.py

python run_agent.py show --task-id <ID>     # full DB bundle for one task
python run_agent.py list
python run_agent.py init-db                 # idempotent schema init/migration

# production shape: container sandbox + real model. Export the variables
# listed in .env.example first — .env is NOT loaded automatically:
#   set -a; source .env; set +a
python run_agent.py review --diff-file my.patch --sandbox container --model-mode real
```

## Tests

```bash
python -m pytest examples/skills_code_review_agent/tests -q     # 71 passed, all offline
```

## Database schema

`cr_review_task` (task + status machine), `cr_sandbox_run` (every sandbox
attempt incl. blocked/failed, redacted output excerpts), `cr_filter_event`
(every governance decision with reasons), `cr_finding` (structured findings,
bucketed into `finding` / `needs_human_review`), `cr_report` (final document
+ metrics). Everything is queryable by task id via
`ReviewStore.get_task_bundle()` / `run_agent.py show`.

See README.md (Chinese) for the acceptance-criteria mapping table and the
architecture diagram, and DESIGN.zh_CN.md for the 300–500-character design
note required by the issue.
