#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from typing import Any

from langchain_tavily import TavilySearch


# =============================================================================
# 1. LangChain Tavily tool
# 参考文档: https://python.langchain.com/docs/integrations/tools/tavily_search/
# =============================================================================
async def tavily_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """Use Tavily to search for the given query and return the results.

    Environment variables:
        - TAVILY_API_KEY: Tavily API Key (required).

    Args:
        query: The query text to search for
        max_results: The maximum number of results to return

    Returns:
        A dictionary containing the original search results.
    """

    try:
        tool = TavilySearch(max_results=max_results)
        res = await tool.ainvoke(query)
        # Compatible with different return structures
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
