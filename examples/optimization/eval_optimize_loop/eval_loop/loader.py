"""Input loading helpers for the deterministic optimization example."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import OptimizerConfig
from .config import parse_optimizer_config
from .schemas import EvalCase


def read_json(path: str | Path) -> dict[str, Any]:
    resolved = Path(path)
    try:
        with resolved.open("r", encoding="utf-8") as file:
            payload = json.load(file, parse_constant=_reject_non_standard_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{resolved}: invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {resolved}")
    return payload


def _reject_non_standard_json_constant(constant: str) -> None:
    raise ValueError(f"non-standard JSON constant {constant!r}")


def load_eval_cases(path: str | Path, split: str | None = None) -> list[EvalCase]:
    payload = read_json(path)
    cases = payload.get("cases")
    effective_split = split or payload.get("split") or Path(path).name.split(".", 1)[0]
    if isinstance(cases, list):
        return [EvalCase.from_dict(case, str(effective_split)) for case in cases]

    sdk_cases = payload.get("eval_cases") or payload.get("evalCases")
    if isinstance(sdk_cases, list):
        _validate_sdk_evalset(payload, path)
        return [_eval_case_from_sdk_dict(case, str(effective_split)) for case in sdk_cases]

    raise ValueError(f"evalset {path} must contain a cases or evalCases list")


def load_optimizer_config(path: str | Path) -> OptimizerConfig:
    payload = read_json(path)
    return parse_optimizer_config(payload, path=path)


def load_prompt(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def stable_config_hash(config: dict[str, Any]) -> str:
    import hashlib

    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_sdk_evalset(payload: dict[str, Any], path: str | Path) -> None:
    try:
        from trpc_agent_sdk.evaluation._eval_set import EvalSet

        EvalSet.model_validate(payload)
    except Exception as exc:
        raise ValueError(f"evalset {path} is not a valid SDK EvalSet: {exc}") from exc


def _eval_case_from_sdk_dict(payload: dict[str, Any], split: str) -> EvalCase:
    case_id = payload.get("eval_id") or payload.get("evalId")
    if not case_id:
        raise ValueError(f"SDK eval case is missing evalId/eval_id: {payload!r}")

    session_input = payload.get("session_input") or payload.get("sessionInput") or {}
    state = session_input.get("state") if isinstance(session_input, dict) else {}
    state = state if isinstance(state, dict) else {}
    expectation = state.get("eval_optimize_expectation")
    if not isinstance(expectation, dict):
        expectation = _infer_expectation_from_sdk_case(payload)

    return EvalCase(
        case_id=str(case_id),
        split=split,
        input=_first_user_text(payload),
        expectation=dict(expectation),
        tags=[str(item) for item in state.get("eval_optimize_tags", [])],
        protected=bool(state.get("eval_optimize_protected", False)),
        simulated_outputs=dict(state.get("eval_optimize_simulated_outputs") or expectation.get("simulated_outputs") or {}),
        expected_failure_category=state.get("eval_optimize_expected_failure_category")
        or expectation.get("expected_failure_category"),
    )


def _infer_expectation_from_sdk_case(payload: dict[str, Any]) -> dict[str, Any]:
    expected = _first_final_response_text(payload)
    if expected:
        return {
            "type": "exact",
            "expected": expected,
            "expected_failure_category": "final_response_mismatch",
        }
    raise ValueError(
        "SDK eval case must put fake-mode metadata in sessionInput.state.eval_optimize_expectation "
        f"or provide a finalResponse that can be treated as an exact expectation: {payload!r}"
    )


def _first_user_text(payload: dict[str, Any]) -> str:
    for invocation in _conversation(payload):
        content = invocation.get("user_content") or invocation.get("userContent") or {}
        text = _content_text(content)
        if text:
            return text
    return ""


def _first_final_response_text(payload: dict[str, Any]) -> str:
    for invocation in _conversation(payload):
        content = invocation.get("final_response") or invocation.get("finalResponse") or {}
        text = _content_text(content)
        if text:
            return text
    return ""


def _conversation(payload: dict[str, Any]) -> list[dict[str, Any]]:
    conversation = payload.get("conversation") or []
    return [item for item in conversation if isinstance(item, dict)]


def _content_text(content: Any) -> str:
    if not isinstance(content, dict):
        return ""
    texts = []
    for part in content.get("parts") or []:
        if isinstance(part, dict) and part.get("text") is not None:
            texts.append(str(part["text"]))
    return "\n".join(texts)
