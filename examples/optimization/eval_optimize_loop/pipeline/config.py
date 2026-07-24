from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Literal

from trpc_agent_sdk.evaluation import OptimizeConfigFile

from .models import DatasetSettings, PipelineSettings, StrictModel


LIVE_REQUIRED_ENV = ("TRPC_AGENT_API_KEY", "TRPC_AGENT_BASE_URL", "TRPC_AGENT_MODEL_NAME")


class PipelineConfig(StrictModel):
    raw: dict[str, Any]
    pipeline: PipelineSettings
    sdk_config: OptimizeConfigFile | None = None
    config_path: Path


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _is_sensitive_key(key: object) -> bool:
    normalized = "".join(character for character in str(key).lower() if character.isalnum())
    return (
        normalized in {"authorization", "cookie", "token", "key"}
        or normalized.endswith("key")
        or any(marker in normalized for marker in ("token", "secret", "password", "credential"))
    )


def sanitize_config(value: object) -> object:
    if isinstance(value, dict):
        return {key: "***REDACTED***" if _is_sensitive_key(key) else sanitize_config(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_config(item) for item in value]
    return value


def _resolved_dataset_settings(pipeline_payload: dict[str, Any], config_path: Path) -> DatasetSettings:
    settings = DatasetSettings.model_validate(pipeline_payload.get("datasets", {}))
    base_dir = config_path.parent
    return DatasetSettings(
        train_path=(base_dir / settings.train_path).resolve() if not settings.train_path.is_absolute() else settings.train_path.resolve(),
        validation_path=(base_dir / settings.validation_path).resolve()
        if not settings.validation_path.is_absolute()
        else settings.validation_path.resolve(),
    )


def _load_eval_ids(path: Path, *, split: str) -> set[str]:
    if not path.is_file():
        raise ValueError(f"{split} dataset does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases = payload["eval_cases"]
        ids = [case["eval_id"] for case in cases]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{split} dataset is not a valid evalset: {path}") from exc
    if len(ids) != len(set(ids)):
        raise ValueError(f"{split} dataset has duplicate eval_id values")
    return set(ids)


def _configured_metric_names(raw: dict[str, Any], sdk_config: OptimizeConfigFile | None) -> set[str]:
    if sdk_config is not None:
        return {metric.metric_name for metric in sdk_config.evaluate.get_eval_metrics()}
    evaluate = raw.get("evaluate", {})
    if not isinstance(evaluate, dict):
        return set()
    metrics = evaluate.get("metrics") or []
    names = {
        metric.get("metric_name") or metric.get("metricName")
        for metric in metrics
        if isinstance(metric, dict) and (metric.get("metric_name") or metric.get("metricName"))
    }
    criteria = evaluate.get("criteria") or {}
    return names | set(criteria) if isinstance(criteria, dict) else names


def _validate_pipeline_settings(
    raw: dict[str, Any], pipeline: PipelineSettings, sdk_config: OptimizeConfigFile | None, *, validate_datasets: bool
) -> None:
    train_path = pipeline.datasets.train_path
    validation_path = pipeline.datasets.validation_path
    if validate_datasets:
        if train_path == validation_path:
            raise ValueError("train and validation dataset paths must be different")
        train_ids = _load_eval_ids(train_path, split="train")
        validation_ids = _load_eval_ids(validation_path, split="validation")
        shared_ids = train_ids & validation_ids
        if shared_ids:
            raise ValueError(f"train and validation datasets have shared eval_id values: {sorted(shared_ids)}")
        missing_critical = set(pipeline.gate.critical_case_ids) - validation_ids
        if missing_critical:
            raise ValueError(f"critical validation case ids are missing: {sorted(missing_critical)}")
    if not all(math.isfinite(weight) for weight in pipeline.metric_weights.values()):
        raise ValueError("metric weights must be finite")
    if any(weight < 0 for weight in pipeline.metric_weights.values()):
        raise ValueError("metric weights must be non-negative")
    if not any(weight > 0 for weight in pipeline.metric_weights.values()):
        raise ValueError("metric weights must include at least one positive weight")
    unknown_floors = set(pipeline.metric_floors) - _configured_metric_names(raw, sdk_config)
    if unknown_floors:
        raise ValueError(f"metric floors reference unknown metric(s): {sorted(unknown_floors)}")


def load_pipeline_config(config_path: Path, *, mode: Literal["fake", "trace", "live"]) -> PipelineConfig:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if "pipeline" not in raw:
        raise ValueError("optimizer config requires a pipeline section")
    if mode == "live":
        missing = [name for name in LIVE_REQUIRED_ENV if not os.environ.get(name)]
        if missing:
            raise ValueError("live mode requires environment variables: " + ", ".join(missing))
    if mode not in {"fake", "trace", "live"}:
        raise ValueError(f"unsupported mode: {mode}")
    if "evaluate" in raw and "optimize" in raw:
        sdk_config = OptimizeConfigFile.model_validate({"evaluate": raw["evaluate"], "optimize": raw["optimize"]})
    elif mode == "live":
        raise ValueError("live mode requires both evaluate and optimize sections")
    else:
        sdk_config = None
    pipeline_payload = dict(raw["pipeline"])
    pipeline_payload["datasets"] = _resolved_dataset_settings(pipeline_payload, config_path).model_dump()
    pipeline = PipelineSettings.model_validate(pipeline_payload)
    _validate_pipeline_settings(
        raw,
        pipeline,
        sdk_config,
        validate_datasets=mode != "fake" or "datasets" in raw["pipeline"],
    )
    return PipelineConfig(raw=raw, pipeline=pipeline, sdk_config=sdk_config, config_path=config_path)
