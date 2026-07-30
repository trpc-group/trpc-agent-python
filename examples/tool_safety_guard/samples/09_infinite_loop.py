#!/usr/bin/env python3
"""Sample 09 — RESOURCE ABUSE: an unbounded loop with no break.

Expected verdict: needs_human_review (AST005, medium).
"""

counter = 0
while True:
    counter += 1
