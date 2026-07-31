# Code-review rules

Findings come from established scanners, normalized into the schema in `OUTPUT_SCHEMA.md`. Each of the
six required categories is backed by a concrete tool or rule:

| Category | Backed by | What it flags |
|---|---|---|
| `security` | **bandit** (all rules except B101) + **ruff** flake8-bandit (`S`) | `eval`/`exec`, `subprocess(..., shell=True)`, `os.system`, `yaml.load`, pickle, weak crypto, hardcoded secrets, etc. |
| `secret_leakage` | **detect-secrets** | AWS keys, tokens, high-entropy strings, private keys in the changed files. |
| `async_errors` | **ruff** `ASYNC` ruleset | blocking calls in `async` functions (e.g. `time.sleep`, blocking `open`). |
| `resource_leak` | **ruff** `SIM115` + flake8-bugbear (`B`) | files/resources opened without a context manager. |
| `db_lifecycle` | `db_lifecycle.yaml` (semgrep) **and** the built-in heuristic in `scripts/run_checks.py` | a DB connection/cursor opened without `with` and never `close()`d. |
| `missing_tests` | diff-level heuristic in `scripts/run_checks.py` (reads the staged `.changes.diff`) | a source file changed with no corresponding test change. |

Notes:
- `assert`-used (bandit `B101` / ruff `S101`) is suppressed — it is noise, especially in tests.
- A required scanner that is not installed produces a `scanner_unavailable` finding routed to
  `needs_human_review`, so a missing tool can never be mistaken for "clean".
- `scripts/run_checks.py` is the only implementation of these rules. The agent reaches it through
  the framework's Skills mechanism (`skill_load` then `skill_run`); the deterministic CLI reaches the
  same script through a development subprocess. Nothing re-implements them elsewhere, so there is no
  second path to keep in parity with.
- When a unified diff is available the findings are restricted to the lines it touched. The script
  locates its own scan root from the `.changes.diff` sidecar staged beside the changed files, because
  the workspace runtimes stage inputs at different depths and a fixed `--target` cannot be relied on.
