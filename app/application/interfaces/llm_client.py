"""
Contract for any LLM provider (Gemini, Groq, Ollama, whatever comes next).

Use cases depend ONLY on this Protocol — never on a concrete class like
GeminiClient. That's what lets us swap or add fallback providers without
touching a single line in application/use_cases/.
"""
from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> str:
        """
        Send a prompt, get text back.

        Security note for every implementation: `prompt` may contain
        untrusted repo content (README text, code comments). Callers
        are responsible for keeping untrusted content in clearly
        delimited data sections of the prompt, never concatenated
        into `system`. This method itself does not sanitize — it's a
        thin transport, sanitization is the use case's job.
        """
        ...

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """
        Return one embedding vector per input text, same order.
        Used during ingestion (embed code chunks) and at query time
        (embed the user's question) so both sides live in the same
        vector space.
        """
        ...


class LLMProviderError(Exception):
    """
    Raised by any LLMClient implementation on failure (timeout, quota
    exceeded, malformed response, etc). Use cases catch this ONE
    exception type regardless of which provider is underneath —
    they never need to know if it was Gemini or Groq that failed.
    """
    def __init__(self, provider: str, message: str, *, is_quota_exceeded: bool = False):
        self.provider = provider
        self.is_quota_exceeded = is_quota_exceeded
        super().__init__(f"[{provider}] {message}")