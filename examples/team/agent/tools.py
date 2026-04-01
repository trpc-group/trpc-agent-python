# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
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
        "可再生能源": "研究发现：2024年全球可再生能源占比达30%，太阳能成本下降89%，风电装机增长15%。",
        "AI":
        "Latest AI trends: Large language models, multimodal AI, and AI agent technologies are rapidly developing. Applications span healthcare, finance, education, and more.",
        "人工智能": "最新AI趋势：大语言模型、多模态AI和AI智能体技术正在快速发展。应用覆盖医疗、金融、教育等领域。",
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
