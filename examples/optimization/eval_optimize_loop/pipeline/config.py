from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from .models import PipelineSettings, StrictModel


class PipelineConfig(StrictModel):
    raw: dict[str, Any]
    pipeline: PipelineSettings
    config_path: Path


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def sanitize_config(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: "***REDACTED***" if key.lower() in {"api_key", "authorization", "cookie", "token"} else sanitize_config(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_config(item) for item in value]
    return value


def load_pipeline_config(config_path: Path, *, mode: Literal["fake"]) -> PipelineConfig:
    if mode != "fake":
        raise ValueError("Phase 1 and 2 implement only --mode fake")
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if "pipeline" not in raw:
        raise ValueError("optimizer config requires a pipeline section")
    return PipelineConfig(raw=raw, pipeline=PipelineSettings.model_validate(raw["pipeline"]), config_path=config_path)
