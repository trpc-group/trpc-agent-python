# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Tools for the team agents """

from datetime import datetime


async def search_web(query: str) -> str:
    """Search the web for information on a given topic.

    Args:
        query: The search query to look up.

    Returns:
        Search results with relevant information.
    """
    # Simulated web search results
    search_results = {
        "renewable energy":
        "Research findings: In 2024, global renewable energy share reached 30%, solar costs dropped 89%, and wind capacity grew 15%.",
        "\u53ef\u518d\u751f\u80fd\u6e90":
        "Research findings: In 2024, global renewable energy share reached 30%, solar costs dropped 89%, and wind capacity grew 15%.",
        "AI":
        "Latest AI trends: Large language models, multimodal AI, and AI agent technologies are rapidly developing. Applications span healthcare, finance, education, and more.",
        "\u4eba\u5de5\u667a\u80fd":
        "Latest AI trends: Large language models, multimodal AI, and AI agent technologies are rapidly developing. Applications span healthcare, finance, education, and more.",
    }

    for key, result in search_results.items():
        if key.lower() in query.lower():
            return result
    return f"Search results for '{query}': This topic is evolving rapidly with increasing research and applications."


async def check_grammar(text: str) -> str:
    """Check the grammar and style of the given text.

    Args:
        text: The text to check for grammar and style issues.

    Returns:
        Grammar check results with suggestions.
    """
    word_count = len(text)
    return f"Grammar check completed: Text contains {word_count} characters. Overall quality: Good."


async def get_current_date() -> str:
    """Get the current date and time.

    Returns:
        Current date and time in ISO format.
    """
    return datetime.now().isoformat()
