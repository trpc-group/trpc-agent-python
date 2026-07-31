# Scanner image for the code-review sandbox (production runtime).
# Build:  docker build -t cr-scanners:latest skills/code-review
# Used as the workspace image when the agent runs the skill in a container sandbox
# (REVIEW_RUNTIME=container, the default). Override the tag with REVIEW_SANDBOX_IMAGE.
FROM python:3.12-slim

RUN pip install --no-cache-dir bandit ruff "detect-secrets>=1.5" semgrep

# The standalone scanner entry point (self-contained; no example package import).
COPY scripts/run_checks.py /opt/skill/run_checks.py
COPY rules /opt/skill/rules

WORKDIR /workspace
