"""Run the shared asynchronous Evaluation + Optimization pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import secrets
import sys
import tempfile
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:  # Package import during tests.
    from .eval_loop.artifacts import validate_artifact_component
    from .eval_loop.artifacts import validate_distinct_file_paths
    from .eval_loop.backends import FakeBackend
    from .eval_loop.backends import SDKBackend
    from .eval_loop.config import _parse_gate_config
    from .eval_loop.config import parse_optimizer_config
    from .eval_loop.config import resolve_effective_seed
    from .eval_loop.loader import read_json
    from .eval_loop.pipeline import PipelineRequest
    from .eval_loop.pipeline import execute_pipeline
    from .eval_loop.schemas import OptimizationReport
except ImportError:  # Direct script execution.
    from eval_loop.artifacts import validate_artifact_component
    from eval_loop.artifacts import validate_distinct_file_paths
    from eval_loop.backends import FakeBackend
    from eval_loop.backends import SDKBackend
    from eval_loop.config import _parse_gate_config
    from eval_loop.config import parse_optimizer_config
    from eval_loop.config import resolve_effective_seed
    from eval_loop.loader import read_json
    from eval_loop.pipeline import PipelineRequest
    from eval_loop.pipeline import execute_pipeline
    from eval_loop.schemas import OptimizationReport


DEFAULT_TRAIN = HERE / "data" / "train.evalset.json"
DEFAULT_VAL = HERE / "data" / "val.evalset.json"
DEFAULT_OPTIMIZER_CONFIG = HERE / "data" / "optimizer.json"
DEFAULT_PROMPT = HERE / "prompts" / "baseline_system_prompt.txt"
DEFAULT_OUTPUT_DIR = Path(tempfile.gettempdir()) / "eval-optimize-loop"
TARGET_PROMPT_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


async def run_pipeline_async(
    *,
    train_path: str | Path = DEFAULT_TRAIN,
    val_path: str | Path = DEFAULT_VAL,
    optimizer_config_path: str | Path = DEFAULT_OPTIMIZER_CONFIG,
    prompt_path: str | Path = DEFAULT_PROMPT,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    mode: str = "fake",
    fake_model: bool = True,
    fake_judge: bool = True,
    trace: bool = False,
    sdk_call_agent: str | None = None,
    update_source: bool = False,
    gate_config_path: str | Path | None = None,
    target_prompts: list[str] | None = None,
    run_id: str | None = None,
    backend: Any | None = None,
) -> OptimizationReport:
    """Build dependencies and await the backend-neutral orchestration core."""

    request, selected_backend = build_pipeline_request_and_backend(
        train_path=train_path,
        val_path=val_path,
        optimizer_config_path=optimizer_config_path,
        prompt_path=prompt_path,
        output_dir=output_dir,
        mode=mode,
        fake_model=fake_model,
        fake_judge=fake_judge,
        trace=trace,
        sdk_call_agent=sdk_call_agent,
        update_source=update_source,
        gate_config_path=gate_config_path,
        target_prompts=target_prompts,
        run_id=run_id,
        backend=backend,
    )
    return await execute_pipeline(
        request,
        evaluator=selected_backend,
        optimizer=selected_backend,
    )


def run_pipeline(
    *,
    train_path: str | Path = DEFAULT_TRAIN,
    val_path: str | Path = DEFAULT_VAL,
    optimizer_config_path: str | Path = DEFAULT_OPTIMIZER_CONFIG,
    prompt_path: str | Path = DEFAULT_PROMPT,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    mode: str = "fake",
    fake_model: bool = True,
    fake_judge: bool = True,
    trace: bool = False,
    sdk_call_agent: str | None = None,
    update_source: bool = False,
    gate_config_path: str | Path | None = None,
    target_prompts: list[str] | None = None,
    run_id: str | None = None,
    backend: Any | None = None,
) -> OptimizationReport:
    """Synchronous compatibility facade for callers without an active loop."""

    if _has_running_loop():
        raise ValueError(
            "run_pipeline() cannot run while an event loop is active; "
            "await run_pipeline_async(...) instead."
        )
    return asyncio.run(
        run_pipeline_async(
            train_path=train_path,
            val_path=val_path,
            optimizer_config_path=optimizer_config_path,
            prompt_path=prompt_path,
            output_dir=output_dir,
            mode=mode,
            fake_model=fake_model,
            fake_judge=fake_judge,
            trace=trace,
            sdk_call_agent=sdk_call_agent,
            update_source=update_source,
            gate_config_path=gate_config_path,
            target_prompts=target_prompts,
            run_id=run_id,
            backend=backend,
        )
    )


def build_pipeline_request_and_backend(
    *,
    train_path: str | Path = DEFAULT_TRAIN,
    val_path: str | Path = DEFAULT_VAL,
    optimizer_config_path: str | Path = DEFAULT_OPTIMIZER_CONFIG,
    prompt_path: str | Path = DEFAULT_PROMPT,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    mode: str = "fake",
    fake_model: bool = True,
    fake_judge: bool = True,
    trace: bool = False,
    sdk_call_agent: str | None = None,
    update_source: bool = False,
    gate_config_path: str | Path | None = None,
    target_prompts: list[str] | None = None,
    run_id: str | None = None,
    backend: Any | None = None,
) -> tuple[PipelineRequest, Any]:
    """Validate wrapper inputs and construct one backend for both protocols."""

    if mode not in {"fake", "sdk"}:
        raise ValueError("field 'mode' must be one of: fake, sdk")
    if mode == "fake" and (not fake_model or not fake_judge):
        raise ValueError(
            "fake mode requires fake_model=True and fake_judge=True. Pass --fake-model "
            "--fake-judge or use --mode sdk with --sdk-call-agent module:function."
        )

    validate_distinct_file_paths(
        {"train": train_path, "validation": val_path},
        context="train and validation evalset paths",
    )

    target_prompt_paths = _parse_target_prompt_paths(
        target_prompts,
        default_prompt_path=prompt_path,
    )
    effective_run_id = validate_run_id(run_id) if run_id is not None else _default_run_id(mode)

    optimizer_payload = read_json(optimizer_config_path)
    effective_seed = resolve_effective_seed(
        optimizer_payload,
        path=optimizer_config_path,
        strict_legacy=mode == "fake",
    )
    if mode == "fake" and backend is not None and hasattr(backend, "seed"):
        backend_seed = getattr(backend, "seed")
        if (
            isinstance(backend_seed, bool)
            or not isinstance(backend_seed, int)
            or backend_seed != effective_seed
        ):
            raise ValueError(
                f"fake backend seed {backend_seed!r} does not match effective seed {effective_seed}"
            )
    if mode == "fake":
        optimizer_config = parse_optimizer_config(
            optimizer_payload,
            path=optimizer_config_path,
        )
        gate_config = (
            _load_sdk_gate_config(gate_config_path)
            if gate_config_path is not None
            else optimizer_config.gate.to_dict()
        )
        gate_config_source = "file" if gate_config_path is not None else "optimizer"
        selected_backend = backend or FakeBackend(
            seed=effective_seed,
            trace_enabled=trace,
        )
    else:
        gate_config = _load_sdk_gate_config(gate_config_path)
        gate_config_source = "file" if gate_config_path is not None else "request"
        if backend is None:
            if not sdk_call_agent:
                raise ValueError("sdk mode requires --sdk-call-agent module:function")
            selected_backend = SDKBackend(
                prompt_path=target_prompt_paths.get("system_prompt", prompt_path),
                call_agent_path=sdk_call_agent,
                target_prompt_paths=target_prompt_paths,
            )
        else:
            selected_backend = backend

    request = PipelineRequest(
        train_path=Path(train_path),
        validation_path=Path(val_path),
        optimizer_config_path=Path(optimizer_config_path),
        output_dir=Path(output_dir),
        target_prompt_paths=target_prompt_paths,
        gate_config=gate_config,
        trace=trace,
        update_source=update_source,
        mode=mode,
        run_id=effective_run_id,
        sdk_call_agent=sdk_call_agent,
        gate_config_path=Path(gate_config_path) if gate_config_path is not None else None,
        effective_seed=effective_seed,
        gate_config_source=gate_config_source,
    )
    return request, selected_backend


def _load_sdk_gate_config(gate_config_path: str | Path | None) -> dict[str, Any]:
    """Load the independent wrapper gate with the full strict GateConfig schema."""

    if gate_config_path is None:
        gate_payload: dict[str, Any] = {}
        path_text = "--gate-config"
    else:
        try:
            payload = read_json(gate_config_path)
        except ValueError as exc:
            raise ValueError(
                f"--gate-config {gate_config_path}: invalid JSON for gate numeric fields "
                f"(including max_total_cost): {exc}"
            ) from exc
        gate_payload = payload.get("gate", payload)
        path_text = str(gate_config_path)
    if gate_payload is None:
        gate_payload = {}
    if not isinstance(gate_payload, dict):
        raise ValueError(f"{path_text}: field 'gate' must be an object when present")
    return _parse_gate_config(
        gate_payload,
        path=f"--gate-config {path_text}",
    ).to_dict()


def _parse_target_prompt_paths(
    target_prompts: list[str] | None,
    *,
    default_prompt_path: str | Path,
) -> dict[str, str | Path]:
    """Parse, resolve, and de-duplicate name=path target prompt arguments."""

    if not target_prompts:
        return {"system_prompt": Path(default_prompt_path).resolve()}
    parsed: dict[str, str | Path] = {}
    casefold_names: set[str] = set()
    for item in target_prompts:
        if "=" not in item:
            raise ValueError("--target-prompt must use name=path format")
        name, raw_path = item.split("=", 1)
        path = raw_path.strip()
        if not TARGET_PROMPT_FIELD_RE.fullmatch(name):
            raise ValueError(
                f"--target-prompt field name {name!r} is invalid; "
                "use /^[A-Za-z_][A-Za-z0-9_]*$/"
            )
        try:
            validate_artifact_component(name, context="target-prompt field")
        except ValueError as error:
            raise ValueError(f"--target-prompt field name {name!r} is invalid") from error
        if not path:
            raise ValueError("--target-prompt must use non-empty name=path values")
        if name in parsed:
            raise ValueError(f"--target-prompt duplicate field name {name!r}")
        if name.casefold() in casefold_names:
            raise ValueError("--target-prompt field names must be case-insensitively unique")
        casefold_names.add(name.casefold())
        parsed[name] = Path(path).resolve()
    validate_distinct_file_paths(
        parsed,
        context="--target-prompt fields",
    )
    return parsed


def validate_run_id(run_id: str) -> str:
    """Validate run IDs before using them as artifact directory names."""

    if not isinstance(run_id, str):
        raise ValueError(f"--run-id value {run_id!r} must be a string")
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError(f"--run-id value {run_id!r} is invalid")
    try:
        return validate_artifact_component(run_id, context="run_id")
    except ValueError as error:
        raise ValueError(f"--run-id value {run_id!r} is invalid") from error


def _default_run_id(mode: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"eval_optimize_loop_{mode}_{timestamp}_{secrets.token_hex(3)}"


def _has_running_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", default=str(DEFAULT_TRAIN), help="Path to train.evalset.json")
    parser.add_argument("--val", default=str(DEFAULT_VAL), help="Path to val.evalset.json")
    parser.add_argument(
        "--optimizer-config",
        default=str(DEFAULT_OPTIMIZER_CONFIG),
        help="Path to optimizer.json",
    )
    parser.add_argument("--prompt", default=str(DEFAULT_PROMPT), help="Path to baseline system prompt")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for runtime reports")
    parser.add_argument("--mode", choices=("fake", "sdk"), default="fake", help="Backend mode")
    parser.add_argument("--fake-model", action="store_true", help="Use deterministic fake model")
    parser.add_argument("--fake-judge", action="store_true", help="Use deterministic fake judge")
    parser.add_argument("--trace", action="store_true", help="Persist evaluator trace details per case")
    parser.add_argument("--sdk-call-agent", help="Async call_agent target for SDK mode, as module:function")
    parser.add_argument("--update-source", action="store_true", help="Write the accepted prompt after audit")
    parser.add_argument("--gate-config", help="Independent wrapper gate configuration")
    parser.add_argument(
        "--target-prompt",
        action="append",
        help="Target prompt as name=path; may be repeated and defaults to system_prompt=--prompt.",
    )
    parser.add_argument("--run-id", help="Optional stable report/audit run ID")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> OptimizationReport:
    args = parse_args(argv)
    report = run_pipeline(
        train_path=args.train,
        val_path=args.val,
        optimizer_config_path=args.optimizer_config,
        prompt_path=args.prompt,
        output_dir=args.output_dir,
        mode=args.mode,
        fake_model=args.fake_model or args.mode == "fake",
        fake_judge=args.fake_judge or args.mode == "fake",
        trace=args.trace,
        sdk_call_agent=args.sdk_call_agent,
        update_source=args.update_source,
        gate_config_path=args.gate_config,
        target_prompts=args.target_prompt,
        run_id=args.run_id,
    )
    output_dir = Path(args.output_dir)
    print(f"Wrote {output_dir / 'optimization_report.json'}")
    print(f"Wrote {output_dir / 'optimization_report.md'}")
    print(f"Selected candidate: {report.selected_candidate}")
    return report


if __name__ == "__main__":
    main()
