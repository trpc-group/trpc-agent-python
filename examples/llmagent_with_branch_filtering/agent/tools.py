# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Tools for the agent. """


def check_server_status(service_name: str) -> str:
    """Check the status of a server or service."""
    status_map = {
        "api_server": "✓ API Server: Running (200ms response time)",
        "web_server": "✓ Web Server: Running (150ms response time)",
        "database": "⚠ Database: High CPU usage (85%), experiencing slowdowns",
    }
    return status_map.get(service_name, "✗ Service not found")


def diagnose_database_issue(symptom: str) -> str:
    """Deep diagnosis of database issues."""
    if "slow" in symptom.lower() or "cpu" in symptom.lower():
        return ("Database diagnosis: Found 23 slow queries on orders table. "
                "Recommendation: Add composite index on (user_id, created_at) columns to improve performance.")
    elif "connection" in symptom.lower():
        return ("Database diagnosis: Connection pool exhausted. Current: 100/100. "
                "Recommendation: Increase pool size to 200.")
    return "Database diagnosis: No obvious issues detected. Please provide more details."


def lookup_invoice(customer_id: str) -> str:
    """Look up customer invoice information."""
    return (f"Invoice for customer {customer_id}: $299.99 "
            f"(due date: 2025-12-15, status: PAID, payment method: Credit Card ending in 4242)")
