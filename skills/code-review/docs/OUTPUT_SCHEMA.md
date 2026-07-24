# Findings JSON contract (single source of truth)

The sandbox scripts (`scripts/run_checks.py`) emit `out/findings.json`. Both the standalone
skill (which must run without importing the example package) and the example pipeline
(`pipeline/types.py::Finding`) are anchored to this schema. Change them together.

```jsonc
{
  "findings": [
    {
      "severity": "critical | high | medium | low",   // required
      "category": "string",                            // required, e.g. "security", "secret_leakage"
      "file": "path/to/file.py",                       // required
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
- The nine fields above `rule_id` are **mandatory** (issue #92, requirement 4). Missing any = invalid.
- Every scanner's native output is normalized into this shape by `pipeline/scanners.py`.
- Secrets in `evidence` MUST be redacted before this JSON is persisted or rendered.
