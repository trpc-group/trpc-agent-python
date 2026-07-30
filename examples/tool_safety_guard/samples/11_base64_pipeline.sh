#!/usr/bin/env bash
# Sample 11 — OBFUSCATED EXECUTION: decode a base64 payload and run it.
# Expected verdict: deny (SH011, high) — decode-then-execute hides the command.
echo "cm0gLXJmIH4v" | base64 -d | bash
