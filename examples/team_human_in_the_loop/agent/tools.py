# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Tools for the HITL team agents """

import uuid


def search_info(topic: str) -> str:
    """Search for information on a topic.

    Args:
        topic: The topic to search for.

    Returns:
        Information about the topic.
    """
    return f"Information about '{topic}': This is an important research area with many recent developments."


async def request_approval(content: str, reason: str) -> dict:
    """Request human approval before proceeding.

    This is a long-running function that requires human intervention.

    Args:
        content: The content that needs approval
        reason: Why approval is needed

    Returns:
        A dictionary indicating pending approval status
    """
    return {
        "status": "pending",
        "content": content,
        "reason": reason,
        "approval_id": str(uuid.uuid4()),
    }
