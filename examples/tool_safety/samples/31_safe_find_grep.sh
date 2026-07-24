#!/usr/bin/env bash
# Sample 31: safe find/grep without delete.
find . -name "*.py" | head -20
grep -R "TODO" src/ || true
