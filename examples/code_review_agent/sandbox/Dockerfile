FROM python:3.12-slim

ARG RUFF_VERSION=0.12.5
ARG BANDIT_VERSION=1.8.6
ARG PYTEST_VERSION=8.4.1

RUN python -m pip install --no-cache-dir \
    "ruff==${RUFF_VERSION}" \
    "bandit==${BANDIT_VERSION}" \
    "pytest==${PYTEST_VERSION}"

WORKDIR /workspace
USER 65532:65532

ENTRYPOINT []
