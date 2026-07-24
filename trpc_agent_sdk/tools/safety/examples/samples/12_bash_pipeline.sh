#!/usr/bin/env bash
# Shell pipeline sample.
# Expected scan result: decision=needs_human_review,
# rule_ids contains PROC003_SHELL_OPERATOR.

set -euo pipefail

ls -la | grep "\.py$" | wc -l
