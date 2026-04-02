# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Tools for the agent. """

import time
import uuid


async def human_approval_required(task_description: str, details: dict) -> dict:
    """A long-running function that requires human approval.

    Args:
        task_description: Description of the task requiring approval
        details: Additional details about the task

    Returns:
        A dictionary indicating the task is pending human approval
    """
    return {
        "status": "pending_approval",
        "message": f"Task '{task_description}' requires human approval",
        "details": details,
        "approval_id": str(uuid.uuid4()),
        "timestamp": time.time(),
    }


async def check_system_critical_operation(operation: str, target: str) -> dict:
    """A long-running function for sub-agent that requires human approval for critical operations.

    Args:
        operation: The critical operation to perform (e.g., delete, restart, update)
        target: The target of the operation (e.g., server name, database name)

    Returns:
        A dictionary indicating the operation requires human approval
    """
    return {
        "status": "pending_approval",
        "message": f"Critical operation '{operation}' on '{target}' requires human approval",
        "operation": operation,
        "target": target,
        "approval_id": str(uuid.uuid4()),
        "timestamp": time.time(),
        "risk_level": "high",
    }
