from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel, OpenAIModel
from trpc_agent_sdk.tools import FunctionTool
from .config import get_model_config
from .tools import list_eval_cases, score_response, optimize_prompt

INSTRUCTION = """You are an evaluation and prompt optimization assistant.
Run eval test cases with score_response, analyze regressions, and use
optimize_prompt to iteratively improve prompts based on scores.

When a prompt fails tests, analyze why and suggest concrete improvements.
Track baseline scores and compare after each optimization iteration."""


def _create_model() -> LLMModel:
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def create_agent() -> LlmAgent:
    return LlmAgent(
        name="eval_optimizer",
        description="Evaluation + Optimization auto-regression pipeline.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[
            FunctionTool(list_eval_cases),
            FunctionTool(score_response),
            FunctionTool(optimize_prompt),
        ],
    )
