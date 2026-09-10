"""FastAPI gateway exposing IM callbacks, health, metrics, and a safe Admin API."""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .adapters import HttpChannelSender, NoopChannelSender
from .config import load_tenant_registry, require_secret
from .domain import StorageBackend
from .repository import ControlPlaneRepository
from .runtime import EchoRuntime, TrpcAgentRuntime
from .service import MultiTenantAgentService
from .telemetry import configure_otel_from_env

logger = logging.getLogger(__name__)


async def _outbox_loop(service: MultiTenantAgentService) -> None:
    while True:
        try:
            await service.dispatch_outbox_once()
        except Exception:  # noqa: BLE001 - recovery loop must survive provider/storage failures
            # Metrics and deployment alerts detect repeated failures; never let
            # one bad provider response terminate the recovery worker.
            logger.warning(
                "Outbox recovery iteration failed; retrying without logging payload or exception text"
            )
        await asyncio.sleep(2)


def create_app(
    service: MultiTenantAgentService, admin_token_env: str = "ADMIN_API_TOKEN"
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker = asyncio.create_task(_outbox_loop(service))
        yield
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker
        close = getattr(service.runtime, "close", None)
        if close is not None:
            await close()
        await asyncio.to_thread(service.repository.close)

    app = FastAPI(
        title="tRPC-Agent Multi-Tenant IM Gateway",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.get("/healthz", tags=["operations"])
    async def healthz():
        return {"status": "ok"}

    @app.get("/readyz", tags=["operations"])
    async def readyz():
        try:
            await asyncio.to_thread(service.repository.healthcheck)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="database unavailable") from exc
        return {"status": "ready"}

    @app.get("/metrics", response_class=PlainTextResponse, tags=["operations"])
    async def metrics():
        return service.metrics.render_prometheus()

    @app.get("/admin/tenants", tags=["admin"])
    async def tenants(x_admin_token: str = Header(default="")):
        expected = require_secret(admin_token_env)
        if not hmac.compare_digest(x_admin_token, expected):
            raise HTTPException(status_code=403, detail="forbidden")
        return {"tenants": service.registry.public_summary()}

    @app.post("/webhooks/{channel}/{account_id}", tags=["webhooks"])
    async def webhook(channel: str, account_id: str, request: Request):
        raw_body = await request.body()
        if len(raw_body) > 1_048_576:
            raise HTTPException(status_code=413, detail="callback body too large")
        response = await service.handle_webhook(
            channel=channel,
            account_id=account_id,
            headers=dict(request.headers),
            query=dict(request.query_params),
            raw_body=raw_body,
        )
        return JSONResponse(
            status_code=response.status_code,
            content=dict(response.body),
            headers=dict(response.headers),
        )

    return app


def build_app_from_env() -> FastAPI:
    configure_otel_from_env()
    # Fail before creating files or tables when the HMAC namespace key is absent.
    namespace_secret = require_secret("TENANT_NAMESPACE_SECRET")
    config_path = Path(
        os.environ.get(
            "TENANT_CONFIG_FILE", Path(__file__).with_name("config.example.json")
        )
    )
    registry = load_tenant_registry(config_path)
    offline = os.environ.get("OFFLINE_ECHO_MODE", "false").lower() == "true"
    if not offline:
        require_secret("ADMIN_API_TOKEN")
        for tenant in registry.all():
            require_secret(tenant.model_api_key_env)
            if tenant.session_backend is not StorageBackend.MEMORY:
                require_secret(tenant.session_dsn_env)
            for binding in tenant.bindings:
                require_secret(binding.webhook_secret_env)
                if binding.channel.lower() == "telegram":
                    require_secret(binding.bot_token_env)
                elif binding.channel.lower() == "wecom":
                    require_secret(binding.outbound_webhook_env)
    repository = ControlPlaneRepository(
        os.environ.get("CONTROL_PLANE_DB_URL", "sqlite:///multi_tenant_im.db")
    )
    auto_create_default = "true" if offline else "false"
    if os.environ.get("AUTO_CREATE_SCHEMA", auto_create_default).lower() == "true":
        repository.create_schema()
    repository.sync_tenants(registry.all())
    runtime = EchoRuntime() if offline else TrpcAgentRuntime()
    sender = NoopChannelSender() if offline else HttpChannelSender()
    service = MultiTenantAgentService(
        registry=registry,
        repository=repository,
        runtime=runtime,
        sender=sender,
        namespace_secret=namespace_secret,
    )
    return create_app(service)
