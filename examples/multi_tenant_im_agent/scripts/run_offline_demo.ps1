$ErrorActionPreference = "Stop"

$exampleRoot = Split-Path -Parent $PSScriptRoot
$repositoryRoot = (Resolve-Path (Join-Path $exampleRoot "..\..")).Path

if (-not $env:OFFLINE_ECHO_MODE) { $env:OFFLINE_ECHO_MODE = "true" }
if (-not $env:AUTO_CREATE_SCHEMA) { $env:AUTO_CREATE_SCHEMA = "true" }
if (-not $env:TENANT_CONFIG_FILE) {
    $env:TENANT_CONFIG_FILE = Join-Path $exampleRoot "config.local.json"
}
if (-not $env:CONTROL_PLANE_DB_URL) {
    $databasePath = Join-Path $exampleRoot "multi_tenant_im.local.db"
    $env:CONTROL_PLANE_DB_URL = "sqlite:///$($databasePath.Replace('\', '/'))"
}
if (-not $env:TENANT_NAMESPACE_SECRET) {
    $env:TENANT_NAMESPACE_SECRET = "local-demo-namespace-secret-32-chars"
}
if (-not $env:ADMIN_API_TOKEN) { $env:ADMIN_API_TOKEN = "local-demo-admin-token" }
if (-not $env:ACME_TELEGRAM_WEBHOOK_SECRET) {
    $env:ACME_TELEGRAM_WEBHOOK_SECRET = "local-telegram-secret"
}
if (-not $env:ACME_WECOM_CALLBACK_TOKEN) {
    $env:ACME_WECOM_CALLBACK_TOKEN = "local-wecom-token"
}

Set-Location $repositoryRoot
python -m examples.multi_tenant_im_agent.main
