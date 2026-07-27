FROM python:3.12-slim

ARG BANDIT_VERSION=1.8.6
ARG RUFF_VERSION=0.12.5

LABEL org.opencontainers.image.title="tRPC-Agent Skills Code Review Runtime" \
      org.opencontainers.image.description="Network-isolated Python toolchain for the code-review Skill" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN python -m pip install --no-cache-dir --no-compile \
      "bandit==${BANDIT_VERSION}" \
      "ruff==${RUFF_VERSION}"

WORKDIR /tmp/run

# Keep the SDK's default root user. Workspace files are injected by Docker's
# archive API as root-owned content, then made read-only before execution.
# Switching users here would prevent both that hardening step and cleanup.

# The SDK overrides this command and uses docker exec for each program. Keeping
# the default container alive also makes a plain `docker run -d` usable.
CMD ["tail", "-f", "/dev/null"]
