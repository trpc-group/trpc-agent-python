"""Simple calculator agent — the target of prompt optimization.

This agent answers math questions. Its system prompt is what gets
optimized by the pipeline. In fake mode, it returns deterministic
responses based on the input question.
"""

import hashlib
from typing import Any

from .config import AgentConfig
from .prompts import BASELINE_SYSTEM_PROMPT


def create_agent(config: AgentConfig | None = None) -> dict[str, Any]:
    """Create an agent instance with the given configuration.

    In fake mode, returns a simple dict representing the agent.
    In live mode, would create an LlmAgent instance.

    Args:
        config: Agent configuration. Uses defaults if None.

    Returns:
        Agent representation (dict in fake mode, LlmAgent in live mode).
    """
    if config is None:
        config = AgentConfig()
    return {
        "name": "calculator_agent",
        "config": config,
        "system_prompt": BASELINE_SYSTEM_PROMPT,
        "tools": config.available_tools,
    }


def run_agent(
    question: str,
    agent: dict | None = None,
    config: AgentConfig | None = None,
) -> dict[str, Any]:
    """Run the agent on a question.

    In fake mode, returns deterministic results based on the question hash.
    In live mode, would invoke the LLM agent.

    Args:
        question: The user's question.
        agent: Pre-created agent (optional).
        config: Agent configuration (optional).

    Returns:
        Dict with 'final_response', 'tool_calls', 'intermediate_steps'.
    """
    if config is None:
        config = AgentConfig()

    if config.model_name == "fake":
        return _fake_run(question, config)
    else:
        return _live_run(question, agent, config)


def _fake_run(question: str, config: AgentConfig) -> dict[str, Any]:
    """Deterministic fake agent execution.

    Uses a hash of the question to produce consistent, deterministic output.
    This enables trace mode evaluation where the "actual" conversation
    is pre-computed rather than requiring LLM inference.

    Args:
        question: The user's question.
        config: Agent configuration.

    Returns:
        Dict with final_response, tool_uses, tool_responses, etc.
    """
    qhash = hashlib.md5(question.encode()).hexdigest()
    hash_int = int(qhash[:8], 16)

    # Parse simple math from the question
    import re

    # Try to detect arithmetic operations
    response_text = ""
    tool_calls = []

    # Pattern: "number operator number"
    math_match = re.search(
        r'(-?\d+\.?\d*)\s*([+\-*/×÷])\s*(-?\d+\.?\d*)',
        question,
    )
    if math_match:
        a = float(math_match.group(1))
        op = math_match.group(2)
        b = float(math_match.group(3))

        if op == '+':
            result = a + b
        elif op == '-':
            result = a - b
        elif op in ('*', '×'):
            result = a * b
        elif op in ('/', '÷'):
            result = a / b if b != 0 else float('inf')
        else:
            result = 0.0

        # Simulate tool call
        tool_calls.append({
            "tool_name": "calculate",
            "arguments": {"a": a, "operator": op, "b": b},
            "result": result,
        })

        if config.step_by_step_reasoning:
            response_text = f"{a} {op} {b} = {result}"
        else:
            response_text = str(result)
    else:
        # Non-math question — use hash to produce deterministic response
        responses = [
            f"The answer is {hash_int % 100}.",
            f"Based on calculation: {hash_int % 1000}.",
            f"I computed: {(hash_int % 500) / 10}.",
        ]
        response_text = responses[hash_int % len(responses)]

    return {
        "final_response": response_text,
        "tool_uses": tool_calls,
        "tool_responses": [t.get("result") for t in tool_calls],
        "intermediate_steps": [],
    }


def _live_run(
    question: str,
    agent: dict | None,
    config: AgentConfig,
) -> dict[str, Any]:
    """Real agent execution via LLM.

    This path requires a configured model and API keys.
    Not implemented in this example — serves as an integration point.
    """
    return {
        "final_response": "",
        "tool_uses": [],
        "tool_responses": [],
        "intermediate_steps": [],
        "error": "Live mode not implemented — use fake mode for testing.",
    }
