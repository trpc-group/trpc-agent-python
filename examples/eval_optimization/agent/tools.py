"""Evaluation and prompt optimization pipeline tools.

Provides:
- eval_cases registry with expected outputs
- run_eval to execute test cases and score results
- optimize_prompt to iteratively improve prompts based on eval regression
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class EvalCase:
    id: str
    input: str
    expected_keywords: list[str] = field(default_factory=list)
    max_tokens: int = 500
    temperature: float = 0.3


@dataclass
class EvalResult:
    case_id: str
    passed: bool
    response: str
    score: float  # 0.0 - 1.0
    missing_keywords: list[str] = field(default_factory=list)
    duration_ms: float = 0.0


@dataclass
class PromptVariant:
    version: int
    prompt: str
    avg_score: float = 0.0
    eval_count: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


# Built-in eval cases — extend by adding more
BUILTIN_CASES = [
    EvalCase(id="greet", input="Say hello in one sentence.",
             expected_keywords=["hello", "hi"], max_tokens=50),
    EvalCase(id="math", input="What is 2+2? Answer with just the number.",
             expected_keywords=["4"], max_tokens=20),
    EvalCase(id="code", input="Write a Python function that returns the sum of two numbers.",
             expected_keywords=["def", "return", "+"], max_tokens=200),
]


def list_eval_cases() -> list[dict]:
    """List all registered evaluation test cases."""
    return [{"id": c.id, "input": c.input, "keywords": c.expected_keywords}
            for c in BUILTIN_CASES]


def score_response(response: str, expected_keywords: list[str]) -> dict:
    """Score a response against expected keywords. Returns {score, missing}."""
    lower = response.lower()
    missing = [k for k in expected_keywords if k.lower() not in lower]
    hit = len(expected_keywords) - len(missing)
    score = hit / len(expected_keywords) if expected_keywords else 1.0
    return {"score": round(score, 2), "missing": missing, "hits": hit}


def optimize_prompt(current_prompt: str, eval_scores: list[float],
                    failure_analysis: str = "") -> dict:
    """Analyze eval scores and suggest prompt optimization.

    Returns: {"version": int, "suggested_prompt": str, "analysis": str}
    """
    avg = sum(eval_scores) / len(eval_scores) if eval_scores else 0.0

    suggestions = []
    if avg < 0.5:
        suggestions.append("Add explicit formatting instructions.")
    if "missing" in failure_analysis.lower():
        suggestions.append("Include required output keywords in the prompt.")
    if not suggestions:
        suggestions.append("Prompt performing well. Minor tuning may help.")

    version = int(time.time())
    optimized = current_prompt.strip()
    if suggestions:
        optimized += "\n\nAdditional instructions:\n- " + "\n- ".join(suggestions)

    return {
        "version": version,
        "avg_score": round(avg, 2),
        "suggestions": suggestions,
        "suggested_prompt": optimized,
    }
