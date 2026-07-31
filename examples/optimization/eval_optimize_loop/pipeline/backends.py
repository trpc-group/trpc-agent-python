"""SDK-backed fake, trace and live adapters plus candidate generators."""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Awaitable, Callable, Optional

from trpc_agent_sdk.evaluation import (
    AgentEvaluator,
    EvalConfig,
    EvalSet,
    EvalSetAggregateResult,
    EvaluatorRegistry,
    EvaluateConfig,
    EvaluateRequest,
    EvaluateResult,
    InMemoryEvalSetsManager,
    InferenceConfig,
    InferenceRequest,
    LocalEvalService,
    OptimizeResult,
    RemoteEvalService,
    TargetPrompt,
)

from .contracts import CandidateGenerator, EvaluationBackend
from .live_adapter import LiveAdapterSpec
from .models import (
    AttributionSnapshot,
    CandidateProposal,
    CandidateRound,
    CostSource,
    Phase,
    Split,
)
from .offline_evaluation import prepare_offline_evaluation
from .schema import add_exception_note, parse_strict_json, sanitized_text
from .trace_fixture import TraceFixture

CallAgent = Callable[[str], Awaitable[str]]
_DEFAULT_APP_NAME = "test_app"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


async def _evaluate_with_sdk(
    eval_set: EvalSet,
    eval_config: EvalConfig,
    *,
    call_agent: Optional[CallAgent],
    runtime_dir: str,
    evaluator_registry: Optional[EvaluatorRegistry] = None,
) -> EvaluateResult:
    """Use AgentEvaluator by default and services only for a run-local registry."""

    dataset = deepcopy(eval_set)
    config = deepcopy(eval_config)
    runtime_root = Path(runtime_dir)
    if not runtime_root.is_dir():
        raise FileNotFoundError(f"backend audit directory does not exist: {runtime_root}")
    if config.num_runs < 1:
        raise ValueError("evaluation num_runs must be at least one")

    app_name = dataset.app_name or _DEFAULT_APP_NAME
    if evaluator_registry is None:
        _, _, _, results_by_case = await AgentEvaluator.evaluate_eval_set(
            dataset,
            call_agent=call_agent,
            eval_config=config,
            num_runs=config.num_runs,
            print_detailed_results=False,
        )
        return EvaluateResult(
            results_by_eval_set_id={
                dataset.eval_set_id:
                EvalSetAggregateResult(
                    eval_results_by_eval_id=results_by_case,
                    num_runs=config.num_runs,
                )
            })

    # AgentEvaluator has no per-call registry hook. Keep custom offline judges
    # scoped to this run by composing the exported services directly.
    manager = InMemoryEvalSetsManager()
    manager.create_eval_set(app_name=app_name, eval_set_id=dataset.eval_set_id)
    for eval_case in dataset.eval_cases:
        manager.add_eval_case(
            app_name=app_name,
            eval_set_id=dataset.eval_set_id,
            eval_case=eval_case,
        )

    if call_agent is None:
        service = LocalEvalService(
            root_agent=None,
            eval_sets_manager=manager,
            evaluator_registry=evaluator_registry,
        )
    else:
        service = RemoteEvalService(
            call_agent=call_agent,
            eval_sets_manager=manager,
            evaluator_registry=evaluator_registry,
        )

    inference_results = []
    inference_request = InferenceRequest(
        app_name=app_name,
        eval_set_id=dataset.eval_set_id,
        inference_config=InferenceConfig(),
    )
    for run_id in range(1, config.num_runs + 1):
        async for inference_result in service.perform_inference(inference_request):
            inference_result.run_id = run_id
            inference_results.append(inference_result)

    results_by_case = {}
    evaluate_request = EvaluateRequest(
        inference_results=inference_results,
        evaluate_config=EvaluateConfig(eval_metrics=config.get_eval_metrics()),
    )
    async for case_result in service.evaluate(evaluate_request):
        results_by_case.setdefault(case_result.eval_id, []).append(case_result)

    return EvaluateResult(
        results_by_eval_set_id={
            dataset.eval_set_id: EvalSetAggregateResult(
                eval_results_by_eval_id=results_by_case,
                num_runs=config.num_runs,
            )
        })


async def _evaluate_offline(
    eval_set: EvalSet,
    eval_config: EvalConfig,
    *,
    call_agent: Optional[CallAgent],
    runtime_dir: str,
) -> EvaluateResult:
    """Evaluate using a run-local deterministic replacement registry."""

    offline_config, registry = prepare_offline_evaluation(eval_config)
    return await _evaluate_with_sdk(
        eval_set,
        offline_config,
        call_agent=call_agent,
        runtime_dir=runtime_dir,
        evaluator_registry=registry,
    )


def _prompt_profile(prompts: dict[str, str]) -> bool:
    return any("BEHAVIOR_PROFILE=precision-v2" in text for text in prompts.values())


def deterministic_fake_response(query: str, prompts: dict[str, str]) -> str:
    """Offline agent used by the public example; behavior is data-driven, not ID-driven."""

    fields: dict[str, str] = {}
    for component in query.split(";"):
        if ":" in component:
            key, value = component.split(":", 1)
            fields[key.strip().upper()] = value.strip()
    behavior = fields.get("BEHAVIOR")
    answer = fields.get("ANSWER")
    if behavior not in {"improve", "regress", "stable"} or answer is None:
        return "INVALID_REQUEST"
    candidate = _prompt_profile(prompts)
    if behavior == "improve":
        return answer if candidate else "INCORRECT"
    if behavior == "regress":
        return "INCORRECT" if candidate else answer
    return answer


class FakeEvaluationBackend:
    """Deterministic evaluator that never reads API credentials or uses the network."""

    async def evaluate(
        self,
        *,
        eval_set: EvalSet,
        eval_config: EvalConfig,
        prompts: dict[str, str],
        split: Split,
        phase: Phase,
        audit_dir: str,
    ) -> EvaluateResult:
        del split, phase
        prompt_copy = deepcopy(prompts)

        async def call_agent(query: str) -> str:
            return deterministic_fake_response(query, prompt_copy)

        return await _evaluate_offline(
            eval_set,
            eval_config,
            call_agent=call_agent,
            runtime_dir=audit_dir,
        )


class TraceEvaluationBackend:
    """Replay actual conversations from a hash-pinned fixture through LocalEvalService."""

    def __init__(
        self,
        fixture_path: str,
        dataset_hashes: dict[str, str],
        fixture_hash: Optional[str] = None,
    ) -> None:
        self._fixture = TraceFixture(fixture_path, dataset_hashes, fixture_hash)

    def validate_fixture(self, train: EvalSet, validation: EvalSet) -> None:
        """Fail before run creation when any pinned trace input has drifted."""

        self._fixture.validate(train, validation)

    def _trace_eval_set(self, eval_set: EvalSet, split: Split, phase: Phase) -> EvalSet:
        return self._fixture.eval_set(eval_set, split, phase)

    async def evaluate(
        self,
        *,
        eval_set: EvalSet,
        eval_config: EvalConfig,
        prompts: dict[str, str],
        split: Split,
        phase: Phase,
        audit_dir: str,
    ) -> EvaluateResult:
        del prompts
        traced = self._trace_eval_set(deepcopy(eval_set), split, phase)
        return await _evaluate_offline(
            traced,
            eval_config,
            call_agent=None,
            runtime_dir=audit_dir,
        )


class LiveEvaluationBackend:
    """Run an injected async business callback through RemoteEvalService."""

    def __init__(self, call_agent: CallAgent) -> None:
        if not inspect.iscoroutinefunction(call_agent):
            raise TypeError("live call_agent must be an async function")
        self._call_agent = call_agent

    async def evaluate(
        self,
        *,
        eval_set: EvalSet,
        eval_config: EvalConfig,
        prompts: dict[str, str],
        split: Split,
        phase: Phase,
        audit_dir: str,
    ) -> EvaluateResult:
        del prompts, split, phase
        return await _evaluate_with_sdk(
            eval_set,
            eval_config,
            call_agent=self._call_agent,
            runtime_dir=audit_dir,
        )


class DeterministicCandidateGenerator:
    """Generate one auditable candidate solely from inner-train failure facts."""

    async def generate(
        self,
        *,
        target_prompt: TargetPrompt,
        baseline_prompts: dict[str, str],
        train_attribution: AttributionSnapshot,
        inner_train_path: str,
        inner_selection_path: str,
        config_path: str,
        output_dir: str,
    ) -> CandidateProposal:
        del inner_train_path, inner_selection_path, config_path, output_dir
        if set(target_prompt.names()) != set(baseline_prompts):
            raise ValueError("candidate target fields do not match baseline prompts")
        changed = bool(train_attribution.failures)
        prompts = dict(baseline_prompts)
        rounds: tuple[CandidateRound, ...] = ()
        if changed:
            for name in prompts:
                prompts[name] = prompts[name].rstrip() + "\n\nBEHAVIOR_PROFILE=precision-v2\n"
            rounds = (CandidateRound(
                round=1,
                candidate_prompts=prompts,
                accepted=True,
                score=1.0,
                kind="deterministic",
                optimized_fields=tuple(prompts),
                acceptance_reason="deterministic failure-attribution rewrite",
                cost_usd=0,
            ), )
        proposal = CandidateProposal(
            algorithm="deterministic_failure_rewrite",
            baseline_prompts=dict(baseline_prompts),
            prompts=prompts,
            changed=changed,
            stop_reason="completed",
            rounds=rounds,
            cost_sources=(CostSource(name="deterministic", cost_usd=0, model_calls=0, metric_calls=0), ),
            duration_seconds=0,
        )
        return proposal


class LiveCandidateGenerator:
    """Adapt AgentOptimizer output into the strict candidate-only contract."""

    def __init__(
        self,
        live_adapter: LiveAdapterSpec,
        *,
        shutdown_timeout_seconds: float = 10.0,
        verbose: int = 0,
    ) -> None:
        if shutdown_timeout_seconds <= 0:
            raise ValueError("optimizer shutdown timeout must be positive")
        self._live_adapter = live_adapter
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._verbose = verbose

    @staticmethod
    def _request_stop(output_dir: str, cancellation: BaseException) -> None:
        stop_path = Path(output_dir) / "optimize.stop"
        try:
            stop_path.parent.mkdir(parents=True, exist_ok=True)
            stop_path.write_text("cancel requested\n", encoding="utf-8")
        except OSError as stop_error:
            add_exception_note(cancellation, f"could not request optimizer stop: {stop_error}")

    async def _wait_for_worker(
        self,
        process: asyncio.subprocess.Process,
        cancellation: BaseException,
        wait_task: Optional[asyncio.Task[int]],
    ) -> tuple[bool, Optional[asyncio.Task[int]]]:
        if wait_task is None:
            wait_task = asyncio.create_task(process.wait())
        try:
            await asyncio.wait_for(
                asyncio.shield(wait_task),
                timeout=self._shutdown_timeout_seconds,
            )
            return True, None
        except asyncio.TimeoutError:
            return False, wait_task
        except asyncio.CancelledError:
            raise
        except Exception as wait_error:
            detail = sanitized_text(wait_error, max_text_chars=1000)
            add_exception_note(
                cancellation,
                f"optimizer worker wait failed with {type(wait_error).__name__}: {detail}",
            )
            return process.returncode is not None, None

    @staticmethod
    def _signal_worker(
        process: asyncio.subprocess.Process,
        signal_name: str,
        cancellation: BaseException,
    ) -> bool:
        if process.returncode is not None:
            return False
        try:
            getattr(process, signal_name)()
            return True
        except ProcessLookupError:
            return False
        except OSError as signal_error:
            detail = sanitized_text(signal_error, max_text_chars=1000)
            add_exception_note(
                cancellation,
                f"optimizer worker {signal_name} failed with {type(signal_error).__name__}: {detail}",
            )
            return False

    async def _stop_worker(
        self,
        process: asyncio.subprocess.Process,
        *,
        output_dir: str,
        cancellation: BaseException,
    ) -> None:
        self._request_stop(output_dir, cancellation)
        wait_task: Optional[asyncio.Task[int]] = None
        forced = False
        try:
            exited, wait_task = await self._wait_for_worker(process, cancellation, wait_task)
            if exited:
                return
            forced = self._signal_worker(process, "terminate", cancellation)
            exited, wait_task = await self._wait_for_worker(process, cancellation, wait_task)
            if exited:
                if forced:
                    add_exception_note(cancellation, "optimizer worker required forced termination")
                return
            forced = self._signal_worker(process, "kill", cancellation) or forced
            exited, wait_task = await self._wait_for_worker(process, cancellation, wait_task)
            if not exited:
                add_exception_note(cancellation, "optimizer worker did not report exit after kill")
            if forced:
                add_exception_note(cancellation, "optimizer worker required forced termination")
        finally:
            if wait_task is not None:
                wait_task.cancel()
                try:
                    await wait_task
                except BaseException:
                    pass

    async def _complete_worker_stop(
        self,
        process: asyncio.subprocess.Process,
        *,
        output_dir: str,
        cancellation: BaseException,
    ) -> None:
        """Finish bounded child cleanup even if the parent task is canceled again."""

        cleanup = asyncio.create_task(
            self._stop_worker(
                process,
                output_dir=output_dir,
                cancellation=cancellation,
            ))
        repeated_cancellations = 0
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                repeated_cancellations += 1
                continue
            except BaseException:
                break
        if repeated_cancellations:
            add_exception_note(
                cancellation,
                f"worker cleanup resisted {repeated_cancellations} additional cancellation request(s)",
            )
        if cleanup.cancelled():
            add_exception_note(cancellation, "optimizer worker cleanup task was canceled before completion")
            return
        try:
            cleanup.result()
        except BaseException as cleanup_error:
            detail = sanitized_text(cleanup_error, max_text_chars=1000)
            add_exception_note(
                cancellation,
                f"optimizer worker cleanup failed with {type(cleanup_error).__name__}: {detail}",
            )

    async def _optimize_in_worker(self, **kwargs) -> OptimizeResult:
        output_dir = kwargs["output_dir"]
        request_path = Path(output_dir).parent / "optimizer-worker-request.json"
        prompt_paths = {name: kwargs["target_prompt"].describe_source(name) for name in kwargs["target_prompt"].names()}
        request_path.write_text(
            json.dumps(
                {
                    "callbackSpec": self._live_adapter.import_path,
                    "callbackSourcePath": str(self._live_adapter.source_path),
                    "callbackSourceSha256": self._live_adapter.source_sha256,
                    "callbackCallableSha256": self._live_adapter.callable_sha256,
                    "promptPaths": prompt_paths,
                    "configPath": kwargs["config_path"],
                    "trainPath": kwargs["train_dataset_path"],
                    "validationPath": kwargs["validation_dataset_path"],
                    "outputDir": output_dir,
                    "verbose": kwargs["verbose"],
                },
                ensure_ascii=False,
                allow_nan=False,
            ),
            encoding="utf-8",
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "examples.optimization.eval_optimize_loop.pipeline.optimizer_worker",
            str(request_path),
            cwd=str(_REPOSITORY_ROOT),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            return_code = await process.wait()
        except asyncio.CancelledError as cancellation:
            await self._complete_worker_stop(
                process,
                output_dir=output_dir,
                cancellation=cancellation,
            )
            raise
        result_path = Path(output_dir) / "result.json"
        if return_code != 0:
            error_path = Path(output_dir) / "worker_error.json"
            detail = "optimizer worker failed without an error artifact"
            if error_path.is_file():
                payload = parse_strict_json(error_path.read_text(encoding="utf-8"))
                detail = str(payload.get("message", detail))
            raise RuntimeError(f"AgentOptimizer worker failed with exit code {return_code}: {detail}")
        if not result_path.is_file():
            raise RuntimeError("AgentOptimizer worker completed without result.json")
        return OptimizeResult.from_file(str(result_path))

    async def generate(
        self,
        *,
        target_prompt: TargetPrompt,
        baseline_prompts: dict[str, str],
        train_attribution: AttributionSnapshot,
        inner_train_path: str,
        inner_selection_path: str,
        config_path: str,
        output_dir: str,
    ) -> CandidateProposal:
        del train_attribution
        optimize_kwargs = {
            "config_path": config_path,
            "target_prompt": target_prompt,
            "train_dataset_path": inner_train_path,
            "validation_dataset_path": inner_selection_path,
            "output_dir": output_dir,
            "update_source": False,
            "verbose": self._verbose,
        }
        result = await self._optimize_in_worker(**optimize_kwargs)
        reported_baseline = dict(result.baseline_prompts)
        result_prompts = dict(result.best_prompts)
        adapter_errors: list[str] = []
        if result.status == "SUCCEEDED":
            if reported_baseline != baseline_prompts:
                adapter_errors.append("optimizer baseline prompts differ from the workspace baseline")
            if set(result_prompts) != set(baseline_prompts):
                adapter_errors.append("optimizer best prompt keys differ from the workspace schema")
        if result.status != "SUCCEEDED" or adapter_errors:
            result_prompts = dict(baseline_prompts)
        rounds = tuple(
            CandidateRound(
                round=round_.round,
                candidate_prompts=dict(round_.candidate_prompts),
                accepted=round_.accepted,
                score=(round_.validation_pass_rate if round_.candidate_prompts and not getattr(
                    round_, "skip_reason", None) and not getattr(round_, "error_message", None) else None),
                kind=getattr(round_, "kind", "reflective"),
                optimized_fields=tuple(getattr(round_, "optimized_field_names", ())),
                metric_scores=dict(getattr(round_, "metric_breakdown", {})),
                acceptance_reason=getattr(round_, "acceptance_reason", None) or None,
                skip_reason=getattr(round_, "skip_reason", None),
                error_message=getattr(round_, "error_message", None),
                cost_usd=round_.round_llm_cost,
                token_usage=dict(round_.round_token_usage),
                duration_seconds=round_.duration_seconds,
            ) for round_ in result.rounds)
        proposal = CandidateProposal(
            status=result.status,
            error_message=result.error_message or None,
            adapter_error="; ".join(adapter_errors) or None,
            algorithm=result.algorithm,
            baseline_prompts=dict(baseline_prompts),
            prompts=result_prompts,
            changed=result_prompts != baseline_prompts,
            stop_reason=result.stop_reason,
            rounds=rounds,
            cost_sources=(CostSource(
                name="optimizer_reported",
                cost_usd=result.total_llm_cost,
                model_calls=result.total_reflection_lm_calls,
                token_usage=dict(result.total_token_usage),
            ), ),
            duration_seconds=result.duration_seconds,
        )
        return proposal


def create_backends(
    mode: str,
    *,
    live_adapter: Optional[LiveAdapterSpec] = None,
    trace_fixture_path: Optional[str] = None,
    trace_fixture_hash: Optional[str] = None,
    dataset_hashes: Optional[dict[str, str]] = None,
    optimizer_shutdown_timeout_seconds: float = 10.0,
) -> tuple[EvaluationBackend, CandidateGenerator]:
    if mode == "fake":
        return FakeEvaluationBackend(), DeterministicCandidateGenerator()
    if mode == "trace":
        if not trace_fixture_path or dataset_hashes is None:
            raise ValueError("trace mode requires a fixture path and dataset hashes")
        return (
            TraceEvaluationBackend(trace_fixture_path, dataset_hashes, trace_fixture_hash),
            DeterministicCandidateGenerator(),
        )
    if mode == "live":
        if live_adapter is None:
            raise ValueError("live mode requires a validated LiveAdapterSpec")
        return (
            LiveEvaluationBackend(live_adapter.callback),
            LiveCandidateGenerator(
                live_adapter,
                shutdown_timeout_seconds=optimizer_shutdown_timeout_seconds,
            ),
        )
    raise ValueError(f"unsupported mode: {mode!r}")
