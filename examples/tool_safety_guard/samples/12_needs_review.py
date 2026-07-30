#!/usr/bin/env python3
"""Sample 12 — UNCERTAIN, NEEDS REVIEW: dynamically constructed command.

The command string is built from a variable, so it cannot be judged safe or
unsafe statically. Rather than allow it outright, the guard routes it to a
human.

Expected verdict: needs_human_review (AST001 high spawn + AST008 medium dynamic
command). Note ``os.system`` itself is high, so decision fusion actually denies;
set ``block_on_review`` semantics aside — this sample shows dynamic-arg
detection producing the AST008 medium hit alongside the spawn.
"""

import os
import sys

user_supplied = sys.argv[1] if len(sys.argv) > 1 else "status"
os.system("git " + user_supplied)
