"""
Tries a primary LLMClient's generate() first; if it raises
LLMProviderError, automatically falls back to a secondary client.
Only applies to generate() — embeddings are never fallback-routed
(see CompositeLLMClient) since mixing embedding providers across
ingestions would break vector-space consistency.
"""
from __future__ import annotations

import logging

from app.application.interfaces.llm_client import LLMClient, LLMProviderError

logger = logging.getLogger(__name__)


class FallbackLLMClient(LLMClient):
    def __init__(self, *, primary: LLMClient, secondary: LLMClient, primary_name: str, secondary_name: str) -> None:
        self._primary = primary
        self._secondary = secondary
        self._primary_name = primary_name
        self._secondary_name = secondary_name

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> str:
        try:
            return await self._primary.generate(
                prompt, system=system, max_tokens=max_tokens, temperature=temperature
            )
        except LLMProviderError as e:
            logger.warning(
                "%s generate() failed (%s), falling back to %s",
                self._primary_name, e, self._secondary_name,
            )
            return await self._secondary.generate(
                prompt, system=system, max_tokens=max_tokens, temperature=temperature
            )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError(
            "FallbackLLMClient.embed() is intentionally not implemented — "
            "use CompositeLLMClient to route embed() to one dedicated "
            "embedding client instead."
        )