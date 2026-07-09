"""Sample 02 — Dangerous file deletion targeting system paths.

Expected decision: DENY
Triggers: FS-001 (forbidden path /etc/)
"""

import os


def cleanup_configs():
    """Delete system configuration files — extremely dangerous!"""
    os.remove("/etc/passwd")
    os.remove("/etc/shadow")
    os.remove("/etc/hosts")


if __name__ == "__main__":
    cleanup_configs()
