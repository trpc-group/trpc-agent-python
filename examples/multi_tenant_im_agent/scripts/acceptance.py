"""Black-box acceptance checks for a running offline gateway."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
import uuid

import httpx


def _require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"required environment variable is missing: {name}")
    return value


def _check(response: httpx.Response, status: int, label: str) -> dict:
    if response.status_code != status:
        raise AssertionError(
            f"{label}: expected HTTP {status}, got {response.status_code}: "
            f"{response.text[:300]}"
        )
    if response.headers.get("content-type", "").startswith("application/json"):
        return response.json()
    return {}


def _passed(label: str) -> None:
    print(f"[PASS] {label}")


def run(base_url: str) -> None:
    telegram_secret = _require_env("ACME_TELEGRAM_WEBHOOK_SECRET")
    wecom_token = _require_env("ACME_WECOM_CALLBACK_TOKEN")
    admin_token = _require_env("ADMIN_API_TOKEN")
    unique = uuid.uuid4().hex[:12]

    with httpx.Client(base_url=base_url, timeout=10) as client:
        _check(client.get("/healthz"), 200, "liveness")
        _check(client.get("/readyz"), 200, "readiness")
        _passed("health and database readiness")

        telegram_path = "/webhooks/telegram/acme-support-bot"
        update = {
            "update_id": int(time.time() * 1000),
            "message": {
                "message_id": 1,
                "from": {"id": 10001},
                "chat": {"id": 10001, "type": "private"},
                "text": f"acceptance-{unique}",
            },
        }
        _check(
            client.post(
                telegram_path,
                headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
                json=update,
            ),
            401,
            "Telegram rejects an invalid signature",
        )
        _passed("invalid Telegram signature is rejected")
        headers = {"X-Telegram-Bot-Api-Secret-Token": telegram_secret}
        first = _check(
            client.post(telegram_path, headers=headers, json=update),
            200,
            "Telegram callback",
        )
        if not first.get("ok") or first.get("queued"):
            raise AssertionError("Telegram callback was not completed synchronously")
        duplicate = _check(
            client.post(telegram_path, headers=headers, json=update),
            200,
            "Telegram duplicate callback",
        )
        if not duplicate.get("duplicate"):
            raise AssertionError("Telegram retry was not recognized as a duplicate")
        conflicting = dict(update)
        conflicting["message"] = {**update["message"], "text": "changed-payload"}
        _check(
            client.post(telegram_path, headers=headers, json=conflicting),
            409,
            "Telegram idempotency conflict",
        )
        _passed(
            "Telegram routing, reply, duplicate suppression, and conflict detection"
        )

        timestamp = str(int(time.time()))
        nonce = unique
        signature = hashlib.sha1(
            "".join(sorted((wecom_token, timestamp, nonce))).encode()
        ).hexdigest()
        wecom_payload = {
            "FromUserName": "acceptance-user",
            "ToUserName": "acceptance-app",
            "MsgId": f"wecom-{unique}",
            "Content": "acceptance from WeCom",
            "ChatType": "single",
        }
        wecom = _check(
            client.post(
                "/webhooks/wecom/acme-wecom-app",
                params={"timestamp": timestamp, "nonce": nonce, "signature": signature},
                json=wecom_payload,
            ),
            200,
            "WeCom callback",
        )
        if not wecom.get("ok"):
            raise AssertionError("WeCom callback did not complete")
        _passed("signed WeCom callback normalization and reply")

        _check(client.get("/admin/tenants"), 403, "Admin API authentication")
        admin = _check(
            client.get("/admin/tenants", headers={"X-Admin-Token": admin_token}),
            200,
            "Admin API",
        )
        if len(admin.get("tenants", [])) != 1:
            raise AssertionError("Admin API did not return exactly one demo tenant")
        _passed("Admin API authentication and safe tenant summary")
        metrics = client.get("/metrics")
        _check(metrics, 200, "Prometheus metrics")
        if "trpc_im_requests_total" not in metrics.text:
            raise AssertionError("request metrics were not emitted")
        _passed("Prometheus request metrics")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    try:
        run(args.base_url.rstrip("/"))
    except (AssertionError, RuntimeError, httpx.HTTPError) as exc:
        print(f"ACCEPTANCE FAILED: {exc}", file=sys.stderr)
        return 1
    print(
        "ACCEPTANCE PASSED: health, auth, routing, signatures, idempotency, and metrics"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
