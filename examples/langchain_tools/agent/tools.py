# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Tools for the agent. """

from typing import Any

from langchain_tavily import TavilySearch


async def tavily_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """Search the web using Tavily and return results.

    Requires the TAVILY_API_KEY environment variable to be set.

    Args:
        query: The search query text.
        max_results: Maximum number of results to return.

    Returns:
        A dict containing search status and results.
    """
    try:
        tool = TavilySearch(max_results=max_results)
        res = await tool.ainvoke(query)

        if isinstance(res, dict) and "results" in res:
            items = res["results"]
        elif isinstance(res, list):
            items = res
        else:
            items = []

        return {
            "status": "success",
            "query": query,
            "result_count": len(items),
            "results": items,
        }
    except Exception as e:  # pylint: disable=broad-except
        return {"status": "error", "error_message": str(e)}
