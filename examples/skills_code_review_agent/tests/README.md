# Test layout

- `unit/`: deterministic module behavior through one public interface; no network, Docker, or real model key.
- `integration/`: collaboration across modules or local adapters such as SQLite, Filter, sandbox, and Skill execution.
- `e2e/`: complete CLI or evaluate flows ending in reports, database bundles, metrics, and exit codes.
- `fixtures/`: test inputs and expected data only; executable tests do not live here. `diffs/` contains eight `_simple` cases and eight paired `_complex` cases with multi-file engineering context.
- `support/`: shared fakes, builders, and assertions used by more than one test layer.

All commands must use the repository `.venv` interpreter. Mandatory public fixtures must fail when missing rather than skip.
