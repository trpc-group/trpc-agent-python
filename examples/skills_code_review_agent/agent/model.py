# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""FakeReviewModel — the deterministic, no-API-key model behind ``--dry-run``.

Issue #92 requires a dry-run / fake-model mode so the parse -> sandbox -> persist chain is testable
without a key (acceptance 6). It is a *mode*, not the default: ``config.get_model`` returns a real
model unless ``--dry-run`` is passed.

Crucially, this model walks the **same four steps a real model must walk** —
``stage_review_input`` -> ``skill_load`` -> ``skill_run`` -> ``finalize_review`` — so a dry run
exercises the real Skills path (staging, sandbox execution, the guard Filter, persistence) instead
of a shortcut around it. The only thing it fakes is the reasoning.
"""
from __future__ import annotations

from typing import Any, AsyncGenerator, List

from trpc_agent_sdk.models import LlmRequest, LlmResponse, LLMModel
from trpc_agent_sdk.types import Content, FunctionCall, Part

SKILL_NAME = "code-review"

_STAGE = "stage_review_input"
_LOAD = "skill_load"
_RUN = "skill_run"
_FINALIZE = "finalize_review"


def _tool_responses(request: LlmRequest) -> list[tuple[str, Any]]:
    """Every tool result so far, oldest first, as (tool_name, response).

    The whole history is needed, not just the newest entry: ``skill_run``'s arguments come from the
    ``stage_review_input`` response several turns earlier.
    """
    out: list[tuple[str, Any]] = []
    for content in request.contents or []:
        for part in content.parts or []:
            fr = getattr(part, "function_response", None)
            if fr is not None:
                out.append((fr.name, fr.response))
    return out


def _first_response(responses: list[tuple[str, Any]], name: str) -> dict:
    for tool_name, payload in responses:
        if tool_name == name and isinstance(payload, dict):
            return payload
    return {}


def _initial_diff(request: LlmRequest) -> str:
    """The diff under review: the first user content carrying text rather than a tool result."""
    for content in request.contents or []:
        if (content.role or "user") != "user":
            continue
        if any(getattr(p, "function_response", None) is not None for p in content.parts or []):
            continue  # a tool result, not the user's message
        for part in content.parts or []:
            if part is not None and part.text:
                return part.text
    return ""


def _failure(payload: Any) -> str:
    """A tool error as a message, or "" when the call succeeded.

    Failures arrive as ordinary function responses carrying ``status``/``error``. They must end the
    run: re-issuing the same call would loop forever.
    """
    if not isinstance(payload, dict):
        return ""
    if payload.get("status") == "failed" or payload.get("error"):
        return str(payload.get("message") or payload.get("error") or "tool call failed")
    return ""


def _findings_json(run_result: dict) -> str:
    """Pull ``out/findings.json`` out of a ``skill_run`` result."""
    primary = run_result.get("primary_output") or {}
    if isinstance(primary, dict) and primary.get("content"):
        return primary["content"]
    for f in run_result.get("output_files") or []:
        if isinstance(f, dict) and str(f.get("name", "")).endswith("findings.json"):
            return f.get("content") or ""
    return ""


def _text(message: str) -> LlmResponse:
    return LlmResponse(content=Content(role="model", parts=[Part(text=message)]))


def _call(call_id: str, name: str, args: dict) -> LlmResponse:
    return LlmResponse(
        content=Content(role="model", parts=[Part(function_call=FunctionCall(id=call_id, name=name, args=args))]))


class FakeReviewModel(LLMModel):
    """Drives the four-step skill flow deterministically. Records its calls so tests can assert them."""

    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"fake-review.*"]

    def validate_request(self, request: LlmRequest) -> None:  # no external validation needed
        return None

    @property
    def seen_calls(self) -> list[str]:
        return list(getattr(self, "_calls", []))

    def _record(self, name: str) -> None:
        if not hasattr(self, "_calls"):
            self._calls: list[str] = []
        self._calls.append(name)

    async def _generate_async_impl(self,
                                   request: LlmRequest,
                                   stream: bool = False,
                                   ctx: object = None) -> AsyncGenerator[LlmResponse, None]:
        responses = _tool_responses(request)

        if not responses:
            self._record(_STAGE)
            yield _call("cr-1", _STAGE, {"diff_text": _initial_diff(request)})
            return

        # Dispatch on the tool NAME of the newest result — never on its payload. The framework
        # rewrites skill_load's response in place with the SKILL.md body on every later request, so
        # payload shape is not a reliable signal of which step we are on.
        last_name, last_payload = responses[-1]

        failed = _failure(last_payload)
        if failed:
            yield _text(f"Review aborted: {last_name} failed — {failed}")
            return

        if last_name == _STAGE:
            self._record(_LOAD)
            yield _call("cr-2", _LOAD, {"skill_name": SKILL_NAME, "include_all_docs": True})
            return

        if last_name == _LOAD:
            hint = _first_response(responses, _STAGE).get("skill_run_hint") or {}
            if not hint:
                yield _text("Review aborted: stage_review_input returned no skill_run hint.")
                return
            self._record(_RUN)
            yield _call("cr-3", _RUN, dict(hint))
            return

        if last_name == _RUN:
            run_result = last_payload if isinstance(last_payload, dict) else {}
            findings_json = _findings_json(run_result)
            if not findings_json:
                yield _text("Review aborted: the skill produced no out/findings.json "
                            f"(exit_code={run_result.get('exit_code')}, "
                            f"timed_out={run_result.get('timed_out')}).")
                return
            self._record(_FINALIZE)
            yield _call(
                "cr-4", _FINALIZE, {
                    "task_id": _first_response(responses, _STAGE).get("task_id", ""),
                    "findings_json": findings_json,
                    "exit_code": int(run_result.get("exit_code", 0) or 0),
                    "duration_ms": int(run_result.get("duration_ms", 0) or 0),
                })
            return

        if last_name == _FINALIZE:
            report = last_payload if isinstance(last_payload, dict) else {}
            summary = report.get("summary", {}) or {}
            sev = report.get("severity", {}) or {}
            yield _text(f"Review complete (task {report.get('task_id', '?')}). "
                        f"{summary.get('total', 0)} active finding(s) "
                        f"[critical={sev.get('critical', 0)}, high={sev.get('high', 0)}, "
                        f"medium={sev.get('medium', 0)}, low={sev.get('low', 0)}], "
                        f"{report.get('needs_human_review', 0)} for human review. "
                        f"See review_report.json for details.")
            return

        yield _text(f"Review stopped: unexpected tool result from {last_name}.")
