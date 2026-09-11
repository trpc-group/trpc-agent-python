from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from examples.multi_tenant_im_agent.adapters import (
    NoopChannelSender,
    TelegramAdapter,
    WeComAdapter,
)
from examples.multi_tenant_im_agent.app import build_app_from_env, create_app
from examples.multi_tenant_im_agent.config import ConfigurationError, TenantRegistry
from examples.multi_tenant_im_agent.domain import (
    AgentReply,
    ChannelBinding,
    ChatType,
    InboundMessage,
    StorageBackend,
    TenantConfig,
    derive_session_id,
    derive_session_user_id,
)
from examples.multi_tenant_im_agent.repository import (
    OUTBOX_MAX_ATTEMPTS,
    AgentAppRecord,
    AuditLogRecord,
    ControlPlaneRepository,
    MessageEventRecord,
    OutboxRecord,
    TenantRecord,
)
from examples.multi_tenant_im_agent.runtime import TrpcAgentRuntime, event_token_count
from examples.multi_tenant_im_agent.service import MultiTenantAgentService
from examples.multi_tenant_im_agent.telemetry import request_span

NAMESPACE_SECRET = "unit-test-namespace-secret-32-characters"


def tenant(
    tenant_id: str = "tenant-a",
    account_id: str = "bot-a",
    *,
    allowed_user_ids: tuple[str, ...] = (),
) -> TenantConfig:
    return TenantConfig(
        tenant_id=tenant_id,
        display_name=tenant_id,
        agent_app_id=f"app-{tenant_id}",
        session_backend=StorageBackend.MEMORY,
        allowed_user_ids=allowed_user_ids,
        bindings=(
            ChannelBinding(
                channel="telegram",
                account_id=account_id,
                webhook_secret_env=f"{tenant_id.upper().replace('-', '_')}_TG_SECRET",
                bot_token_env=f"{tenant_id.upper().replace('-', '_')}_TG_TOKEN",
            ),
        ),
    )


class RecordingRuntime:
    def __init__(self):
        self.calls = []

    async def reply(self, *, tenant, message, user_id, session_id):
        self.calls.append(
            (tenant.tenant_id, message.external_message_id, user_id, session_id)
        )
        return AgentReply(text=f"reply:{message.text}", token_count=7, cost=0.01)


class FailOnceRuntime(RecordingRuntime):
    async def reply(self, *, tenant, message, user_id, session_id):
        self.calls.append(
            (tenant.tenant_id, message.external_message_id, user_id, session_id)
        )
        if len(self.calls) == 1:
            raise RuntimeError(
                "provider unavailable with secret that must not be returned"
            )
        return AgentReply(text="recovered", token_count=2)


class RecordingSender:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.requests = []

    async def send(self, request):
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("delivery unavailable")
        return {"ok": True}


def telegram_body(
    update_id: int = 100, message_id: int = 9, text: str = "hello", user_id: int = 42
) -> bytes:
    return json.dumps(
        {
            "update_id": update_id,
            "message": {
                "message_id": message_id,
                "from": {"id": user_id},
                "chat": {"id": user_id, "type": "private"},
                "text": text,
            },
        }
    ).encode()


@pytest.fixture
def repository():
    repo = ControlPlaneRepository("sqlite:///:memory:")
    repo.create_schema()
    return repo


def build_service(monkeypatch, repository, runtime=None, sender=None, tenants=None):
    tenants = tenants or [tenant()]
    for item in tenants:
        for binding in item.bindings:
            monkeypatch.setenv(binding.webhook_secret_env, f"secret-{item.tenant_id}")
            if binding.bot_token_env:
                monkeypatch.setenv(binding.bot_token_env, "test-bot-token")
    registry = TenantRegistry(tenants)
    repository.sync_tenants(registry.all())
    runtime = runtime or RecordingRuntime()
    sender = sender or RecordingSender()
    service = MultiTenantAgentService(
        registry=registry,
        repository=repository,
        runtime=runtime,
        sender=sender,
        namespace_secret=NAMESPACE_SECRET,
    )
    return service, runtime, sender


def test_registry_rejects_duplicate_channel_account():
    with pytest.raises(ConfigurationError):
        TenantRegistry([tenant("one", "shared"), tenant("two", "shared")])


def test_registry_rejects_lease_shorter_than_model_timeout():
    invalid = replace(tenant(), model_timeout_seconds=90, session_lease_seconds=90)
    with pytest.raises(ConfigurationError, match="greater than"):
        TenantRegistry([invalid])


def test_session_id_is_stable_and_tenant_isolated():
    base = InboundMessage(
        tenant_id="a",
        channel="telegram",
        account_id="bot",
        external_message_id="1",
        user_id="u1",
        conversation_id="u1",
        chat_type=ChatType.DIRECT,
        text="hello",
    )
    assert derive_session_id(base, NAMESPACE_SECRET) == derive_session_id(
        base, NAMESPACE_SECRET
    )
    assert derive_session_id(base, NAMESPACE_SECRET) != derive_session_id(
        replace(base, tenant_id="b"), NAMESPACE_SECRET
    )
    assert derive_session_id(base, NAMESPACE_SECRET) != derive_session_id(
        replace(base, user_id="u2"), NAMESPACE_SECRET
    )


def test_group_session_is_shared_by_members_but_not_groups():
    base = InboundMessage(
        tenant_id="a",
        channel="telegram",
        account_id="bot",
        external_message_id="1",
        user_id="u1",
        conversation_id="g1",
        chat_type=ChatType.GROUP,
        text="hello",
    )
    assert derive_session_id(base, NAMESPACE_SECRET) == derive_session_id(
        replace(base, user_id="u2"), NAMESPACE_SECRET
    )
    assert derive_session_user_id(base, NAMESPACE_SECRET) == derive_session_user_id(
        replace(base, user_id="u2"), NAMESPACE_SECRET
    )
    assert derive_session_id(base, NAMESPACE_SECRET) != derive_session_id(
        replace(base, conversation_id="g2"), NAMESPACE_SECRET
    )


def test_telegram_verification_and_normalization(monkeypatch):
    binding = tenant().bindings[0]
    monkeypatch.setenv(binding.webhook_secret_env, "expected")
    adapter = TelegramAdapter()
    adapter.verify(
        binding=binding,
        headers={"X-Telegram-Bot-Api-Secret-Token": "expected"},
        query={},
        raw_body=b"{}",
    )
    message = adapter.parse(tenant=tenant(), binding=binding, raw_body=telegram_body())
    assert message.external_message_id == "100:9"
    assert message.chat_type is ChatType.DIRECT
    assert message.text == "hello"


def test_wecom_signature_and_json_normalization(monkeypatch):
    binding = ChannelBinding(
        "wecom", "corp-app", "WECOM_TOKEN", outbound_webhook_env="WECOM_WEBHOOK"
    )
    config = replace(tenant(), bindings=(binding,))
    monkeypatch.setenv("WECOM_TOKEN", "callback-token")
    timestamp = str(int(time.time()))
    nonce = "random"
    signature = hashlib.sha1(
        "".join(sorted(["callback-token", timestamp, nonce])).encode()
    ).hexdigest()
    body = json.dumps(
        {
            "MsgId": "wx-1",
            "FromUserName": "alice",
            "ChatId": "group-8",
            "ChatType": "group",
            "Content": "hi",
        }
    ).encode()
    adapter = WeComAdapter()
    adapter.verify(
        binding=binding,
        headers={},
        query={"timestamp": timestamp, "nonce": nonce, "signature": signature},
        raw_body=body,
    )
    message = adapter.parse(tenant=config, binding=binding, raw_body=body)
    assert message.chat_type is ChatType.GROUP
    assert message.external_message_id == "wx-1"


@pytest.mark.asyncio
async def test_duplicate_callback_runs_agent_and_delivery_once(monkeypatch, repository):
    service, runtime, sender = build_service(monkeypatch, repository)
    kwargs = {
        "channel": "telegram",
        "account_id": "bot-a",
        "headers": {"x-telegram-bot-api-secret-token": "secret-tenant-a"},
        "query": {},
        "raw_body": telegram_body(),
    }
    first = await service.handle_webhook(**kwargs)
    second = await service.handle_webhook(**kwargs)
    assert first.status_code == 200
    assert second.body["duplicate"] is True
    assert len(runtime.calls) == 1
    assert len(sender.requests) == 1


@pytest.mark.asyncio
async def test_same_id_with_different_payload_is_conflict(monkeypatch, repository):
    service, runtime, _ = build_service(monkeypatch, repository)
    common = {
        "channel": "telegram",
        "account_id": "bot-a",
        "headers": {"x-telegram-bot-api-secret-token": "secret-tenant-a"},
        "query": {},
    }
    assert (
        await service.handle_webhook(**common, raw_body=telegram_body(text="one"))
    ).status_code == 200
    result = await service.handle_webhook(
        **common, raw_body=telegram_body(text="tampered")
    )
    assert result.status_code == 409
    assert len(runtime.calls) == 1


@pytest.mark.asyncio
async def test_same_external_id_is_isolated_across_tenants(monkeypatch, repository):
    configs = [tenant("tenant-a", "bot-a"), tenant("tenant-b", "bot-b")]
    service, runtime, _ = build_service(monkeypatch, repository, tenants=configs)
    for config in configs:
        response = await service.handle_webhook(
            channel="telegram",
            account_id=config.bindings[0].account_id,
            headers={"x-telegram-bot-api-secret-token": f"secret-{config.tenant_id}"},
            query={},
            raw_body=telegram_body(),
        )
        assert response.status_code == 200
    assert len(runtime.calls) == 2
    assert runtime.calls[0][3] != runtime.calls[1][3]


def test_same_agent_app_id_is_isolated_across_tenants(monkeypatch, repository):
    configs = [
        replace(tenant("tenant-a", "bot-a"), agent_app_id="shared-app"),
        replace(tenant("tenant-b", "bot-b"), agent_app_id="shared-app"),
    ]
    build_service(monkeypatch, repository, tenants=configs)
    with repository.Session() as db:
        apps = list(db.scalars(select(AgentAppRecord)))
        assert {(item.tenant_id, item.agent_app_id) for item in apps} == {
            ("tenant-a", "shared-app"),
            ("tenant-b", "shared-app"),
        }


@pytest.mark.asyncio
async def test_same_external_id_is_isolated_across_accounts(monkeypatch, repository):
    first = tenant().bindings[0]
    second = replace(
        first,
        account_id="bot-b",
        webhook_secret_env="TENANT_A_TG_SECRET_B",
        bot_token_env="TENANT_A_TG_TOKEN_B",
    )
    config = replace(tenant(), bindings=(first, second))
    service, runtime, _ = build_service(monkeypatch, repository, tenants=[config])
    for binding in config.bindings:
        response = await service.handle_webhook(
            channel="telegram",
            account_id=binding.account_id,
            headers={"x-telegram-bot-api-secret-token": "secret-tenant-a"},
            query={},
            raw_body=telegram_body(),
        )
        assert response.status_code == 200
    assert len(runtime.calls) == 2


@pytest.mark.asyncio
async def test_failed_message_can_be_retried_without_duplicate_row(
    monkeypatch, repository
):
    runtime = FailOnceRuntime()
    service, _, _ = build_service(monkeypatch, repository, runtime=runtime)
    kwargs = {
        "channel": "telegram",
        "account_id": "bot-a",
        "headers": {"x-telegram-bot-api-secret-token": "secret-tenant-a"},
        "query": {},
        "raw_body": telegram_body(),
    }
    assert (await service.handle_webhook(**kwargs)).status_code == 503
    assert (await service.handle_webhook(**kwargs)).status_code == 200
    with repository.Session() as db:
        assert db.scalar(select(func.count()).select_from(MessageEventRecord)) == 1


@pytest.mark.asyncio
async def test_policy_denies_unlisted_user_before_runtime(monkeypatch, repository):
    service, runtime, _ = build_service(
        monkeypatch, repository, tenants=[tenant(allowed_user_ids=("99",))]
    )
    response = await service.handle_webhook(
        channel="telegram",
        account_id="bot-a",
        headers={"x-telegram-bot-api-secret-token": "secret-tenant-a"},
        query={},
        raw_body=telegram_body(user_id=42),
    )
    assert response.status_code == 403
    assert not runtime.calls


@pytest.mark.asyncio
async def test_monthly_token_budget_denies_before_runtime(monkeypatch, repository):
    limited = replace(tenant(), monthly_token_budget=1)
    service, runtime, _ = build_service(monkeypatch, repository, tenants=[limited])
    response = await service.handle_webhook(
        channel="telegram",
        account_id="bot-a",
        headers={"x-telegram-bot-api-secret-token": "secret-tenant-a"},
        query={},
        raw_body=telegram_body(text="more than four characters"),
    )
    assert response.status_code == 403
    assert response.body["error"] == "monthly_token_budget_exceeded"
    assert not runtime.calls


@pytest.mark.asyncio
async def test_monthly_budget_uses_atomic_reserved_and_actual_usage(
    monkeypatch, repository
):
    limited = replace(tenant(), monthly_token_budget=5)
    service, runtime, _ = build_service(monkeypatch, repository, tenants=[limited])
    common = {
        "channel": "telegram",
        "account_id": "bot-a",
        "headers": {"x-telegram-bot-api-secret-token": "secret-tenant-a"},
        "query": {},
    }
    assert (
        await service.handle_webhook(
            **common, raw_body=telegram_body(update_id=1, text="a")
        )
    ).status_code == 200
    denied = await service.handle_webhook(
        **common, raw_body=telegram_body(update_id=2, text="b")
    )
    assert denied.status_code == 403
    assert denied.body["error"] == "monthly_token_budget_exceeded"
    assert len(runtime.calls) == 1
    with repository.Session() as db:
        assert db.get(TenantRecord, "tenant-a").token_usage == 7


def test_session_lease_excludes_other_worker(repository):
    assert repository.acquire_session_lease("session-a", "worker-1", 30)
    assert not repository.acquire_session_lease("session-a", "worker-2", 30)
    repository.release_session_lease("session-a", "worker-1")
    assert repository.acquire_session_lease("session-a", "worker-2", 30)


@pytest.mark.asyncio
async def test_delivery_failure_is_queued_and_recoverable(monkeypatch, repository):
    sender = RecordingSender(fail=True)
    service, _, _ = build_service(monkeypatch, repository, sender=sender)
    response = await service.handle_webhook(
        channel="telegram",
        account_id="bot-a",
        headers={"x-telegram-bot-api-secret-token": "secret-tenant-a"},
        query={},
        raw_body=telegram_body(),
    )
    assert response.status_code == 202
    with repository.Session.begin() as db:
        item = db.scalar(select(OutboxRecord))
        assert item.status == "retry"
        item.next_attempt_at = item.created_at
    sender.fail = False
    assert await service.dispatch_outbox_once() == 1
    with repository.Session() as db:
        assert db.scalar(select(OutboxRecord.status)) == "sent"


@pytest.mark.asyncio
async def test_outbox_moves_to_dead_letter_after_bounded_retries(
    monkeypatch, repository
):
    sender = RecordingSender(fail=True)
    service, _, _ = build_service(monkeypatch, repository, sender=sender)
    await service.handle_webhook(
        channel="telegram",
        account_id="bot-a",
        headers={"x-telegram-bot-api-secret-token": "secret-tenant-a"},
        query={},
        raw_body=telegram_body(),
    )
    with repository.Session.begin() as db:
        item = db.scalar(select(OutboxRecord))
        item.attempts = OUTBOX_MAX_ATTEMPTS
        outbox_id = item.outbox_id
    assert repository.mark_outbox_retry(outbox_id) == "dead_letter"
    with repository.Session() as db:
        assert db.get(OutboxRecord, outbox_id).status == "dead_letter"


@pytest.mark.asyncio
async def test_audit_outage_does_not_replay_completed_turn(monkeypatch, repository):
    service, runtime, sender = build_service(monkeypatch, repository)

    def fail_audit(_values):
        raise RuntimeError("audit database unavailable")

    monkeypatch.setattr(repository, "add_audit", fail_audit)
    kwargs = {
        "channel": "telegram",
        "account_id": "bot-a",
        "headers": {"x-telegram-bot-api-secret-token": "secret-tenant-a"},
        "query": {},
        "raw_body": telegram_body(),
    }
    assert (await service.handle_webhook(**kwargs)).status_code == 200
    assert (await service.handle_webhook(**kwargs)).body["duplicate"] is True
    assert len(runtime.calls) == 1
    assert len(sender.requests) == 1
    assert (
        'trpc_im_audit_total{status="failed"} 2' in service.metrics.render_prometheus()
    )


@pytest.mark.asyncio
async def test_audit_contains_required_safe_identifiers(monkeypatch, repository):
    service, _, _ = build_service(monkeypatch, repository)
    await service.handle_webhook(
        channel="telegram",
        account_id="bot-a",
        headers={"x-telegram-bot-api-secret-token": "secret-tenant-a"},
        query={},
        raw_body=telegram_body(user_id=424242),
    )
    with repository.Session() as db:
        audit = db.scalar(select(AuditLogRecord))
        assert audit.tenant_id == "tenant-a"
        assert audit.channel == "telegram"
        assert audit.user_id.startswith("usr_")
        assert "424242" not in audit.user_id
        assert audit.session_id.startswith("ses_")
        assert audit.decision == "completed"


def test_http_health_ready_metrics_and_admin(monkeypatch, repository):
    service, _, _ = build_service(monkeypatch, repository)
    monkeypatch.setenv("ADMIN_API_TOKEN", "admin-secret")
    with TestClient(create_app(service)) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200
        assert client.get("/metrics").status_code == 200
        assert client.get("/admin/tenants").status_code == 403
        response = client.get(
            "/admin/tenants", headers={"X-Admin-Token": "admin-secret"}
        )
        assert response.status_code == 200
        assert response.json()["tenants"][0]["tenant_id"] == "tenant-a"


def test_environment_factory_runs_in_offline_mode(monkeypatch):
    monkeypatch.setenv("OFFLINE_ECHO_MODE", "true")
    monkeypatch.setenv("TENANT_NAMESPACE_SECRET", NAMESPACE_SECRET)
    monkeypatch.setenv("CONTROL_PLANE_DB_URL", "sqlite:///:memory:")
    with TestClient(build_app_from_env()) as client:
        assert client.get("/readyz").status_code == 200


def test_app_lifespan_disposes_repository_engine(monkeypatch, repository):
    service, _, _ = build_service(monkeypatch, repository)
    monkeypatch.setenv("ADMIN_API_TOKEN", "admin-secret")
    disposed = False
    original_dispose = repository.engine.dispose

    def record_dispose() -> None:
        nonlocal disposed
        disposed = True
        original_dispose()

    monkeypatch.setattr(repository.engine, "dispose", record_dispose)
    with TestClient(create_app(service)) as client:
        assert client.get("/readyz").status_code == 200
    assert disposed


def test_trace_context_does_not_swallow_application_import_error():
    with (
        pytest.raises(ImportError, match="application failure"),
        request_span("tenant", "telegram", "session"),
    ):
        raise ImportError("application failure")


def test_namespace_secret_must_be_strong(repository):
    registry = TenantRegistry([tenant()])
    with pytest.raises(ValueError):
        MultiTenantAgentService(
            registry=registry,
            repository=repository,
            runtime=RecordingRuntime(),
            sender=NoopChannelSender(),
            namespace_secret="short",
        )


def test_tool_allowlist_fails_closed_for_unknown_tool():
    runtime = TrpcAgentRuntime(tool_registry={"safe_tool": object()})
    config = replace(tenant(), tool_allowlist=("missing_tool",))
    with pytest.raises(ConfigurationError, match="unregistered tools"):
        runtime._resolve_tools(config)


@pytest.mark.asyncio
async def test_trpc_runtime_awaits_runner_shutdown():
    class FakeRunner:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    runner = FakeRunner()
    runtime = TrpcAgentRuntime()
    runtime._runners["tenant-a"] = runner
    await runtime.close()
    assert runner.closed
    assert runtime._runners == {}


def test_runtime_extracts_provider_usage_metadata():
    assert (
        event_token_count(
            SimpleNamespace(usage_metadata=SimpleNamespace(total_token_count=37))
        )
        == 37
    )
    assert (
        event_token_count(
            SimpleNamespace(
                usage_metadata=SimpleNamespace(
                    total_token_count=None,
                    prompt_token_count=11,
                    candidates_token_count=7,
                )
            )
        )
        == 18
    )
