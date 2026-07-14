"""
Concrete LLMClient implementation using Gemini's free tier via the
new `google-genai` SDK (the old `google-generativeai` package is
deprecated and receives no further updates/fixes).

Security/reliability responsibilities this class owns:
  - Never lets raw exceptions escape — everything gets wrapped in our
    own LLMProviderError so use cases only ever handle ONE exception
    type, regardless of which provider is underneath.
  - Distinguishes quota-exceeded errors specifically (is_quota_exceeded
    flag) so callers can react differently (e.g. fall back to Groq,
    or show the "at capacity" message) vs a generic transient failure.
  - Enforces a request timeout — a hung call to a free-tier API
    should never hang our whole ingestion pipeline.
"""
from __future__ import annotations

import asyncio
import logging

from google import genai
from google.genai import types as genai_types

from app.application.interfaces.llm_client import LLMClient, LLMProviderError

logger = logging.getLogger(__name__)


class GeminiClient(LLMClient):
    def __init__(
        self,
        *,
        api_key: str,
        model_name: str = "gemini-2.0-flash",
        embedding_model_name: str = "text-embedding-004",
        request_timeout_seconds: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError("Gemini API key is required")

        self._client = genai.Client(api_key=api_key)
        self._model_name = model_name
        self._embedding_model_name = embedding_model_name
        self._timeout = request_timeout_seconds

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> str:
        # The genai SDK's client calls are sync under the hood — run in
        # a thread so we don't block the event loop, and keep our
        # LLMClient interface honestly async.
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(self._generate_sync, prompt, system, max_tokens, temperature),
                timeout=self._timeout,
            )
            return result
        except asyncio.TimeoutError as e:
            raise LLMProviderError("gemini", "Request timed out") from e
        except Exception as e:
            is_quota = self._looks_like_quota_error(e)
            raise LLMProviderError("gemini", str(e), is_quota_exceeded=is_quota) from e

    def _generate_sync(
        self, prompt: str, system: str | None, max_tokens: int, temperature: float
    ) -> str:
        config = genai_types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
            system_instruction=system,  # kept separate from prompt — our
            # prompt-injection guard: untrusted repo content goes in
            # `prompt`, never merged into this instruction field.
        )

        response = self._client.models.generate_content(
            model=self._model_name,
            contents=prompt,
            config=config,
        )

        text = getattr(response, "text", None)
        if not text or not text.strip():
            raise ValueError("Gemini returned empty text (possibly blocked by safety filters)")

        return text.strip()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._embed_sync, texts),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError as e:
            raise LLMProviderError("gemini", "Embedding request timed out") from e
        except Exception as e:
            is_quota = self._looks_like_quota_error(e)
            raise LLMProviderError("gemini", str(e), is_quota_exceeded=is_quota) from e

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        embeddings: list[list[float]] = []
        for text in texts:
            result = self._client.models.embed_content(
                model=self._embedding_model_name,
                contents=text,
            )
            embeddings.append(result.embeddings[0].values)
        return embeddings

    @staticmethod
    def _looks_like_quota_error(e: Exception) -> bool:
        message = str(e).lower()
        return any(
            marker in message
            for marker in ("quota", "rate limit", "resource_exhausted", "429")
        )