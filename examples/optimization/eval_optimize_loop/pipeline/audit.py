from __future__ import annotations

import importlib.metadata
import json
import platform
import subprocess
from pathlib import Path
from typing import Literal

from .config import PipelineConfig, canonical_sha256, sanitize_config
from .models import StrictModel


class InputMetadata(StrictModel):
    config_digest: str
    train_dataset_digest: str
    validation_dataset_digest: str
    prompt_digest: str
    snapshot_path: Path


def _write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return path


def _canonical_file_digest(path: Path) -> str:
    return canonical_sha256(json.loads(path.read_text(encoding="utf-8")))


def _read_prompt_map(target_prompt: object) -> dict[str, str]:
    names = target_prompt.names()
    prompt_map: dict[str, str] = {}
    for name in names:
        source = target_prompt.describe_source(name)
        prompt_map[name] = Path(source).read_text(encoding="utf-8") if source != "<callback>" else source
    return prompt_map


def write_input_snapshot(config: PipelineConfig, target_prompt: object, output_dir: Path) -> InputMetadata:
    prompt_map = _read_prompt_map(target_prompt)
    metadata = InputMetadata(
        config_digest=canonical_sha256(config.raw),
        train_dataset_digest=_canonical_file_digest(config.pipeline.datasets.train_path),
        validation_dataset_digest=_canonical_file_digest(config.pipeline.datasets.validation_path),
        prompt_digest=canonical_sha256(prompt_map),
        snapshot_path=output_dir / "input.snapshot.json",
    )
    _write_json(
        metadata.snapshot_path,
        {
            "config": sanitize_config(config.raw),
            "digests": metadata.model_dump(mode="json", exclude={"snapshot_path"}),
        },
    )
    return metadata


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _sdk_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True, timeout=2
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def write_environment_snapshot(mode: Literal["fake", "trace", "live"], seed: int, output_dir: Path) -> Path:
    from trpc_agent_sdk.version import __version__

    dependencies = {name: version for name in ("pydantic", "pytest") if (version := _package_version(name)) is not None}
    payload = {
        "dependencies": dependencies,
        "mode": mode,
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "sdk_commit": _sdk_commit(),
        "sdk_version": __version__,
        "seed": seed,
    }
    return _write_json(output_dir / "environment.snapshot.json", payload)
