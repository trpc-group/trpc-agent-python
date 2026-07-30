"""Validated, content-addressed run inputs for the optimization example."""

from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
from typing import Any, Optional

from trpc_agent_sdk.evaluation import EvalSet

from .artifacts import load_strict_json
from .evaluation import canonical_json, dataset_fingerprint, validate_datasets
from .configuration import PipelineConfig, ValidatedRunConfig
from .schema import validate_safe_component, validate_secret_free_text
from .prompt_workspace import prompt_hashes
from .trace_fixture import TraceFixture


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inside_example_root(root: Path, relative: str, label: str) -> Path:
    """Resolve a configured path while preventing escape from the example root."""

    raw = Path(relative)
    if raw.is_absolute():
        raise ValueError(f"{label} must be relative to the example root")
    resolved = (root / raw).resolve()
    if root != resolved and root not in resolved.parents:
        raise ValueError(f"{label} escapes the example root")
    return resolved


def _component_identity(component: object | None, default: str) -> str:
    if component is None:
        return default
    component_type = type(component)
    return f"{component_type.__module__}.{component_type.__qualname__}"


def _callable_identity(call_agent: object | None, callback_spec: Optional[str]) -> Optional[str]:
    if callback_spec:
        return callback_spec
    if call_agent is None:
        return None
    module = getattr(call_agent, "__module__", type(call_agent).__module__)
    qualname = getattr(call_agent, "__qualname__", type(call_agent).__qualname__)
    return f"{module}:{qualname}"


def _callable_source_path(call_agent: object | None) -> Optional[Path]:
    if call_agent is None:
        return None
    try:
        source = inspect.getsourcefile(inspect.unwrap(call_agent)) or inspect.getfile(call_agent)
    except (OSError, TypeError):
        return None
    return Path(source).resolve() if source else None


def _adapter_identity(
    mode: str,
    *,
    call_agent: object | None,
    callback_spec: Optional[str],
    backend: object | None,
    candidate_generator: object | None,
) -> str:
    """Stable identity for the components that can change execution behavior."""

    return canonical_json({
        "mode":
        mode,
        "backend":
        _component_identity(backend, f"default:{mode}:evaluation:v1"),
        "candidateGenerator":
        _component_identity(candidate_generator, f"default:{mode}:candidate-generator:v1"),
        "callback":
        _callable_identity(call_agent, callback_spec) if mode == "live" else None,
    })


def preflight_run(
    root_dir: str,
    *,
    config_path: Optional[str],
    train_path: Optional[str],
    validation_path: Optional[str],
    mode: Optional[str],
    run_id: Optional[str],
    apply_candidate: Optional[bool],
    call_agent: object | None,
    callback_spec: Optional[str],
    backend: object | None,
    candidate_generator: object | None,
) -> ValidatedRunConfig:
    """Load all inputs and derive the immutable run identity before side effects."""

    root = Path(root_dir).resolve()
    config_file = Path(config_path).resolve() if config_path else root / "optimizer.json"
    train_file = Path(train_path).resolve() if train_path else root / "train.evalset.json"
    validation_file = Path(validation_path).resolve() if validation_path else root / "val.evalset.json"
    config = PipelineConfig.model_validate(load_strict_json(config_file))
    settings = config.pipeline
    updates: dict[str, Any] = {}
    if mode is not None:
        updates["mode"] = mode
    if run_id is not None:
        updates["run_id"] = run_id
    if apply_candidate is not None:
        updates["apply_candidate"] = apply_candidate
    if updates:
        settings = type(settings).model_validate({**settings.model_dump(mode="python", by_alias=False), **updates})
        config = config.model_copy(update={"pipeline": settings})

    train = EvalSet.model_validate(load_strict_json(train_file))
    validation = EvalSet.model_validate(load_strict_json(validation_file))
    metric_names = [metric.metric_name for metric in config.evaluate.get_eval_metrics()]
    validate_datasets(
        train,
        validation,
        train_path=str(train_file),
        validation_path=str(validation_file),
        configured_metrics=metric_names,
        critical_case_ids=settings.critical_case_ids,
        hard_case_ids=settings.hard_case_ids,
        metric_weights=settings.metric_weights,
        train_case_weights=settings.train_case_weights,
        validation_case_weights=settings.validation_case_weights,
    )

    prompt_paths: dict[str, str] = {}
    prompts: dict[str, str] = {}
    for name, relative in settings.prompt_paths.items():
        validate_safe_component(name, name="prompt field")
        path = inside_example_root(root, relative, f"prompt path {name!r}")
        prompts[name] = validate_secret_free_text(
            path.read_text(encoding="utf-8"),
            name=f"prompt {name!r}",
        )
        prompt_paths[name] = str(path)

    hashes = {
        "config":
        _sha256_file(config_file),
        "effectiveConfig":
        hashlib.sha256(canonical_json(config.model_dump(mode="json", by_alias=True)).encode("utf-8")).hexdigest(),
        "train":
        dataset_fingerprint(train),
        "validation":
        dataset_fingerprint(validation),
    }
    trace_path: Optional[Path] = None
    if settings.mode == "trace":
        trace_path = inside_example_root(root, settings.trace_fixture, "trace fixture")
        hashes["trace"] = _sha256_file(trace_path)
        TraceFixture(
            trace_path,
            {
                "train": hashes["train"],
                "validation": hashes["validation"],
            },
            hashes["trace"],
        ).validate(train, validation)

    adapter_identity = _adapter_identity(
        settings.mode,
        call_agent=call_agent,
        callback_spec=callback_spec,
        backend=backend,
        candidate_generator=candidate_generator,
    )
    reproducibility_paths = [config_file, train_file, validation_file]
    reproducibility_paths.extend(Path(path) for path in prompt_paths.values())
    if trace_path is not None:
        reproducibility_paths.append(trace_path)
    reproducibility_issues: list[str] = []
    if settings.mode == "live" and callback_spec:
        callback_source = _callable_source_path(call_agent)
        if callback_source is None:
            reproducibility_issues.append("live_callback_source_unavailable")
        else:
            reproducibility_paths.append(callback_source)
    derived = ("run-" + hashlib.sha256(
        canonical_json({
            "inputHashes": hashes,
            "promptHashes": prompt_hashes(prompts),
            "adapterIdentity": adapter_identity,
        }).encode("utf-8")).hexdigest()[:16])
    effective_run_id = settings.run_id or derived
    validate_safe_component(effective_run_id, name="run ID")
    artifact_root = inside_example_root(root, settings.artifact_root, "artifact root")
    return ValidatedRunConfig(
        root_dir=str(root),
        config_path=str(config_file),
        train_path=str(train_file),
        validation_path=str(validation_file),
        trace_fixture_path=str(trace_path) if trace_path else None,
        artifact_root=str(artifact_root),
        run_id=effective_run_id,
        config=config,
        train=train,
        validation=validation,
        input_hashes=hashes,
        prompt_paths=prompt_paths,
        prompt_hashes=prompt_hashes(prompts),
        adapter_identity=adapter_identity,
        reproducibility_paths=tuple(str(path.resolve()) for path in reproducibility_paths),
        reproducibility_issues=tuple(reproducibility_issues),
    )
