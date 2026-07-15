#!/usr/bin/env bash
# Dependency install sample.
# Expected scan result: decision=deny, rule_ids contains DEP001_ENV_MUTATION.

set -euo pipefail

pip install requests
npm install lodash
