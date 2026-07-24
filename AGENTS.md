# Project Agent Guidelines

These rules apply to the whole repository unless a deeper `AGENTS.md` overrides them.

## Engineering Defaults

- Prefer the smallest change that satisfies the requested behavior.
- Match existing file layout, naming, and test style before adding new patterns.
- Do not refactor adjacent code or rewrite unrelated docs while implementing an issue.
- Treat prompt, evalset, optimizer config, and report files as auditable artifacts.

## Evaluation And Optimization Work

- Default to offline, deterministic flows for examples and tests.
- Online model calls must be opt-in and gated by explicit environment variables.
- Keep train and validation evalsets physically separate.
- Do not expose validation gold data to optimizer logic beyond final gate scoring.
- Write generated reports only under `runs/` or a caller-provided output directory.

## Reports

- Produce machine-readable JSON first; human-readable Markdown may summarize it.
- Avoid new Markdown files unless the issue asks for them or they are standard example docs.
- Reports must state baseline score, candidate score, case deltas, gate decision, and rejection or acceptance reasons.

## Testing

- Add focused tests for new behavior before broad regression runs.
- Fake or trace modes must run without API keys.
- Online-mode tests should validate configuration and wiring without consuming real API calls.
