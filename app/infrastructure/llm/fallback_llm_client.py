"""
Tries a primary LLMClient's generate() first; if it raises
LLMProviderError, automatically falls back to a secondary client.
Only applies to generate() — embeddings are never fallback-routed
(see CompositeLLMClient) since mixing embedding providers across
ingestions would break vector-space consistency.

After a primary failure the primary is skipped for a cooldown period.
Without this, a provider that is down or misconfigured (a bad key
returns 401 every time) costs a failed round-trip on every single
call — and a deep investigation makes six of them in a row.
"""
from __future__ import annotations

import logging
import time

from app.application.interfaces.llm_client import LLMClient, LLMProviderError

logger = logging.getLogger(__name__)


class FallbackLLMClient(LLMClient):
    def __init__(
        self,
        *,
        primary: LLMClient,
        secondary: LLMClient,
        primary_name: str,
        secondary_name: str,
        cooldown_seconds: float = 300.0,
    ) -> None:
        self._primary = primary
        self._secondary = secondary
        self._primary_name = primary_name
        self._secondary_name = secondary_name
        self._cooldown_seconds = cooldown_seconds
        self._primary_retry_at: float | None = None  # None = primary is healthy

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> str:
        if self._primary_available():
            try:
                result = await self._primary.generate(
                    prompt, system=system, max_tokens=max_tokens, temperature=temperature
                )
                if self._primary_retry_at is not None:
                    logger.info("%s recovered; using it as primary again", self._primary_name)
                    self._primary_retry_at = None
                return result
            except LLMProviderError as e:
                self._primary_retry_at = time.monotonic() + self._cooldown_seconds
                logger.warning(
                    "%s generate() failed (%s); using %s for the next %.0fs",
                    self._primary_name, e, self._secondary_name, self._cooldown_seconds,
                )

        return await self._secondary.generate(
            prompt, system=system, max_tokens=max_tokens, temperature=temperature
        )

    def _primary_available(self) -> bool:
        return self._primary_retry_at is None or time.monotonic() >= self._primary_retry_at

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError(
            "FallbackLLMClient.embed() is intentionally not implemented — "
            "use CompositeLLMClient to route embed() to one dedicated "
            "embedding client instead."
        )
