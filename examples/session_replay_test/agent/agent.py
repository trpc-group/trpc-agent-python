from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel, OpenAIModel
from trpc_agent_sdk.tools import FunctionTool
from .config import get_model_config
from .tools import list_available_backends, replay_conversation, compare_replays

INSTRUCTION = """You are a session/memory consistency testing assistant.
Help users replay conversations across different session and memory backends,
then compare the results for consistency.

Use list_available_backends to discover available backends.
Use replay_conversation to run replays.
Use compare_replays to check consistency across backends."""


def _create_model() -> LLMModel:
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def create_agent() -> LlmAgent:
    return LlmAgent(
        name="session_replay_tester",
        description="Session/Memory multi-backend replay consistency tester.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[
            FunctionTool(list_available_backends),
            FunctionTool(replay_conversation),
            FunctionTool(compare_replays),
        ],
    )
