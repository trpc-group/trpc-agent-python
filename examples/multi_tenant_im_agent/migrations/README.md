# Control-plane migrations

Run from the repository root:

```powershell
pip install -e ".[multi-tenant-im]"
$env:CONTROL_PLANE_DB_URL="mysql+pymysql://user:password@host:3306/trpc_agent"
alembic -c examples/multi_tenant_im_agent/alembic.ini upgrade head
```

Production deployments run migrations as a separate release job before rolling out Gateway pods. The application defaults to `AUTO_CREATE_SCHEMA=false` outside offline mode, so a missing migration fails startup instead of silently changing production tables.
