# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Tools for team members. """

import asyncio


async def search_web(query: str) -> dict:
    """Search the web for information.

    This function simulates a slow web search (3 seconds) to demonstrate
    cancellation during member tool execution.

    Args:
        query: Search query string

    Returns:
        Dictionary with search results
    """
    print(f"[Researcher Tool: searching for '{query}'...]", flush=True)
    await asyncio.sleep(3)

    # Simulate search results
    results = {
        "query":
        query,
        "results": [
            {
                "title": f"About {query} - Overview",
                "snippet": f"Comprehensive information about {query}...",
            },
            {
                "title": f"Latest developments in {query}",
                "snippet": f"Recent news and updates on {query}...",
            },
        ],
        "total_results":
        2
    }

    print(f"[Researcher Tool: search completed for '{query}']", flush=True)
    return results


async def check_grammar(text: str) -> dict:
    """Check grammar and improve writing quality.

    This function simulates a slow grammar check (2 seconds) to demonstrate
    cancellation during member tool execution.

    Args:
        text: Text to check for grammar issues

    Returns:
        Dictionary with grammar check results
    """
    print(f"[Writer Tool: checking grammar for {len(text)} characters...]", flush=True)
    await asyncio.sleep(2)

    # Simulate grammar check results
    result = {
        "original_length":
        len(text),
        "issues_found":
        2,
        "suggestions":
        ["Consider using active voice instead of passive voice", "Add more transitional phrases for better flow"],
        "improved_text":
        text  # In real scenario, would return improved text
    }

    print(f"[Writer Tool: grammar check completed]", flush=True)
    return result


async def get_current_date() -> str:
    """Get the current date.

    This is a quick tool used by the team leader.

    Returns:
        Current date string
    """
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d")
