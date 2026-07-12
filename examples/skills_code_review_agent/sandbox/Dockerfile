FROM python:3.12.13-slim-bookworm

ARG REVIEW_IMAGE_POLICY_HASH=unverified
LABEL skills-code-review-agent.security-profile="${REVIEW_IMAGE_POLICY_HASH}"

RUN apt-get update \
    && apt-get install --yes --no-install-recommends git \
    && git config --system --add safe.directory '*' \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 65532 review \
    && useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin review

ENV HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /tmp
USER 65532:65532
