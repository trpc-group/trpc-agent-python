FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --system agent && useradd --system --gid agent --home-dir /app agent
WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY trpc_agent_sdk ./trpc_agent_sdk
COPY examples ./examples
RUN pip install ".[multi-tenant-im]"

USER agent
EXPOSE 8080
CMD ["python", "-m", "examples.multi_tenant_im_agent.main"]
