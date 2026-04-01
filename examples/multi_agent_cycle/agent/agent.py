# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""
This is an example of a Cycle Agent for content creation improvement.

Main Process: Content Creation → Quality Assessment → Content Improvement → Re-assessment... until the requirements are met.

In this process, the CycleAgent achieves iterative improvement through output_key and the exit tool.
"""

from trpc_agent_sdk.agents import CycleAgent
from trpc_agent_sdk.agents import InvocationContext
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def exit_refinement_loop(tool_context: InvocationContext):
    """Tool function to stop the content improvement cycle

    This is the core exit mechanism of a Cycle Agent:
    Actively exit the loop by setting tool_context.actions.escalate = True
    This is a deterministic exit method that does not rely on LLM judgment.
    It provides dual protection together with max_iterations to prevent infinite loops.
    """
    print("  ✅ Content evaluator: Content meets quality standards, exiting loop")
    tool_context.actions.escalate = True  # Key: Set the escalate flag to exit the loop
    return {"status": "content_approved", "message": "Content quality is satisfactory"}


def create_agent():
    """Create a Cycle Agent for Content Improvement"""

    model = _create_model()

    # Content Creation Agent
    content_writer = LlmAgent(
        name="content_writer",
        model=model,
        description="Create and refine content based on requirements",
        instruction="""Create high-quality content based on the user's request.

If this is the first iteration, create original content.
If there's existing content with feedback, improve it based on the suggestions:

Existing content: {current_content}
Feedback: {feedback}

Focus on:
- Clear and engaging writing
- Proper structure and flow
- Accuracy and completeness
- Professional tone

Output only the improved content.""",
        output_key="current_content",  # Save the current content to the state variable
    )

    # Content Evaluation Agent
    content_evaluator = LlmAgent(
        name="content_evaluator",
        model=model,
        description="Evaluate content quality and decide if refinement is needed",
        instruction="""Evaluate the following content for quality:

{current_content}

Assessment criteria:
- Clarity and readability (score 1-10)
- Structure and organization (score 1-10)
- Completeness and accuracy (score 1-10)
- Professional tone (score 1-10)

If ALL scores are 8 or above, call the exit_refinement_loop tool immediately.
If any score is below 8, provide specific feedback for improvement but do NOT call the tool.

Format your response as:
Clarity: X/10
Structure: X/10
Completeness: X/10
Tone: X/10

Feedback: [specific suggestions for improvement if needed]""",
        output_key="feedback",  # Save the feedback to the state variable
        tools=[FunctionTool(exit_refinement_loop)],
    )

    # Create a Cycle Agent
    return CycleAgent(
        name="content_refinement_loop",
        description="Iterative content refinement: write → evaluate → improve",
        max_iterations=5,  # Maximum number of iterations to prevent infinite loops
        sub_agents=[content_writer, content_evaluator],
    )


root_agent = create_agent()
