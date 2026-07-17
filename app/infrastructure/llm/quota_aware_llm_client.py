"""
QuotaAwareLLMClient — wraps any LLMClient and enforces the global daily
quota guard BEFORE spending a call, not after failing into a 429.

This is a decorator, not a subclass of a concrete provider — it
implements the same LLMClient Protocol and delegates to whatever real
client it wraps (Gemini today, Groq/Ollama later). Every use case
already depends only on the LLMClient Protocol, so wrapping happens
once, at construction time, with zero changes to ingest_repo.py,
answer_question.py, or generate_diagram.py.

Why this matters after today's incident: retrying into a 429 wastes
time and (on some tiers) still counts against quota. Checking our own
soft-limit counter first means once we're near the daily cap, requests
fail FAST with a clear, friendly message — instead of hanging through
retry/backoff cycles only to fail anyway.
"""
from __future__ import annotations

import logging

from app.application.interfaces.llm_client import LLMClient, LLMProviderError
from app.infrastructure.security.rate_limiter import DailyQuotaGuard

logger = logging.getLogger(__name__)


class DailyQuotaExceededError(LLMProviderError):
    """
    Raised BEFORE an LLM call is attempted, when we're already at/near
    the daily soft limit. Subclasses LLMProviderError so existing
    except LLMProviderError handlers in use cases still catch it
    without modification — but callers that want to distinguish "we
    refused to even try" from "the provider itself failed" can check
    isinstance(e, DailyQuotaExceededError) specifically.
    """
    def __init__(self, provider: str):
        super().__init__(
            provider,
            "Daily free-tier quota is at capacity. Try again after the daily reset.",
            is_quota_exceeded=True,
        )


class QuotaAwareLLMClient(LLMClient):
    def __init__(self, *, wrapped: LLMClient, quota_guard: DailyQuotaGuard, provider_name: str = "llm") -> None:
        self._wrapped = wrapped
        self._guard = quota_guard
        self._provider_name = provider_name

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> str:
        self._check_quota()
        result = await self._wrapped.generate(
            prompt, system=system, max_tokens=max_tokens, temperature=temperature
        )
        self._guard.record_call()
        return result

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self._check_quota()
        result = await self._wrapped.embed(texts)
        # One embed() call may internally batch many texts into few
        # real API calls (see GeminiClient) — we record ONE unit here
        # per logical embed() invocation, matching how we record ONE
        # unit per generate() call. This is a deliberate, documented
        # simplification: the soft limit is a safety margin, not a
        # byte-exact accounting of provider-side request counts.
        self._guard.record_call()
        return result

    def _check_quota(self) -> None:
        if self._guard.is_over_soft_limit():
            logger.warning(
                "Refusing %s call: daily soft quota limit reached (%d calls today)",
                self._provider_name, self._guard.current_count,
            )
            raise DailyQuotaExceededError(self._provider_name)