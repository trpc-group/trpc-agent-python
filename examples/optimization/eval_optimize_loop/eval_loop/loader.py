"""Input loading helpers for the deterministic optimization example."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .schemas import EvalCase


def read_json(path: str | Path) -> dict[str, Any]:
    resolved = Path(path)
    with resolved.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {resolved}")
    return payload


def load_eval_cases(path: str | Path, split: str | None = None) -> list[EvalCase]:
    payload = read_json(path)
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise ValueError(f"evalset {path} must contain a cases list")
    effective_split = split or payload.get("split") or Path(path).name.split(".", 1)[0]
    return [EvalCase.from_dict(case, str(effective_split)) for case in cases]


def load_optimizer_config(path: str | Path) -> dict[str, Any]:
    payload = read_json(path)
    if "gate" not in payload:
        raise ValueError(f"optimizer config {path} must contain a gate object")
    return payload


def load_prompt(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def stable_config_hash(config: dict[str, Any]) -> str:
    import hashlib

    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
