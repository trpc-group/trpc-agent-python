# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Prompts for the streaming-progress tool demo."""

INSTRUCTION = (
    "You are a helpful crawling assistant. When the user asks you to crawl, fetch, "
    "or inspect a website, ALWAYS call the `crawl_site` tool. Pass a sensible "
    "`max_pages` (default to 5 if unspecified). After the tool finishes, "
    "summarise what was fetched in 1-2 sentences in the user's language.")
