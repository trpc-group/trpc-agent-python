from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel, OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import review_code, save_review


def _create_model() -> LLMModel:
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def create_agent() -> LlmAgent:
    return LlmAgent(
        name="code_reviewer",
        description="An AI-powered code review agent backed by Hy3.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[
            FunctionTool(review_code),
            FunctionTool(save_review),
        ],
    )
