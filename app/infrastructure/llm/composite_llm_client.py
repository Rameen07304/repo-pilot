"""
Routes generate() and embed() to two DIFFERENT underlying providers,
while still satisfying the single LLMClient Protocol every use case
already depends on.
"""
from __future__ import annotations

from app.application.interfaces.llm_client import LLMClient


class CompositeLLMClient(LLMClient):
    def __init__(self, *, generate_client: LLMClient, embed_client: LLMClient) -> None:
        self._generate_client = generate_client
        self._embed_client = embed_client

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> str:
        return await self._generate_client.generate(
            prompt, system=system, max_tokens=max_tokens, temperature=temperature
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self._embed_client.embed(texts)
