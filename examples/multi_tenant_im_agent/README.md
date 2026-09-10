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

The test suite covers account routing, tenant/session isolation, Telegram and WeCom callback verification, governance, duplicate delivery, payload conflicts, retry after failures, session leases, transactional outbox recovery, safe audit identifiers, and HTTP operations endpoints.
