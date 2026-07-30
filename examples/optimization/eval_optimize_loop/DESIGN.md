# Design

`run_pipeline.py` only parses arguments and assembles an optional live callback.
`pipeline/orchestrator.py` is the sole composition root. Raw SDK
`EvaluateResult` objects are handed directly to `evaluation.normalize_result`;
no other module unfolds them.

The dependency direction is flat and one-way:

```text
CLI -> orchestrator -> preflight / evaluation_runtime / candidate_runtime / reporting
configuration -> models / schema
models -> schema
evaluation_runtime -> backend contract / normalizer / cost ledger / artifact sink
candidate_runtime -> generator contract / split policy / prompt workspace / artifact sink
backends -> offline_evaluation / contracts / models
offline_evaluation -> SDK evaluation types
pure policies -> models
reporting -> models / artifacts
```

`schema.py` owns strict parsing and model primitives; `configuration.py` owns
validated input and policy configuration; `models.py` owns stage outputs and
report facts. `preflight.py` owns validated inputs and run identity.
`evaluation_runtime.py` owns ordered backend calls, completed-call accounting,
and snapshot persistence. `candidate_runtime.py` owns inner-split persistence,
the OS-temporary optimizer workspace, and sanitized optimizer output import.
`offline_evaluation.py` owns offline rule parsing, evidence extraction,
deterministic evaluators, and the run-local replacement registry. `backends.py`
only adapts fake, trace, and live inputs to SDK calls. These are concrete flat
modules, not additional protocol or service layers.

Only `EvaluationBackend` and `CandidateGenerator` are protocols because each has
real fake, trace, and live substitutions. Attribution, comparison, normalization,
and gate logic are ordinary pure functions.

The example does not extend or patch the SDK. Standard fake, trace, and live
evaluation calls `AgentEvaluator.evaluate_eval_set` with validated in-memory
`EvalSet` and `EvalConfig` objects. Only deterministic offline LLM metrics need a
run-local `EvaluatorRegistry`, which `AgentEvaluator` cannot receive per call; for
that narrow path `backends.py` composes the exported `LocalEvalService`,
`RemoteEvalService`, and `InMemoryEvalSetsManager` APIs and rebuilds the exported
`EvaluateResult` aggregate at the example boundary. The registry never mutates
global state. The SDK remains the owner of remote metric
compatibility: black-box evaluation rejects trajectory and knowledge-recall
metrics because callback results do not expose their required intermediate data.

Offline substitutions preserve each metric's contract. `llm_final_response` uses
deterministic exact-reference scoring. Rubric metrics require an explicit
machine-readable offline rule in each rubric's `type`; natural-language rubric
text is never guessed or treated as a pass. Response rules can compare with the
reference, compare or search literal rubric content, or require a non-empty
response. Knowledge rules inspect configured knowledge-tool responses and are
trace-only because black-box callbacks do not expose intermediate data. Each
rubric is scored independently. Normalization preserves its ID, score, pass
status, and reason; attribution consumes only failed rubric outcomes. Unsupported
rules, missing operands, or unavailable evidence fail the evaluation instead of
fabricating a score.

Candidate generators return independent cost sources instead of a lossy aggregate.
The live optimizer source reports only reflection calls and optimizer cost; a
separate unknown judge source records that judge calls and judge cost are not
available from the SDK result. Unknown accounting remains `null` through the cost
ledger, so the total is also unknown. Only the built-in deterministic generator
declares zero cost and zero model calls. Custom generators default to an explicit
`unreported` source; an enabled cost gate rejects it with `COST_UNAVAILABLE`.

The report schema is `v2`. It intentionally replaces `rubricIds` with structured
`rubrics` and candidate aggregate accounting with source-level accounting; no
compatibility shim reconstructs the discarded `v1` information.

## Lifecycle

1. Preflight strictly parses config and datasets, rejects duplicate JSON keys,
   validates split isolation, hashes every input, and validates trace pins.
2. The artifact directory is created exclusively; an existing run ID fails.
3. Baseline train and held-out validation are evaluated sequentially.
4. A seeded inner train/selection split is persisted. Only inner-train failure
   attribution is passed to the candidate generator.
5. The candidate is generated with source updates disabled, then evaluated on
   full train and held-out validation inside a verified temporary prompt context.
6. Pure comparison and gate functions produce the only business decision.
7. A report is persisted before optional apply. Apply occurs only on ACCEPT when
   explicitly enabled, and every write is read back and hash verified.
8. A complete terminal write cycle is measured before the final duration is
   recorded. The report excludes its own files from its embedded artifact list;
   the manifest hashes the final JSON and Markdown files.

The immutable `artifacts/<run_id>/` directory and its manifest are the authority.
Root-level report files are atomic latest-run snapshots for interactive use. They
are not published as a transaction, so a consumer that needs a coherent JSON and
Markdown pair reads the run ID from the root JSON and then verifies both files in
the corresponding immutable run directory. Unique temporary names prevent
concurrent latest-run publishers from corrupting or deleting each other's writes.

Any evaluator, optimizer, normalization, comparison, gate, render, apply, write,
cancel, or system-exit failure restores and verifies the baseline prompt. A
restoration failure raises `PromptRestoreError`; it is never downgraded to
REJECT. Reports store no chain-of-thought and recursively redact credentials.
Live optimizer config, prompt sandbox and raw optimizer output stay in an OS
temporary directory; only sanitized known artifact types enter the audit tree.
The import boundary enforces file-count, per-file-byte and total-byte budgets
before reading optimizer output. This boundary protects audit storage; it is not
a sandbox for a programmatic generator, which is trusted in-process code.

Live cancellation writes the optimizer stop signal and waits for cooperative
shutdown so an optimization thread is not silently abandoned. Python cannot
safely force-kill an in-process thread. Deployments requiring a hard timeout must
place live optimization in a supervised process or container and enforce the
deadline there.

## Industrial Acceptance

The executable acceptance matrix covers every orchestration stage, cancellation,
partial backend failures, failed candidate accounting, malicious artifact sizes,
reference-free attribution, fake/trace replay, and live adapter shutdown. Local
policy matrices assert every declared attribution category and gate decision;
they are contract tests, not an independent accuracy benchmark. Issue-level
accuracy thresholds must be measured by a separately owned hidden corpus. Fake
and trace runs must finish within 180 seconds and produce hash-valid immutable
manifests. The dependency test is an allowlist for all flat pipeline modules and
rejects any new pipeline module, reverse import, or cycle until the architecture
contract is updated deliberately.

The pipeline is roughly 3,700 non-empty lines and its tests roughly 2,150. That
size reflects immutable manifests, verified prompt rollback and apply, strict
SDK-result normalization, partial ERROR reports, reproducibility metadata, and
three execution modes. The implementation stays in one flat package with explicit
ownership and an import allowlist. Line count is tracked as a review signal, not
used as a reason to merge unrelated responsibilities or introduce facade layers.
The largest modules are validated result models, normalization, backend adapters,
report persistence, and the composition root.
