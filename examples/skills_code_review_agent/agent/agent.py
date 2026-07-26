"""Native LlmAgent factory with Windows-safe, delayed SDK imports."""
from __future__ import annotations

import json
from typing import Any

from .prompts import INSTRUCTION

class OpenAIReviewModel:
    """OpenAI-compatible adapter backed by the repository's SDK model."""

    def __init__(self, api_key: str, base_url: str, model_name: str):
        self.api_key, self.base_url, self.model_name = api_key, base_url, model_name

    def create(self) -> Any:
        from trpc_agent_sdk.models import OpenAIModel
        return OpenAIModel(model_name=self.model_name, api_key=self.api_key, base_url=self.base_url)


def create_fake_model() -> Any:
    """Create a deterministic SDK ``LLMModel`` only when an SDK Runner is used."""
    from trpc_agent_sdk.models import LLMModel, LlmResponse
    from trpc_agent_sdk.types import Content, FunctionCall, Part
    from .parser import parse_unified_diff

    class FakeReviewModel(LLMModel):
        @classmethod
        def supported_models(cls) -> list[str]:
            return [r"code-review-fake"]

        def validate_request(self, request: Any) -> None:
            return None

        def __init__(self) -> None:
            super().__init__(model_name="code-review-fake")
            self.payload: dict[str, Any] = {}
            self.step = 0

        async def _generate_async_impl(self, request: Any, stream: bool = False, ctx: Any = None):
            if self.step == 0:
                for content in request.contents:
                    for part in content.parts:
                        if part.text:
                            try:
                                self.payload = json.loads(part.text)
                            except json.JSONDecodeError:
                                continue
                self.step += 1
                changed = parse_unified_diff(str(self.payload.get("diff", "")))
                finding = [] if not changed else [{
                    "severity": "warning", "category": "semantic_review", "file": changed[0].file,
                    "line": changed[0].line, "title": "Review changed behavior", "evidence": changed[0].content[:200],
                    "recommendation": "Confirm this change has a focused test.", "confidence": 0.4, "source": "fake_model",
                }]
                yield LlmResponse(content=Content(role="model", parts=[Part(function_call=FunctionCall(
                    name="save_review_report",
                    args={"task_id": self.payload.get("task_id", "dry-run"), "findings": finding,
                          "evidence": {"changed_lines": [{"file": item.file, "line": item.line, "content": item.content} for item in changed],
                                       "skill_runs": [], "filter_decisions": []},
                          "output_dir": self.payload.get("output_dir", "")},
                ))]))
                return
            yield LlmResponse(content=Content(role="model", parts=[Part(text="Review report saved.")]))

    return FakeReviewModel()


def create_agent(*, runtime: str = "docker", model: Any = None, workspace_inputs: list[dict[str, str]] | None = None) -> Any:
    """Create the SDK-native Agent; call only in a supported SDK runtime."""
    from trpc_agent_sdk.agents import LlmAgent
    from .tools import create_review_tools
    from .filter import before_model_audit, after_model_audit

    skill_tool_set, review_tools, repository = create_review_tools(runtime)
    agent = LlmAgent(
        name="code_review_agent",
        description="A policy-governed code review agent powered by loaded Skills.",
        model=model or create_fake_model(), instruction=INSTRUCTION,
        tools=[skill_tool_set, *review_tools], skill_repository=repository,
        filters_name=["code_review_agent_filter"],
        before_model_callback=before_model_audit, after_model_callback=after_model_audit,
    )
    # Host paths are execution-only data. Never put them in the model message,
    # session transcript, report, or audit payload.
    agent._code_review_workspace_inputs = list(workspace_inputs or [])
    return agent


async def create_agent_async(*, runtime: str = "docker", model: Any = None,
                             workspace_inputs: list[dict[str, str]] | None = None) -> Any:
    """Async variant used by Cube/E2B, whose SDK workspace starts asynchronously."""
    if runtime not in {"cube", "e2b"}:
        return create_agent(runtime=runtime, model=model, workspace_inputs=workspace_inputs)
    from trpc_agent_sdk.agents import LlmAgent
    from .tools import create_review_tools_async
    from .filter import before_model_audit, after_model_audit

    skill_tool_set, review_tools, repository = await create_review_tools_async(runtime)
    agent = LlmAgent(name="code_review_agent", description="A policy-governed code review agent powered by loaded Skills.",
                     model=model or create_fake_model(), instruction=INSTRUCTION, tools=[skill_tool_set, *review_tools],
                     skill_repository=repository, filters_name=["code_review_agent_filter"],
                     before_model_callback=before_model_audit, after_model_callback=after_model_audit)
    agent._code_review_workspace_inputs = list(workspace_inputs or [])
    return agent
