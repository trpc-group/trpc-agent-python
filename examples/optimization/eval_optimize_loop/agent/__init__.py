"""Agent under evaluation — a simple calculator agent for optimization testing.

This agent serves as the optimization target: its system prompt is what
gets optimized by the pipeline to improve evaluation scores.
"""

from .agent import create_agent, run_agent
from .config import AgentConfig
from .prompts import BASELINE_SYSTEM_PROMPT

__all__ = ["create_agent", "run_agent", "AgentConfig", "BASELINE_SYSTEM_PROMPT"]
