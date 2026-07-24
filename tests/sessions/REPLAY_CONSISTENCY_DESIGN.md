# Replay Consistency Design

The replay harness models each case as a normalized stream of operations:
session creation, ordinary or tool events, state deltas, memory store/search,
and summary creation/update. Lightweight CI has an InMemory-only path and an
InMemory-vs-SQLite path, so contributors do not need Redis or MySQL locally.
External SQL and Redis backends are explicit opt-in paths through
`TRPC_AGENT_REPLAY_SQL_URL` and `TRPC_AGENT_REPLAY_REDIS_URL`.

Normalization is intentionally narrow. Timestamps become a placeholder,
generated summary event ids become a stable marker, JSON objects are key sorted,
memory hits are sorted after timestamp normalization, and summary text only gets
whitespace folding. Business data remains strict: event order, authors, content,
state, memory content, summary ownership, summary version/update, and overwrite
semantics are all compared.

The diff report uses InMemory as the baseline and recursively compares each
other backend. Every difference records the case id, expected backend, actual
backend, field path, and both values. Summary comparison is split into semantic
text and storage metadata: `session_id`, `summary_id`, version, updated-at
placeholder, and compressed/original event counts remain independent fields.
`allowed_diff` only accepts explicit rules; the lightweight mode currently has
no default allowed differences, so real mismatches cannot disappear silently.
