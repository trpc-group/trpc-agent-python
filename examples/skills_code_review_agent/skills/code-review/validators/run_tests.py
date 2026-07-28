#!/usr/bin/env python3
"""Validator task contract for a sandbox-provided test command."""

import json

print(json.dumps({"validator": "run_tests", "command": ["pytest"], "status": "pending"}))
