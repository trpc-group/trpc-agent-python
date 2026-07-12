# Sandbox Policy

The code-review Agent must treat diff content, generated scripts, and command output as untrusted data.

## Default runtime

The deterministic example uses `fake` sandbox runtime by default. It records sandbox-shaped results without executing arbitrary host commands.

## Production runtime

Production implementations should use Container or Cube/E2B workspace runtimes. Local execution is only a development fallback and must require explicit opt-in.

## Pre-execution governance

Before execution, every sandbox request must pass Filter governance:

- script name must be allowlisted;
- risky command tokens such as `rm`, `sudo`, `curl`, `wget`, `ssh`, install commands, or shell chaining are denied;
- forbidden paths such as `.env`, `.ssh`, private keys, credentials, `/etc`, and traversal paths are denied;
- network access is denied unless explicitly allowlisted;
- timeout and output-size budgets are enforced.

Decisions are `allow`, `deny`, or `needs_human_review`. Denied and human-review requests are recorded in reports and storage but must not execute.

## Output handling

Sandbox stdout and stderr are capped, redacted, and stored as excerpts. Failures and timeouts are non-fatal review audit events.
