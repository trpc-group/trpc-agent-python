#!/usr/bin/env bash
# .env read sample.
# Expected scan result: decision=deny,
# rule_ids contains FILE004_DOTENV_READ.

set -euo pipefail

cat .env
