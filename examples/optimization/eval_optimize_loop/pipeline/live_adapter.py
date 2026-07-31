"""Single importable callback contract for live evaluation and optimization."""

from __future__ import annotations

import importlib
import hashlib
import inspect
import marshal
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def load_callback(spec: str) -> Any:
    """Load one MODULE:FUNCTION callback and reject ambiguous specifications."""

    if spec.count(":") != 1:
        raise ValueError("live callback must use MODULE:FUNCTION syntax")
    module_name, function_name = spec.split(":", 1)
    if not module_name or not function_name or "." in function_name:
        raise ValueError("live callback must use MODULE:FUNCTION syntax")
    callback = getattr(importlib.import_module(module_name), function_name)
    if not inspect.iscoroutinefunction(callback):
        raise TypeError("live callback must be an async function")
    return callback


def _source_identity(callback: Any) -> tuple[Path, str, str]:
    unwrapped = inspect.unwrap(callback)
    code = getattr(unwrapped, "__code__", None)
    if code is None:
        raise TypeError("live callback must expose Python function code")
    source = inspect.getsourcefile(unwrapped) or inspect.getfile(unwrapped)
    if not source:
        raise ValueError("live callback source is unavailable")
    source_path = Path(source).resolve()
    if not source_path.is_file():
        raise ValueError("live callback source is not a file")
    return (
        source_path,
        hashlib.sha256(source_path.read_bytes()).hexdigest(),
        hashlib.sha256(marshal.dumps(code)).hexdigest(),
    )


def load_verified_callback(
    spec: str,
    *,
    expected_source_path: str,
    expected_source_sha256: str,
    expected_callable_sha256: str,
) -> Any:
    """Load a worker callback and prove it is the preflight-resolved source."""

    callback = load_callback(spec)
    source_path, source_sha256, callable_sha256 = _source_identity(callback)
    if source_path != Path(expected_source_path).resolve():
        raise ValueError("worker callback source path differs from preflight")
    if source_sha256 != expected_source_sha256:
        raise ValueError("worker callback source hash differs from preflight")
    if callable_sha256 != expected_callable_sha256:
        raise ValueError("worker callback code differs from preflight")
    return callback


@dataclass(frozen=True)
class LiveAdapterSpec:
    """Bind the parent callable, worker import path and tracked source as one fact."""

    import_path: str
    callback: Any
    source_path: Path
    source_sha256: str
    callable_sha256: str

    @classmethod
    def resolve(cls, import_path: str, callback: Any) -> "LiveAdapterSpec":
        loaded = load_callback(import_path)
        if inspect.unwrap(loaded) is not inspect.unwrap(callback):
            raise ValueError("call_agent and callback_spec must resolve to the same function")
        source_path, source_sha256, callable_sha256 = _source_identity(loaded)
        return cls(
            import_path=import_path,
            callback=loaded,
            source_path=source_path,
            source_sha256=source_sha256,
            callable_sha256=callable_sha256,
        )
