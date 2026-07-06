# Design Notes

## Problem Shape

Agents increasingly call tools that accept script-like payloads: a Bash command, a Python snippet, generated code blocks, or a file-backed skill script. Those payloads can perform useful local work, but they can also delete files, exfiltrate secrets, install packages, start unbounded subprocesses, or contact unapproved network destinations. The safety guard is a pre-execution control that makes those risks visible before the tool body runs.

## Architecture

The quickstart has three layers:

1. `policy.yaml` defines environment-specific rules: allowed network domains, allowed commands, denied paths, protected system roots, resource thresholds, and the risk levels that map to `allow`, `needs_human_review`, or `deny`.
2. `ToolSafetyGuard` owns scanning, decision calculation, telemetry attributes, and audit emission. It receives a `ToolSafetyScanRequest` and returns a `ToolSafetyReport`.
3. Integration adapters place the guard in front of execution. `ToolSafetyFilter` protects ordinary tools by scanning common argument names such as `command`, `script`, `code`, and `code_blocks`. `SafetyGuardedCodeExecutor` protects CodeExecutor delegates by scanning every code block before delegation.

The quickstart runner exercises all three layers with the same sample scripts. Direct scan shows the raw report. The filter path proves that a blocked script prevents the tool handler from running. The CodeExecutor path proves that generated code is stopped before the executor delegate sees it.

## Decision Model

Scanning produces findings with stable `rule_id`, `risk_type`, `risk_level`, evidence, source line, redaction status, and remediation guidance. The aggregate decision is policy driven: findings at or above `deny_risk_level` become `deny`; findings at or above `review_risk_level` become `needs_human_review`; otherwise the report is `allow`. With `block_on_review: true`, review-needed scripts are blocked by adapters even though the decision is distinct from a hard deny.

## Rule Strategy

Python uses AST analysis first because it can distinguish imports, calls, path literals, loop bounds, subprocess options, and write modes more reliably than plain text matching. Bash uses tokenization and targeted shell-pattern checks for command prefixes, redirections, control operators, recursive deletes, network commands, dependency installation, and fork-bomb patterns. Regex is reserved for cross-language text risks such as literal secrets and URLs.

## Audit And Telemetry

Each report includes OpenTelemetry-ready attributes such as `tool.safety.decision`, `tool.safety.risk_level`, `tool.safety.rule_id`, `tool.safety.rule_ids`, and `tool.safety.blocked`. When an audit path is configured, the guard appends a compact JSONL event with the same decision, policy version, finding count, redaction flag, and primary rule id. This gives operators a low-cardinality signal for dashboards and a durable trail for incident review.

## Safety Boundaries

The guard is intentionally static and pre-execution. It reduces obvious risk and gives consistent policy enforcement, but it is not a sandbox. Obfuscated strings, downloaded second-stage scripts, interpreter-specific behavior, shell expansion, and generated runtime code can bypass static analysis. Production deployments should still use sandbox isolation, least-privilege credentials, read-only mounts where possible, network egress controls, resource limits, timeouts, and post-execution monitoring.
