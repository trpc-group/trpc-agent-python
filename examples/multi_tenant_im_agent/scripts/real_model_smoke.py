"""Run one credential-safe tRPC-Agent model smoke test."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from examples.multi_tenant_im_agent.domain import (
    ChatType,
    InboundMessage,
    StorageBackend,
    TenantConfig,
)
from examples.multi_tenant_im_agent.runtime import TrpcAgentRuntime


def load_private_env() -> None:
    """Load the ignored local file without printing any values."""

    path = Path(__file__).parents[1] / ".env.private"
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


def provider_status_code(exc: BaseException) -> int | None:
    """Find an HTTP status in a wrapped exception without rendering its text."""

    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        status = getattr(current, "status_code", None)
        if isinstance(status, int):
            return status
        current = current.__cause__ or current.__context__
    return None


async def main() -> None:
    load_private_env()
    api_key = os.environ.get("ACME_MODEL_API_KEY", "").strip()
    model_name = os.environ.get("REAL_MODEL_NAME", "").strip()
    base_url = os.environ.get("REAL_MODEL_BASE_URL", "").strip()
    if not api_key or api_key == "replace-me":
        raise RuntimeError("ACME_MODEL_API_KEY is not configured")
    if not model_name:
        raise RuntimeError("REAL_MODEL_NAME is not configured")

    tenant = TenantConfig(
        tenant_id="real-model-smoke",
        display_name="Real Model Smoke Test",
        agent_app_id="smoke-agent",
        agent_name="smoke_agent",
        model_name=model_name,
        model_base_url=base_url,
        model_api_key_env="ACME_MODEL_API_KEY",
        session_backend=StorageBackend.MEMORY,
        model_timeout_seconds=20,
        session_lease_seconds=30,
    )
    message = InboundMessage(
        tenant_id=tenant.tenant_id,
        channel="local-smoke",
        account_id="local",
        external_message_id="smoke-1",
        user_id="smoke-user",
        conversation_id="smoke-conversation",
        chat_type=ChatType.DIRECT,
        text="Reply with exactly OK and nothing else.",
    )
    runtime = TrpcAgentRuntime()
    try:
        # SDK retry logs may include provider response bodies. The verifier emits
        # only the bounded status lines below.
        logging.disable(logging.CRITICAL)
        reply = await runtime.reply(
            tenant=tenant,
            message=message,
            user_id="smoke-user",
            session_id="smoke-session",
        )
        if not reply.text.strip():
            raise RuntimeError("model returned an empty response")
        print("REAL MODEL SMOKE PASSED")
        print(f"response_chars={len(reply.text)}")
        print(f"reported_tokens={reply.token_count}")
    finally:
        try:
            await asyncio.wait_for(runtime.close(), timeout=5)
        except TimeoutError:
            print("runner_close=timed_out")


if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(main(), timeout=30))
    except TimeoutError:
        print("REAL MODEL SMOKE FAILED: request timed out")
        raise SystemExit(2) from None
    except Exception as exc:  # noqa: BLE001 - print only the safe exception type
        print(f"REAL MODEL SMOKE FAILED: {type(exc).__name__}")
        status_code = provider_status_code(exc)
        if status_code is not None:
            print(f"provider_http_status={status_code}")
        raise SystemExit(1) from None
