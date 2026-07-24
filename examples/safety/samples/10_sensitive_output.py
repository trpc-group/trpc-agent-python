"""Sample 10 — Sensitive information output via environment dump.

Expected decision: NEEDS_HUMAN_REVIEW
Triggers: SEC-002 (environment variable leakage — print(os.environ))
"""

import os


def dump_environment():
    """Print all environment variables — may expose secrets!"""
    print(os.environ)


def log_sensitive_vars():
    """Log specific sensitive environment variables."""
    print(f"Database URL: {os.environ.get('DATABASE_URL', 'N/A')}")
    print(f"API Key: {os.environ.get('API_KEY', 'N/A')}")


if __name__ == "__main__":
    dump_environment()
    log_sensitive_vars()
