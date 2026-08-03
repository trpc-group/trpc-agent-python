# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""一次性连通性验证：确认 .env 里的 hy3 凭据经 OpenAIModel 能正常返回。

用法：
    python examples/optimization/eval_optimize_loop/verify_real.py
"""

import asyncio
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
for p in (str(_REPO_ROOT), str(_HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from dotenv import load_dotenv

for _cand in (_HERE / ".env", _HERE.parent / ".env", Path(".env")):
    if _cand.exists():
        load_dotenv(_cand, override=True)
        break
else:
    print("[warn] no .env found near", _HERE)

from real_call_agent import call_agent  # noqa: E402


async def main() -> None:
    print("[cfg] model   =", os.getenv("TRPC_AGENT_MODEL_NAME"))
    print("[cfg] base_url =", os.getenv("TRPC_AGENT_BASE_URL"))
    print("[cfg] api_key =", ("set" if os.getenv("TRPC_AGENT_API_KEY") else "MISSING"))
    try:
        out = await call_agent("你好，请用一句话介绍北京。")
        print("[OK] response =", repr(out))
    except Exception as e:  # noqa: BLE001
        print("[FAIL] ", type(e).__name__, str(e)[:500])


if __name__ == "__main__":
    asyncio.run(main())
