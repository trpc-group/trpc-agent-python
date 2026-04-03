# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Tools for the hierarchical team agents """


async def design_api(feature: str) -> str:
    """Design API endpoints for a given feature.

    Args:
        feature: The feature to design API for.

    Returns:
        API design specification.
    """
    return f"API Design for '{feature}': GET /api/{feature.lower().replace(' ', '_')}, POST /api/{feature.lower().replace(' ', '_')}, with JWT authentication."


async def design_ui(feature: str) -> str:
    """Design UI components for a given feature.

    Args:
        feature: The feature to design UI for.

    Returns:
        UI component design specification.
    """
    return f"UI Design for '{feature}': React components with Material-UI, responsive layout, form validation included."


async def format_docs(content: str) -> str:
    """Format and structure documentation content.

    Args:
        content: The content to format into documentation.

    Returns:
        Formatted documentation structure.
    """
    word_count = len(content.split())
    return f"Documentation formatted: {word_count} words, Markdown structure applied, API references linked."
