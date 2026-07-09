"""LLM client + factory (Phase 7).

The real client wraps the OpenAI SDK, which is OpenAI-compatible — it works
with OpenAI, Azure OpenAI, local vLLM, and internal gateways by simply
changing ``LLM_BASE_URL``/``LLM_API_KEY`` in ``.env``. The fake client is a
safe no-op used when the model is disabled or unconfigured.
"""
from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Optional

from .config import LlmConfig, load_llm_config

logger = logging.getLogger("cr_agent.llm")


class LlmClient(ABC):
    """Async client that judges low-confidence candidate findings."""

    is_enabled: bool = True
    kind: str = "base"

    @abstractmethod
    async def triage(self, findings, diff_text: str) -> list[dict]:
        """Return verdicts: list of
        ``{"index": int, "verdict": "real"|"false_positive",
            "confidence": float, "explanation": str}``.

        An empty list means "no change" (degrade to no-op).
        """
        ...


class FakeLlm(LlmClient):
    """No-op client — used when the model is disabled / no API key."""

    is_enabled = False
    kind = "fake"

    async def triage(self, findings, diff_text: str) -> list[dict]:
        return []


_SYSTEM_PROMPT = (
    "You are a senior static-analysis code reviewer. You will receive a code "
    "diff and a list of candidate issues found by automated rules. For each "
    "candidate, decide whether it is a REAL issue or a FALSE POSITIVE. "
    "Respond with STRICT JSON only: "
    '{"verdicts":[{"index":int,"verdict":"real"|"false_positive",'
    '"confidence":float 0-1,"explanation":str}]}. '
    "Only include entries you can judge with confidence; omit uncertain ones "
    "(they stay unchanged). 'confidence' should reflect your own certainty "
    "for real issues."
)

_USER_TEMPLATE = (
    "## Code diff (secrets masked)\n"
    "```\n{diff}\n```\n\n"
    "## Candidate issues (index = position)\n"
    "{items}\n\n"
    "Return the JSON verdicts now."
)


class RealLlm(LlmClient):
    """Real OpenAI-compatible client. Lazily imports ``openai``."""

    kind = "real"

    def __init__(self, config: LlmConfig, client=None):
        self.config = config
        self._client = client

    def _make_client(self):
        from openai import AsyncOpenAI

        kwargs = {
            "api_key": self.config.api_key,
            "timeout": self.config.timeout,
        }
        if self.config.base_url:
            kwargs["base_url"] = self.config.base_url
        return AsyncOpenAI(**kwargs)

    @property
    def client(self):
        if self._client is None:
            self._client = self._make_client()
        return self._client

    async def _chat(self, messages):
        resp = await self.client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )
        return resp

    async def triage(self, findings, diff_text: str) -> list[dict]:
        if not findings:
            return []
        payload = [
            {
                "index": i,
                "file": f.file,
                "line": f.line,
                "category": f.category,
                "title": f.title,
                "evidence": f.evidence,
            }
            for i, f in enumerate(findings)
        ]
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _USER_TEMPLATE.format(
                    diff=diff_text,
                    items=json.dumps(payload, ensure_ascii=False, indent=2),
                ),
            },
        ]
        try:
            resp = await self._chat(messages)
            content = resp.choices[0].message.content or ""
            return _parse_verdicts(content)
        except Exception as exc:  # network / parse / timeout -> degrade
            logger.warning("llm triage failed, degrading to no-op: %s", exc)
            return []


def _parse_verdicts(content: str) -> list[dict]:
    """Best-effort JSON parse of the model response (handles code fences)."""
    try:
        s = content.strip()
        if s.startswith("```"):
            s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
            s = s.rstrip("`").strip()
        data = json.loads(s)
        vs = data.get("verdicts") or []
    except Exception:
        return []
    out: list[dict] = []
    for v in vs:
        try:
            idx = int(v["index"])
            verdict = str(v.get("verdict", "real")).lower()
            conf = float(v.get("confidence", 0.5))
            conf = max(0.0, min(1.0, conf))
            out.append(
                {
                    "index": idx,
                    "verdict": verdict,
                    "confidence": conf,
                    "explanation": str(v.get("explanation", "")),
                }
            )
        except (KeyError, ValueError, TypeError):
            continue
    return out


def get_llm_client(enable=None, env_path: Optional[str] = None) -> LlmClient:
    """Return a :class:`RealLlm` when enabled + keyed, else :class:`FakeLlm`.

    ``enable`` (e.g. from ``--enable-llm``) overrides the ``.env`` flag when
    not ``None``. Without an API key the real client is never instantiated,
    guaranteeing the no-key test path stays model-free.
    """
    cfg = load_llm_config(env_path)
    on = cfg.enabled if enable is None else enable
    if not on or not cfg.has_key:
        return FakeLlm()
    return RealLlm(cfg)
