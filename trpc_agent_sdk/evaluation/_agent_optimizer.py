# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""AgentOptimizer: business-facing entry point for prompt optimization.

Mirrors :class:`AgentEvaluator`: business code calls
``AgentOptimizer.optimize(...)`` and the facade dispatches to the
algorithm registered under ``config.optimize.algorithm.name`` (looked
up in :data:`OPTIMIZER_REGISTRY`). Switching algorithms is a
single-field config change.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import signal
import sys
import threading
import warnings
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from typing import Optional
from typing import Sequence

from ._eval_callbacks import Callbacks
from ._optimize_config import OptimizeConfigFile
from ._optimize_config import load_optimize_config
from ._optimize_registry import OPTIMIZER_REGISTRY
from ._optimize_reporter import RunHeader
from ._optimize_reporter import create_reporter
from ._optimize_result import OptimizeResult
from ._remote_eval_service import CallAgent
from ._target_prompt import TargetPrompt

# Metrics incompatible with call_agent (black-box) mode because their
# evaluators need data RemoteEvalService doesn't capture:
#   - ``tool_trajectory_avg_score``: per-step tool call traces.
#   - ``llm_rubric_knowledge_recall``: tool responses from
#     ``Invocation.intermediate_data`` (RemoteEvalService leaves it None,
#     so the judge would always see "No knowledge search results were
#     found." for every case).
_DISALLOWED_METRICS_IN_CALL_AGENT_MODE = frozenset({
    "tool_trajectory_avg_score",
    "llm_rubric_knowledge_recall",
})

_PROMPT_FILE_LOGGER = logging.getLogger("trpc_agent_sdk.optimizer")

_REDACTED_CONFIG_VALUE = "<redacted>"
_SENSITIVE_CONFIG_KEY_SUFFIXES = (
    "apikey",
    "authorization",
    "authtoken",
    "bearertoken",
    "clientsecret",
    "credential",
    "credentials",
    "idtoken",
    "password",
    "passwd",
    "privatekey",
    "refreshtoken",
    "secret",
    "secretaccesskey",
    "secretkey",
    "token",
    "accesstoken",
)


def _reject_non_finite_json_constant(value: str) -> Any:
    """Reject Python's non-standard JSON constants (NaN and infinities)."""
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def _redact_config_secrets(value: Any) -> Any:
    """Return a JSON-compatible copy with credential values redacted.

    Key matching is case-insensitive, ignores separators and accepts
    namespaced credential suffixes, so ``api_key``, ``API-Key`` and
    ``openai_api_key`` share the same policy without redacting safe fields
    such as ``max_tokens``.
    """
    if isinstance(value, dict):
        redacted = {}
        for key, child in value.items():
            normalized_key = "".join(character for character in key.casefold() if character.isalnum())
            if normalized_key.endswith(_SENSITIVE_CONFIG_KEY_SUFFIXES):
                redacted[key] = _REDACTED_CONFIG_VALUE
            else:
                redacted[key] = _redact_config_secrets(child)
        return redacted
    if isinstance(value, list):
        return [_redact_config_secrets(child) for child in value]
    return value


def _atomic_write_text(path: str, content: str) -> None:
    """Atomically replace ``path`` with ``content`` (UTF-8).

    Writes to a sibling ``<path>.tmp`` then ``os.replace`` to swap into
    place — POSIX guarantees rename is atomic, so a process kill or
    power loss between the write and the rename leaves ``path`` either
    pristine (pre-call content, or missing if it did not exist) or
    fully updated, never half-written. Mirrors
    :meth:`TargetPrompt._atomic_write_path` so artifact persistence
    enjoys the same crash safety as source rollback.
    """
    tmp = path + ".tmp"
    Path(tmp).write_text(content, encoding="utf-8")
    os.replace(tmp, path)


class _mask_sigint:
    """Context manager that masks SIGINT for the duration of the block.

    Used by :meth:`AgentOptimizer._persist_artifacts` so a panicked second
    Ctrl+C during teardown cannot interrupt artifact writes between
    ``os.replace`` boundaries. Restores the previous handler on exit even
    if the block raises. On platforms / threads where ``signal.signal``
    is unavailable (Windows, non-main thread) the context degrades to a
    no-op rather than crashing — the underlying ``_atomic_write_text`` is
    still crash-safe; only the second-Ctrl+C-during-finally race
    protection is foregone.
    """

    def __init__(self) -> None:
        self._previous = None
        self._installed = False

    def __enter__(self) -> "_mask_sigint":
        # signal.signal() only works in the main thread of the main interpreter.
        if threading.current_thread() is not threading.main_thread():
            return self
        try:
            self._previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
            self._installed = True
        except (ValueError, OSError):  # pragma: no cover - platform fallback
            # ValueError: not main thread on some platforms; OSError: signal
            # not supported (rare embedded interpreters). Either way, leave
            # SIGINT as-is; persistence is still best-effort.
            self._installed = False
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self._installed:
            return
        try:
            signal.signal(signal.SIGINT, self._previous)
        except (ValueError, OSError):  # pragma: no cover - platform fallback
            pass


class AgentOptimizer:
    """Business-facing entry point dispatching to the registered algorithm.

    Business code passes a config file path; the facade reads + validates
    it, looks up the algorithm class from
    :data:`OPTIMIZER_REGISTRY` by ``config.optimize.algorithm.name``,
    instantiates it, and runs the loop.

    Example:
        target = TargetPrompt().add_path("system_prompt", "prompts/system.md")
        result = await AgentOptimizer.optimize(
            config_path="optimizer.json",
            call_agent=my_call_agent,
            target_prompt=target,
            train_dataset_path="data/train.evalset.json",
            validation_dataset_path="data/val.evalset.json",
            output_dir="runs/2026-05-17T16-30-00",
        )
    """

    @classmethod
    async def optimize(
        cls,
        *,
        config_path: str,
        call_agent: CallAgent,
        target_prompt: TargetPrompt,
        train_dataset_path: str,
        validation_dataset_path: str,
        output_dir: str,
        callbacks: Optional[Callbacks] = None,
        update_source: bool = False,
        verbose: int = 1,
        extra_stop_callbacks: Optional[Sequence[Any]] = None,
        extra_gepa_callbacks: Optional[Sequence[Any]] = None,
    ) -> OptimizeResult:
        """Load the config file at ``config_path`` and run the selected algorithm.

        Args:
            config_path: Path to the optimizer JSON config file.
            call_agent: Async callable mapping a user query to an agent response.
            target_prompt: Registry of prompt fields to optimize.
            train_dataset_path: Path to the training eval set file.
            validation_dataset_path: Path to the validation eval set file (must
                differ from ``train_dataset_path``).
            output_dir: Required artifact directory. The facade creates it when
                missing and persists ``result.json``, ``summary.txt``,
                ``rounds/`` records, ``baseline_prompts/`` and ``best_prompts/``
                directories, a redacted ``config.snapshot.json`` copy of the
                input config, and a ``run.log`` summary line.
            callbacks: Optional evaluator lifecycle callbacks.
            update_source: When True, persist the best candidate back to
                every registered TargetPrompt field after a SUCCEEDED
                run; when False (default), source files keep their
                baseline content. ``OptimizeResult.best_prompts`` always
                carries the best text regardless, so callers can review
                the proposal before deciding to write back.
            verbose: Reporter verbosity. ``0`` suppresses terminal
                output (artifact persistence still happens). ``1``
                (default): Rich panel header + per-round line + closing
                summary, falling back to ASCII when ``rich`` is missing.
                ``2`` adds gepa-internal log forwarding on the
                ``trpc_agent_sdk.optimizer.gepa`` logger.
            extra_stop_callbacks: Runtime-only stoppers appended after
                gepa-native stoppers. Useful for SLO monitors / kill
                switches. Plain callables surface as
                ``stop_reason="completed"``; wrap in
                ``_LabeledStopper`` (or expose a ``.label`` attribute
                matching :data:`StopReason`) for a stable classification.
            extra_gepa_callbacks: Runtime-only gepa event callbacks
                appended after the framework's built-in
                ``_AgentGEPACallback`` (e.g. forwarding events to a
                dashboard). Each entry should implement the
                ``gepa.core.callback.GEPACallback`` protocol; gepa
                silently ignores callbacks missing a method it invokes.

        Raises:
            FileNotFoundError: if ``config_path`` does not exist.
            pydantic.ValidationError: if the config violates schema constraints.
            ValueError: if ``optimize`` section is missing; if the requested
                ``algorithm.name`` is not registered (message lists every
                algorithm currently in ``OPTIMIZER_REGISTRY.list_registered()``);
                if ``target_prompt`` has no registered fields; if a metric
                requiring session traces is configured under call_agent mode; or
                if ``train_dataset_path`` and ``validation_dataset_path`` resolve
                to the same file (train-test leakage guard).
            TypeError: if ``call_agent`` is not an ``async`` callable.
        """
        cls._precheck_algorithm_name(config_path)
        config = load_optimize_config(config_path)
        cls._validate_inputs(
            config=config,
            call_agent=call_agent,
            target_prompt=target_prompt,
            train_dataset_path=train_dataset_path,
            validation_dataset_path=validation_dataset_path,
            output_dir=output_dir,
        )
        os.makedirs(output_dir, exist_ok=True)

        algorithm_name = config.optimize.algorithm.name
        algorithm_cls = OPTIMIZER_REGISTRY.get(algorithm_name)
        optimizer = algorithm_cls(
            config=config,
            call_agent=call_agent,
            target_prompt=target_prompt,
            train_dataset_path=train_dataset_path,
            validation_dataset_path=validation_dataset_path,
            callbacks=callbacks,
            output_dir=output_dir,
            extra_stop_callbacks=extra_stop_callbacks,
            extra_gepa_callbacks=extra_gepa_callbacks,
        )

        reporter = create_reporter(verbose=verbose, stream=sys.stdout)
        baseline_snapshot = await target_prompt.read_all()
        header = cls._build_run_header(
            algorithm=algorithm_name,
            target_prompt=target_prompt,
            config=config,
            train_dataset_path=train_dataset_path,
            validation_dataset_path=validation_dataset_path,
            output_dir=output_dir,
        )
        cls._safe_reporter_call(reporter.run_started, header)

        result: Optional[OptimizeResult] = None
        # ``cleanup_done`` gates whether the ``finally`` block must restore
        # baseline. It flips to True after EITHER (a) write_all(best) succeeded
        # (so sources already hold the desired content and no restore is
        # needed) OR (b) the ``except`` branch successfully wrote baseline back
        # as part of its rollback. This single sentinel guarantees baseline
        # write_all is invoked at most once per optimize() — important for
        # callback-backed fields whose write_fn may be non-idempotent (version
        # counters, audit log entries).
        cleanup_done = False
        run_error: Optional[BaseException] = None
        try:
            try:
                result = await optimizer.run(reporter=reporter)
            except BaseException as ex:
                run_error = ex
                raise

            if update_source and result.status == "SUCCEEDED":
                # write_all is atomic for path-backed sources (tmp +
                # os.replace, rollback on partial failure). If it raises,
                # sources may sit at an intermediate candidate from the
                # last in-run evaluation — restore baseline explicitly
                # then re-raise so the caller sees the write failure.
                try:
                    await target_prompt.write_all(result.best_prompts)
                    cleanup_done = True
                except Exception:
                    try:
                        await target_prompt.write_all(baseline_snapshot)
                        cleanup_done = True
                    except Exception:  # pragma: no cover - defensive guard
                        pass
                    raise
        finally:
            if not cleanup_done:
                # Best-effort restore: never mask the underlying run/write error.
                try:
                    await target_prompt.write_all(baseline_snapshot)
                except Exception:  # pragma: no cover - defensive guard
                    pass

            cls._persist_artifacts(
                result=result,
                baseline_snapshot=baseline_snapshot,
                output_dir=output_dir,
                config_path=config_path,
                run_error=run_error,
                update_source=update_source,
            )
            cls._emit_reporter_finish(
                reporter=reporter,
                result=result,
                baseline_snapshot=baseline_snapshot,
                output_dir=output_dir,
                update_source=update_source,
                run_error=run_error,
            )
        return result

    @staticmethod
    def _build_run_header(
        *,
        algorithm: str,
        target_prompt: TargetPrompt,
        config: OptimizeConfigFile,
        train_dataset_path: str,
        validation_dataset_path: str,
        output_dir: str,
    ) -> RunHeader:
        """Collect the static run context surfaced in the terminal header.

        Train / val sizes are read from each EvalSet on disk so the header
        reflects the actual material the algorithm will evaluate, including
        edge cases where one of the sets is empty.
        """
        from ._eval_set import EvalSet
        from pathlib import Path

        def _count_cases(path: str) -> int:
            try:
                return len(EvalSet.model_validate_json(Path(path).read_text(encoding="utf-8")).eval_cases)
            except Exception:
                return 0

        target_fields: list[tuple[str, str]] = []
        for name in target_prompt.names():
            target_fields.append((name, target_prompt.describe_source(name)))

        metric_names = [metric.metric_name for metric in config.evaluate.get_eval_metrics()]
        budget_total = getattr(config.optimize.algorithm, "max_metric_calls", None)
        return RunHeader(
            algorithm=algorithm,
            target_fields=target_fields,
            train_size=_count_cases(train_dataset_path),
            val_size=_count_cases(validation_dataset_path),
            metric_names=metric_names,
            output_dir=output_dir,
            budget_total=budget_total,
        )

    @staticmethod
    def _safe_reporter_call(fn, *args, **kwargs) -> None:
        """Invoke a reporter method, swallowing render errors."""
        try:
            fn(*args, **kwargs)
        except Exception:  # pragma: no cover - reporter must never break the loop
            _PROMPT_FILE_LOGGER.warning("reporter event failed", exc_info=True)

    @classmethod
    def _emit_reporter_finish(
        cls,
        *,
        reporter,
        result: Optional[OptimizeResult],
        baseline_snapshot: dict[str, str],
        output_dir: str,
        update_source: bool,
        run_error: Optional[BaseException],
    ) -> None:
        if result is not None:
            cls._safe_reporter_call(
                reporter.run_finished,
                result,
                output_dir=output_dir,
                update_source=update_source,
            )
            return
        message = (str(run_error) if run_error is not None else "optimization failed")
        cls._safe_reporter_call(
            reporter.run_failed,
            baseline_prompts=dict(baseline_snapshot),
            output_dir=output_dir,
            error_message=message,
        )

    @classmethod
    def _persist_artifacts(
        cls,
        *,
        result: Optional[OptimizeResult],
        baseline_snapshot: dict[str, str],
        output_dir: str,
        config_path: str,
        run_error: Optional[BaseException],
        update_source: bool,
    ) -> None:
        """Write run artifacts under ``output_dir``.

        Layout:
          - ``result.json``                Full OptimizeResult JSON.
          - ``summary.txt``                Human-readable summary.
          - ``rounds/round_<NNN>.json``    One file per RoundRecord.
          - ``baseline_prompts/<name>.md`` Pre-run snapshot of every
                                            TargetPrompt field
                                            (regardless of update_source).
          - ``best_prompts/<name>.md``     Best candidate per field
                                            (only when a result was produced).
          - ``config.snapshot.json``       Redacted copy of the input config.
          - ``run.log``                    One-line status footer.

        SIGINT (Ctrl+C) is masked for the duration of this method so a
        second Ctrl+C during persistence cannot leave half-written
        artifacts. All files are written atomically (tmp + os.replace),
        so even if SIGKILL or a power loss interrupts the process the
        output_dir never contains a partially-written file (only a
        ``.tmp`` sibling that the next run can ignore). Missing pieces
        (e.g. ``best_prompts`` on early failure) are silently omitted.
        """
        with _mask_sigint():
            cls._write_baseline_prompts(baseline_snapshot, output_dir)
            cls._copy_config_snapshot(config_path, output_dir)

            if result is None:
                cls._write_run_log(
                    output_dir=output_dir,
                    line=cls._render_failure_log_line(run_error),
                )
                return

            try:
                _atomic_write_text(
                    os.path.join(output_dir, "result.json"),
                    result.model_dump_json(indent=2, by_alias=True),
                )
            except Exception:  # pragma: no cover - defensive guard for write errors
                _PROMPT_FILE_LOGGER.warning("failed to write result.json", exc_info=True)

            try:
                summary_text = result.format_summary(output_dir=output_dir, update_source=update_source)
                _atomic_write_text(os.path.join(output_dir, "summary.txt"), summary_text)
            except Exception:  # pragma: no cover
                _PROMPT_FILE_LOGGER.warning("failed to write summary.txt", exc_info=True)

            cls._write_rounds_directory(result, output_dir)
            cls._write_best_prompts(result, output_dir)
            cls._write_run_log(
                output_dir=output_dir,
                line=cls._render_success_log_line(result),
            )

    @staticmethod
    def _write_baseline_prompts(baseline_snapshot: dict[str, str], output_dir: str) -> None:
        baseline_dir = os.path.join(output_dir, "baseline_prompts")
        os.makedirs(baseline_dir, exist_ok=True)
        for name, content in baseline_snapshot.items():
            path = os.path.join(baseline_dir, f"{name}.md")
            try:
                _atomic_write_text(path, content)
            except Exception:  # pragma: no cover
                _PROMPT_FILE_LOGGER.warning("failed to write baseline prompt %s", name, exc_info=True)

    @staticmethod
    def _write_best_prompts(result: OptimizeResult, output_dir: str) -> None:
        best_dir = os.path.join(output_dir, "best_prompts")
        os.makedirs(best_dir, exist_ok=True)
        for name, content in result.best_prompts.items():
            path = os.path.join(best_dir, f"{name}.md")
            try:
                _atomic_write_text(path, content)
            except Exception:  # pragma: no cover
                _PROMPT_FILE_LOGGER.warning("failed to write best prompt %s", name, exc_info=True)

    @staticmethod
    def _write_rounds_directory(result: OptimizeResult, output_dir: str) -> None:
        rounds_dir = os.path.join(output_dir, "rounds")
        os.makedirs(rounds_dir, exist_ok=True)
        for record in result.rounds:
            path = os.path.join(rounds_dir, f"round_{record.round:03d}.json")
            try:
                _atomic_write_text(path, record.model_dump_json(indent=2, by_alias=True))
            except Exception:  # pragma: no cover
                _PROMPT_FILE_LOGGER.warning("failed to write round %s", record.round, exc_info=True)

    @staticmethod
    def _copy_config_snapshot(config_path: str, output_dir: str) -> None:
        target = os.path.join(output_dir, "config.snapshot.json")
        try:
            # Parse and redact fully before the first write so neither the
            # destination nor its temporary sibling can ever contain the raw
            # credential-bearing config. Invalid/non-finite JSON therefore
            # fails closed instead of falling back to a plaintext copy.
            raw_config = json.loads(
                Path(config_path).read_text(encoding="utf-8"),
                parse_constant=_reject_non_finite_json_constant,
            )
            redacted_config = _redact_config_secrets(raw_config)
            content = json.dumps(
                redacted_config,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            ) + "\n"
            _atomic_write_text(target, content)
        except Exception:  # pragma: no cover
            _PROMPT_FILE_LOGGER.warning("failed to write redacted config snapshot", exc_info=True)

    @staticmethod
    def _write_run_log(*, output_dir: str, line: str) -> None:
        try:
            _atomic_write_text(
                os.path.join(output_dir, "run.log"),
                line.rstrip("\n") + "\n",
            )
        except Exception:  # pragma: no cover
            _PROMPT_FILE_LOGGER.warning("failed to write run.log", exc_info=True)

    @staticmethod
    def _render_success_log_line(result: OptimizeResult) -> str:
        return (f"{datetime.now(timezone.utc).isoformat()} status={result.status} "
                f"algorithm={result.algorithm} "
                f"baseline={result.baseline_pass_rate:.4f} "
                f"best={result.best_pass_rate:.4f} "
                f"delta={result.pass_rate_improvement:+.4f} "
                f"rounds={result.total_rounds} "
                f"duration_seconds={result.duration_seconds:.2f}")

    @staticmethod
    def _render_failure_log_line(run_error: Optional[BaseException]) -> str:
        msg = str(run_error) if run_error else "optimization failed before result"
        return (f"{datetime.now(timezone.utc).isoformat()} status=FAILED "
                f"error={msg!r}")

    @staticmethod
    def _precheck_algorithm_name(config_path: str) -> None:
        """Friendly fail-fast when ``algorithm.name`` is unknown.

        ``GepaReflectiveAlgo.name`` is declared as ``Literal["gepa_reflective"]``
        for future pydantic-discriminator-based union routing. The Literal
        causes pydantic to reject unknown names with a ``literal_error`` that
        does not list available algorithms. We pre-read the raw JSON, look up
        ``algorithm.name`` against ``OPTIMIZER_REGISTRY``, and raise a
        ``ValueError`` listing every registered algorithm before pydantic's
        Literal check fires. If parsing fails or the field is absent we let
        pydantic's normal error path handle it (so we do not duplicate
        formatting errors).
        """
        import json

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError):
            return  # let pydantic / load_optimize_config surface the real cause

        try:
            name = raw["optimize"]["algorithm"]["name"]
        except (KeyError, TypeError):
            return  # malformed shape: pydantic will raise a structured error

        if not isinstance(name, str):
            return  # type error: let pydantic's normal validation handle it

        registered = OPTIMIZER_REGISTRY.list_registered()
        if name not in registered:
            raise ValueError(f"No optimizer registered for algorithm: {name!r}. "
                             f"Available algorithms: {registered}")

    @staticmethod
    def _validate_inputs(
        *,
        config,
        call_agent: CallAgent,
        target_prompt: TargetPrompt,
        train_dataset_path: str,
        validation_dataset_path: str,
        output_dir: str,
    ) -> None:
        """Startup-time fail-fast checks.

        Reports actionable error messages so misconfigurations surface before
        any LLM call is made.
        """
        if not output_dir or not isinstance(output_dir, str):
            raise ValueError("output_dir is required and must be a non-empty path; "
                             "pass output_dir='runs/<timestamp>' or similar.")

        if not target_prompt.names():
            raise ValueError("TargetPrompt has no registered fields; "
                             "call .add_path(...) or .add_callback(...) before optimize().")

        # Accept async functions and partials wrapping a coroutine function.
        is_async = inspect.iscoroutinefunction(call_agent)
        if not is_async:
            wrapped = getattr(call_agent, "__wrapped__", None)
            is_async = wrapped is not None and inspect.iscoroutinefunction(wrapped)
        if not is_async:
            raise TypeError("call_agent must be an async callable (async def or "
                            "Callable returning Awaitable[str]); "
                            f"got {type(call_agent).__name__}.")

        # Normalize so trivially-different strings ('./x', 'x') still collide
        # when they resolve to the same file (train-validation leakage guard).
        train_norm = os.path.normpath(os.path.abspath(train_dataset_path))
        val_norm = os.path.normpath(os.path.abspath(validation_dataset_path))
        if train_norm == val_norm:
            raise ValueError("train_dataset_path and validation_dataset_path resolve to the "
                             f"same file ({train_norm}); use distinct datasets to avoid "
                             "train-validation leakage.")

        # call_agent (black-box) mode can't supply session traces or
        # tool intermediate_data. ``get_eval_metrics()`` normalizes both
        # 'metrics' and 'criteria' encodings so this check is uniform.
        for metric in config.evaluate.get_eval_metrics():
            if metric.metric_name in _DISALLOWED_METRICS_IN_CALL_AGENT_MODE:
                raise ValueError(f"Metric '{metric.metric_name}' requires session "
                                 "traces or tool intermediate data, which call_agent "
                                 "(black-box) mode does not capture; remove it from "
                                 "evaluate.metrics or switch to a response-based metric "
                                 "(e.g. final_response_avg_score, llm_rubric_response, "
                                 "llm_final_response).")

        # gepa merge degenerates to "pick one of two parents" with a single
        # component, never producing new candidates. Warn instead of error
        # so existing benign configs keep running; user gets a clear hint
        # that merge_rounds_total will be 0.
        algo = config.optimize.algorithm
        if (getattr(algo, "name", None) == "gepa_reflective" and getattr(algo, "use_merge", False)
                and len(target_prompt.names()) < 2):
            warnings.warn(
                "use_merge=true requires TargetPrompt to register at least 2 "
                "fields. With a single field, gepa merge degenerates to "
                "picking one of the two parents and never creates new "
                "candidates (merge_rounds_total stays 0). Set use_merge=false "
                "or register more prompt fields. See "
                "examples/optimization/advanced_strategies/README.md §6.1.",
                UserWarning,
                stacklevel=2,
            )
