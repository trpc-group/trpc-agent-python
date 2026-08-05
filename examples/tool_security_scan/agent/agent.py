from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel, OpenAIModel
from trpc_agent_sdk.tools import FunctionTool
from .config import get_model_config
from .tools import scan_tool_input, filter_tool_call

INSTRUCTION = """You are a tool security auditor. Before any tool executes:
1. Use scan_tool_input to check for security violations.
2. Use filter_tool_call to enforce policy (block/sanitize).
3. Report findings to the user clearly."""


def _create_model() -> LLMModel:
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def create_agent() -> LlmAgent:
    return LlmAgent(
        name="tool_security_scanner",
        description="Tool execution security scanning and filter monitoring.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[
            FunctionTool(scan_tool_input),
            FunctionTool(filter_tool_call),
        ],
    )
