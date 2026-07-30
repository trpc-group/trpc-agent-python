#!/usr/bin/env python3
"""Sample 03 — SENSITIVE INFO: reading an SSH private key.

Expected verdict: deny (CR001 + AST003, critical).
"""

with open("/home/user/.ssh/id_rsa", "r", encoding="utf-8") as key_file:
    private_key = key_file.read()
    print(private_key)
