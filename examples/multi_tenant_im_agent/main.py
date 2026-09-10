"""Server entry point: python -m examples.multi_tenant_im_agent.main"""

from __future__ import annotations

import os

import uvicorn

from .app import build_app_from_env


def main() -> None:
    uvicorn.run(
        build_app_from_env(),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()
