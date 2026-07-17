"""
Concrete LLMClient implementation using Gemini's free tier via the
new `google-genai` SDK (the old `google-generativeai` package is
deprecated and receives no further updates/fixes).

Security/reliability responsibilities this class owns:
  - Never lets raw exceptions escape - everything gets wrapped in our
    own LLMProviderError so use cases only ever handle ONE exception
    type, regardless of which provider is underneath.
  - Distinguishes quota-exceeded errors specifically (is_quota_exceeded
    flag) so callers can react differently (e.g. fall back to Groq,
    or show the "at capacity" message) vs a generic transient failure.
  - Enforces a request timeout - a hung call to a free-tier API
    should never hang our whole ingestion pipeline.
  - Batches embedding calls (many texts per API request) instead of
    one call per chunk, to stay well inside free-tier quota limits.
  - Retries on BOTH quota errors (429/rate-limit) AND transient
    network errors (dropped connections, server disconnects, timeouts)
    - these are two different failure classes and both need handling,
    since a real network occasionally drops a connection mid-request
    regardless of quota status.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

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
        embedding_model_name: str = "models/gemini-embedding-001",
        request_timeout_seconds: float = 30.0,
        embed_batch_size: int = 20,
    ) -> None:
        if not api_key:
            raise ValueError("Gemini API key is required")

        self._client = genai.Client(api_key=api_key)
        self._model_name = model_name
        self._embedding_model_name = embedding_model_name
        self._timeout = request_timeout_seconds
        # How many texts go into ONE embed_content API call. Higher =
        # fewer total API calls = less quota consumed.
        self._embed_batch_size = embed_batch_size

    # ------------------------------------------------------------------
    # generate()
    # ------------------------------------------------------------------

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> str:
        last_error: Exception | None = None
        for attempt in range(2):  # one retry for transient network errors
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(self._generate_sync, prompt, system, max_tokens, temperature),
                    timeout=self._timeout,
                )
                return result
            except asyncio.TimeoutError as e:
                last_error = e
                raise LLMProviderError("gemini", "Request timed out") from e
            except Exception as e:
                last_error = e
                if self._looks_like_transient_error(e) and attempt == 0:
                    logger.warning("generate() hit a transient error, retrying once: %s", e)
                    await asyncio.sleep(2.0)
                    continue
                is_quota = self._looks_like_quota_error(e)
                raise LLMProviderError("gemini", str(e), is_quota_exceeded=is_quota) from e
        is_quota = self._looks_like_quota_error(last_error)
        raise LLMProviderError("gemini", str(last_error), is_quota_exceeded=is_quota) from last_error

    def _generate_sync(
        self, prompt: str, system: str | None, max_tokens: int, temperature: float
    ) -> str:
        config = genai_types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
            system_instruction=system,  # kept separate from prompt - our
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

    # ------------------------------------------------------------------
    # embed() - BATCHED
    # ------------------------------------------------------------------

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        num_batches = (len(texts) + self._embed_batch_size - 1) // self._embed_batch_size
        batch_timeout = max(self._timeout, num_batches * 20.0 + 30.0)

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._embed_sync, texts),
                timeout=batch_timeout,
            )
        except asyncio.TimeoutError as e:
            raise LLMProviderError("gemini", "Embedding request timed out") from e
        except Exception as e:
            is_quota = self._looks_like_quota_error(e)
            raise LLMProviderError("gemini", str(e), is_quota_exceeded=is_quota) from e

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        embeddings: list[list[float]] = []
        batch_size = self._embed_batch_size

        calls_per_minute_budget = 25
        min_interval_seconds = 60.0 / calls_per_minute_budget

        for batch_start in range(0, len(texts), batch_size):
            batch = texts[batch_start: batch_start + batch_size]
            start = time.monotonic()

            batch_embeddings = self._embed_batch_with_retry(batch)
            embeddings.extend(batch_embeddings)

            elapsed = time.monotonic() - start
            remaining_pause = min_interval_seconds - elapsed
            is_last_batch = batch_start + batch_size >= len(texts)
            if remaining_pause > 0 and not is_last_batch:
                time.sleep(remaining_pause)

        return embeddings

    def _embed_batch_with_retry(self, batch: list[str], max_retries: int = 3) -> list[list[float]]:
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                result = self._client.models.embed_content(
                    model=self._embedding_model_name,
                    contents=batch,  # list in, list of embeddings out - ONE API call
                )
                return [e.values for e in result.embeddings]
            except Exception as e:
                last_error = e
                is_retryable = self._looks_like_quota_error(e) or self._looks_like_transient_error(e)
                if is_retryable and attempt < max_retries - 1:
                    retry_delay = self._extract_retry_delay(e) or (2 ** attempt * 5)
                    logger.warning(
                        "Embed batch failed (%s), retrying in %.1fs (attempt %d/%d)",
                        type(e).__name__, retry_delay, attempt + 1, max_retries,
                    )
                    time.sleep(retry_delay)
                    continue
                raise
        raise last_error  # exhausted retries

    @staticmethod
    def _extract_retry_delay(e: Exception) -> float | None:
        """
        Google's 429 responses often include a suggested retry delay
        in the error message (e.g. 'Please retry in 23.1s') - use it
        when present instead of guessing. Caps at 90s.
        """
        match = re.search(r"retry in (\d+(?:\.\d+)?)s", str(e))
        if match:
            return min(float(match.group(1)) + 1.0, 90.0)
        return None

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _looks_like_quota_error(e: Exception) -> bool:
        message = str(e).lower()
        return any(
            marker in message
            for marker in ("quota", "rate limit", "resource_exhausted", "429")
        )

    @staticmethod
    def _looks_like_transient_error(e: Exception) -> bool:
        """
        Catches transient network-level failures (dropped connections,
        server disconnects, timeouts) that are NOT quota-related but
        are still worth retrying - real networks occasionally drop a
        connection mid-request regardless of provider or quota status.
        """
        message = str(e).lower()
        return any(
            marker in message
            for marker in (
                "disconnected",
                "connection",
                "timeout",
                "timed out",
                "reset",
                "unavailable",
                "503",
                "502",
            )
        )