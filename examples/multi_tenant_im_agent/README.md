# Multi-Tenant IM Agent Gateway

This production-oriented reference implements tenant routing, Telegram and WeCom adapters, shared tRPC-Agent sessions, cross-node session serialization, callback idempotency, a transactional delivery outbox, tenant governance, audit records, metrics, tracing, and deployment manifests.

See the [Chinese quick start](./README.zh_CN.md) and the [full architecture and acceptance design](./ARCHITECTURE.zh_CN.md).

## Offline quick start

```powershell
python examples/multi_tenant_im_agent/scripts/judge_demo.py
```

The judge demo starts a real local HTTP gateway, creates a temporary database, runs signed Telegram and WeCom black-box callbacks, verifies idempotency, authentication, and metrics, then cleans everything up. It makes no model or IM network calls. Production mode creates a real tRPC-Agent `LlmAgent + Runner` per tenant and selects the configured Redis, SQL, or in-memory session service.

In a second terminal, set the three local values from `.env.local.example` and run:

```powershell
python examples/multi_tenant_im_agent/scripts/acceptance.py
```

For managed deployments, apply `alembic -c examples/multi_tenant_im_agent/alembic.ini upgrade head` before rolling out workers. Compose does this automatically; the Kubernetes manifest includes a release migration Job. Production workers keep `AUTO_CREATE_SCHEMA=false`.

## Verification

```powershell
pytest examples/multi_tenant_im_agent/tests -q
```

The test suite covers account routing, tenant/session isolation (including tenant-scoped app IDs), account-scoped idempotency, Telegram and WeCom callback verification, atomic token budgets, governance, duplicate delivery, payload conflicts, bounded retry/dead-letter behavior, transactional outbox recovery, audit-outage safety, provider token accounting, and HTTP operations endpoints.

## Optional real-model smoke test

Put `ACME_MODEL_API_KEY`, `REAL_MODEL_NAME`, and `REAL_MODEL_BASE_URL` in the Git-ignored `.env.private`, then run:

```powershell
uv run --no-project --with-editable . python examples/multi_tenant_im_agent/scripts/real_model_smoke.py
```

The verifier sends one short request through the real tRPC-Agent `LlmAgent + Runner` path. It prints only response length, token usage, or a sanitized failure status—never credentials or generated text.
