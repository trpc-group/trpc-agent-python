# Findings JSON contract (single source of truth)

`scripts/run_checks.py` emits `out/findings.json`. It is the only implementation of the review
rules: the agent reaches it through the framework's Skills mechanism (`skill_load` then
`skill_run`), and the deterministic CLI reaches the same script through a development subprocess.
Nothing re-implements these checks elsewhere. The example's `pipeline/types.py::Finding` is
anchored to this schema — change them together.

```jsonc
{
  "schema_version": 1,                                // required, bumped on breaking changes

  // Where the scan actually rooted itself. The runner stages inputs at a layout that differs
  // between workspace runtimes, so the script locates its own root by finding the diff sidecar
  // (".changes.diff") and reports it here. Every `file` below is relative to this root, which
  // makes them line up with the paths in the diff regardless of staging layout.
  "root": "work/inputs/cr_scan_ab12cd",

  // Which scanners actually executed *inside the sandbox* on this run. Measured where the work
  // happens, so it cannot drift from the host's PATH.
  "tools": {
    "bandit": true,
    "ruff": true,
    "detect-secrets": false,
    "semgrep": true,
    "cr-db-lifecycle": true
  },
  "tool_calls": 4,                                    // required, count of `true` entries above

  "diff_files": ["security.py"],                      // files the diff touched; [] when no diff

  "findings": [
    {
      "severity": "critical | high | medium | low",   // required
      "category": "string",                            // required, e.g. "security", "secret_leakage"
      "file": "path/to/file.py",                       // required (see rule 2)
      "line": 42,                                       // required (nullable if file-level)
      "title": "string",                               // required, one-line
      "evidence": "string",                            // required, the offending snippet / reason
      "recommendation": "string",                      // required, how to fix
      "confidence": 0.0,                                // required, 0.0 - 1.0
      "source": "rule | llm | static",                 // required (which producer)

      "rule_id": "bandit:B602",                         // optional, tool + rule id
      "status": "active | duplicate | warning | needs_human_review"  // set by dedup/denoise stage
    }
  ]
}
```

Rules:

1. The nine fields above `rule_id` are **mandatory** (issue #92, requirement 4). Missing any = invalid.
2. `file` is a required **string**, relative to `root`. The empty string `""` is legal and denotes a
   **run-scoped** finding that belongs to no particular file — `scanner_unavailable` and
   `scanner_error`. An invented placeholder path would be worse data than `""`, and the dedup stage
   already keys line-less findings on `rule_id`/`title`, so `""` cannot over-collapse.
3. Secrets in `evidence` MUST be redacted before this JSON is persisted or rendered. The script
   redacts at emit time; `pipeline/report.py::build_report` redacts again at render time. Both are
   required — the script's copy protects the model's context, the report's copy protects the
   artifact.
4. `status` is normally assigned downstream by dedup/denoise. The script sets it only for
   `scanner_error`, where `needs_human_review` is inherent to the finding.
