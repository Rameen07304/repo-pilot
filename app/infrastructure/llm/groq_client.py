"""
Concrete LLMClient implementation using Groq's API for text generation
ONLY. Groq has no embeddings endpoint at all — embed() intentionally
raises, since this class is meant to be used inside CompositeLLMClient
alongside a real embedding provider, never called for embed() directly.
"""
from __future__ import annotations

import asyncio
import logging

from groq import Groq

from app.application.interfaces.llm_client import LLMClient, LLMProviderError

logger = logging.getLogger(__name__)


class GroqClient(LLMClient):
    def __init__(
        self,
        *,
        api_key: str,
        model_name: str = "openai/gpt-oss-120b",
        request_timeout_seconds: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError("Groq API key is required")
        # The transport gets the timeout, not just the await.
        #
        # asyncio.wait_for around asyncio.to_thread only stops WAITING: the
        # sync SDK call keeps running in its worker thread to completion and
        # its result is discarded. Those threads come from the default
        # executor, which SQLite writes also use, so a slow provider could
        # saturate the pool and stall unrelated work. Giving the client its
        # own request timeout means the HTTP call is actually aborted and
        # the thread ends. wait_for stays as a backstop, set slightly
        # higher so the transport is what normally fires.
        self._client = Groq(api_key=api_key, timeout=request_timeout_seconds)
        self._model_name = model_name
        # Backstop only; the transport timeout above should fire first.
        self._timeout = request_timeout_seconds + 5.0

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> str:
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(self._generate_sync, prompt, system, max_tokens, temperature),
                timeout=self._timeout,
            )
            return result
        except asyncio.TimeoutError as e:
            raise LLMProviderError("groq", "Request timed out") from e
        except Exception as e:
            is_quota = self._looks_like_quota_error(e)
            raise LLMProviderError("groq", str(e), is_quota_exceeded=is_quota) from e

    def _generate_sync(
        self, prompt: str, system: str | None, max_tokens: int, temperature: float
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = self._client.chat.completions.create(
            model=self._model_name,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        text = response.choices[0].message.content
        if not text or not text.strip():
            raise ValueError("Groq returned empty text")
        return text.strip()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError(
            "GroqClient does not support embeddings — Groq has no embedding "
            "endpoint. Use this class only inside CompositeLLMClient paired "
            "with a real embedding provider (e.g. local sentence-transformers)."
        )

    @staticmethod
    def _looks_like_quota_error(e: Exception) -> bool:
        message = str(e).lower()
        return any(marker in message for marker in ("rate limit", "quota", "429"))
