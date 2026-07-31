# Design

`run_pipeline.py` only parses arguments and assembles an optional live callback.
`pipeline/orchestrator.py` is the sole composition root. Raw SDK
`EvaluateResult` objects are handed directly to `evaluation.normalize_result`;
no other module unfolds them.

The dependency direction is flat and one-way:

```text
CLI -> orchestrator -> preflight / evaluation_runtime / candidate_runtime / reporting
configuration -> live_adapter / models / schema
models -> schema
evaluation_runtime -> backend contract / normalizer / cost ledger / artifact sink
candidate_runtime -> generator contract / split policy / prompt workspace / artifact sink
backends -> offline_evaluation / trace_fixture / live_adapter / contracts / models
live_adapter -> none
optimizer_worker -> live_adapter / schema
preflight -> live_adapter
trace_fixture -> models / schema
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
`trace_fixture.py` owns trace schema, hash and case-matrix validation.
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

Candidate generators return independent cost sources instead of a lossy
aggregate. The SDK defines `OptimizeResult.total_llm_cost` as total optimizer
cost, including evaluator calls, so the live adapter records it once without an
invented unknown judge source. Live evaluation remains unknown unless both
per-agent-call and per-metric-call maxima are configured; then each evaluation
source is explicitly marked as an upper bound. Unknown accounting remains
`null` through the ledger, and an enabled cost gate rejects it with
`COST_UNAVAILABLE`.

The report schema is `v2`. It intentionally replaces `rubricIds` with structured
`rubrics` and candidate aggregate accounting with source-level accounting; no
compatibility shim reconstructs the discarded `v1` information.

## Lifecycle

```text
validated = preflight(config, train, validation, prompts, trace, callback_source)
with prompt_set_lock(validated.prompt_paths):
    baseline = evaluate(train, validation)
    failures = attribute(structured_evidence, explicit_maps, reason_semantics, metric_fallback)
    candidate = optimizer_worker(inner_train(failures), inner_selection)
    regression = compare(evaluate(candidate, train, validation), baseline)
    preliminary = hard_overfit_guard(regression) -> configured_gate(regression, cost, duration)
    persist_pre_apply_audit_or_raise()
    apply_verified(candidate) only when preliminary == ACCEPT and explicitly_authorized
    terminal = configured_gate(regression, cost, current_duration)
    restore_verified_baseline() if terminal rejects an applied candidate
    persist_terminal_audit_once_or_raise()
on cancel: request_stop -> bounded_wait -> terminate -> bounded_wait -> kill
```

1. Preflight strictly parses config and datasets, rejects duplicate JSON keys,
   validates split isolation, hashes every input, and validates the complete
   trace phase/split/case matrix without side effects.
2. A cross-process lock is acquired for the complete prompt path set, then the
   artifact directory is created exclusively; contention or a reused run ID
   fails before prompt mutation.
3. Baseline train and held-out validation are evaluated sequentially.
4. A seeded inner train/selection split is persisted. Only inner-train failure
   attribution is passed to the candidate generator.
5. The candidate is generated with source updates disabled, then evaluated on
   full train and held-out validation inside a verified temporary prompt context.
6. Pure comparison and gate functions produce the only business decision. The
   non-configurable overfit guard treats either train score or train pass-rate
   improvement combined with either validation score or pass-rate regression as
   a rejection.
7. A report is persisted before optional apply. Apply occurs only on ACCEPT when
   explicitly enabled, and every write is read back and hash verified.
8. The terminal gate and report duration are sampled at the final decision
   boundary. If that decision changes to REJECT after an apply, the baseline is
   restored before publication. The terminal report, manifest, JSON snapshot,
   and Markdown snapshot are then published once. Audit I/O is outside the
   business duration gate; the 180-second end-to-end requirement is measured by
   the acceptance runner. The report excludes its own files from its embedded
   artifact list; the manifest hashes the final JSON and Markdown files.

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
Source `inputs.hashes` bind the submitted contracts used for replay. Separate
`inputs.auditHashes` and inner-split `auditHashes` bind the redacted dataset
copies stored on disk, so credential removal cannot invalidate or misrepresent
either fact.
Live optimizer config, prompt sandbox and raw optimizer output stay in an OS
temporary directory; only sanitized known artifact types enter the audit tree.
The import boundary snapshots only regular, single-link files. It rejects
symlinks, hard links and Windows reparse points, verifies file identity before
and after bounded reads, and enforces file-count, per-file-byte and total-byte
budgets before publishing any optimizer artifact. This boundary protects audit
storage; it is not
a sandbox for a programmatic generator, which is trusted in-process code.

Default live evaluation and optimization resolve one importable callback through
`LiveAdapterSpec`; preflight binds its source path, file SHA-256, and callable code
fingerprint to the run, and the worker verifies all three after re-importing it. A
different callback object, source, cached code version, or source version is
rejected before optimization. Live optimization always uses
`optimizer_worker.py`. Cancellation
writes the SDK stop signal, waits for the configured bound, then terminates and
finally kills a worker that still does not exit. Programmatically injected
backends and generators remain trusted in-process components and make the report
non-reproducible. A successful SDK result must report the exact workspace
baseline and registered best-prompt keys before it can enter regression. Failed
or canceled SDK results retain their structured rounds, duration, error and cost
facts in the terminal report. Terminal report
persistence failures raise `AuditPersistenceError`; they are never discarded
while returning an apparently handled pipeline error. Error chains are
credential-redacted with bounded item count and total text size, retaining the
primary and restoration causes. Audit writes redact credentials without
truncation and fail before publication when the configured file-byte ceiling is
exceeded.

A replay claim is emitted only for a clean, pinned Git commit when every
effective config, dataset, prompt, trace and live callback source is inside that
repository and tracked. Absolute external or untracked inputs remain executable
but are explicitly marked non-reproducible.

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

The implementation stays in one flat package with explicit ownership and an
import allowlist. Line count is tracked as a review signal, not used as a reason
to merge unrelated responsibilities or introduce facade layers. The largest
modules remain validated result models, normalization, backend adapters, report
persistence, and the composition root.
