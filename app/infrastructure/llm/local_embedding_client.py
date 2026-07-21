"""
Local embedding client using sentence-transformers — runs entirely on
this machine's CPU, zero API calls, zero dependency on any external
provider. This is what keeps embed() permanently immune to any
Gemini/Groq outage, deliberately never fallback-routed.
"""
from __future__ import annotations

import asyncio
import logging

from sentence_transformers import SentenceTransformer

from app.application.interfaces.llm_client import LLMClient, LLMProviderError

logger = logging.getLogger(__name__)


class LocalEmbeddingClient(LLMClient):
    def __init__(self, *, model_name: str = "all-MiniLM-L6-v2") -> None:
        logger.info("Loading local embedding model '%s' (one-time cost)...", model_name)
        self._model = SentenceTransformer(model_name)
        logger.info("Local embedding model ready.")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            return await asyncio.to_thread(self._encode_sync, texts)
        except Exception as e:
            raise LLMProviderError("local-embeddings", str(e)) from e

    def _encode_sync(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, show_progress_bar=False)
        return vectors.tolist()

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> str:
        raise NotImplementedError(
            "LocalEmbeddingClient does not generate text — pair it with a "
            "real text-generation provider inside CompositeLLMClient."
        )