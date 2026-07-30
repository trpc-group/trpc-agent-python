# Evaluation + Optimization Loop

This example runs a baseline on train and held-out validation data, attributes
failures, generates a candidate from an inner training split, reruns full
regression, and makes a deterministic gate decision. Every completed run writes
an immutable, authoritative audit directory under `artifacts/<run_id>/`. The
generated `optimization_report.json` and `optimization_report.md` files in this
directory are ignored atomic latest-run convenience snapshots, not a
transactional report pair. Consumers that need a coherent pair must read the JSON
run ID and then use the two immutable files and manifest under
`artifacts/<run_id>/`.

The default fake mode is deterministic, offline, and does not read API keys:

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py --run-id local-fake
```

Trace mode replays the hash-pinned fixture through the SDK evaluator:

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py --mode trace --run-id local-trace
```

In fake and trace modes, LLM rubric metrics use explicit deterministic rules in
each rubric's `type`. Response rubrics support `OFFLINE_RESPONSE_EXACT_REFERENCE`,
`OFFLINE_RESPONSE_EQUALS`, `OFFLINE_RESPONSE_CONTAINS`, and
`OFFLINE_RESPONSE_NON_EMPTY`. Trace knowledge rubrics support
`OFFLINE_KNOWLEDGE_CONTAINS` and `OFFLINE_KNOWLEDGE_NON_EMPTY`. Literal operands
come from `content.text`. Natural-language-only rubrics and knowledge recall in
fake black-box mode fail fast because they cannot be scored deterministically.
Reports retain each rubric's ID, score, pass status, and reason. Failure
attribution uses only rubric outcomes that failed their metric threshold.

Live mode requires an importable async `query -> str` callback. The optimizer is
always called with `update_source=False`; only this pipeline's gate may apply a
candidate:

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py \
  --mode live --call-agent my_package.agent:call_agent --run-id live-review
```

Source prompts remain unchanged by default. Add `--apply-candidate` only when a
gate ACCEPT should be written back. REJECT is a completed audit run and exits 0;
ERROR exits non-zero. Run IDs are immutable and cannot be reused. A reproducible
report command preserves all effective inputs but appends `-replay` to the run ID
so it can execute without overwriting the authoritative original run.

Cost is reported by source. Any unreported source makes the total cost unknown;
when a cost budget is enabled, the gate fails closed with `COST_UNAVAILABLE`.

Optimizer artifacts are accepted only through a sanitized allowlist and bounded
by configurable file-count, per-file-byte, and total-byte limits. Live optimizer
cancellation is cooperative. A production deployment that requires a hard
deadline must run the live optimizer in a supervised worker process or container
and terminate that worker at the platform boundary.

See [DESIGN.md](DESIGN.md) for stage contracts and failure semantics.
