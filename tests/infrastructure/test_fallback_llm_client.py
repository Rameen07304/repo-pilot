import asyncio
import unittest

from app.application.interfaces.llm_client import LLMProviderError
from app.infrastructure.llm import fallback_llm_client
from app.infrastructure.llm.fallback_llm_client import FallbackLLMClient


class CountingClient:
    def __init__(self, name: str, *, fail: bool = False):
        self.name = name
        self.fail = fail
        self.calls = 0

    async def generate(self, prompt, **kwargs):
        self.calls += 1
        if self.fail:
            raise LLMProviderError(self.name, "401 unauthenticated")
        return f"{self.name}: {prompt}"

    async def embed(self, texts):
        raise NotImplementedError


class FallbackCooldownTest(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self._real_monotonic = fallback_llm_client.time.monotonic
        fallback_llm_client.time.monotonic = lambda: self.now

    def tearDown(self):
        fallback_llm_client.time.monotonic = self._real_monotonic

    def test_failed_primary_is_skipped_during_cooldown(self):
        primary = CountingClient("gemini", fail=True)
        secondary = CountingClient("groq")
        client = FallbackLLMClient(primary=primary, secondary=secondary, primary_name="gemini", secondary_name="groq", cooldown_seconds=300)

        for _ in range(6):
            self.assertEqual(asyncio.run(client.generate("q")), "groq: q")

        self.assertEqual(primary.calls, 1)  # one failure opened the cooldown; the other five never touched it
        self.assertEqual(secondary.calls, 6)

    def test_primary_is_retried_after_cooldown_and_recovers(self):
        primary = CountingClient("gemini", fail=True)
        secondary = CountingClient("groq")
        client = FallbackLLMClient(primary=primary, secondary=secondary, primary_name="gemini", secondary_name="groq", cooldown_seconds=300)

        asyncio.run(client.generate("a"))
        self.now += 301
        primary.fail = False  # key fixed in the meantime
        self.assertEqual(asyncio.run(client.generate("b")), "gemini: b")
        self.assertEqual(asyncio.run(client.generate("c")), "gemini: c")
        self.assertEqual(primary.calls, 3)
        self.assertEqual(secondary.calls, 1)

    def test_healthy_primary_never_falls_back(self):
        primary = CountingClient("gemini")
        secondary = CountingClient("groq")
        client = FallbackLLMClient(primary=primary, secondary=secondary, primary_name="gemini", secondary_name="groq")
        self.assertEqual(asyncio.run(client.generate("q")), "gemini: q")
        self.assertEqual(secondary.calls, 0)

    def test_secondary_failure_still_raises(self):
        client = FallbackLLMClient(
            primary=CountingClient("gemini", fail=True), secondary=CountingClient("groq", fail=True),
            primary_name="gemini", secondary_name="groq",
        )
        with self.assertRaises(LLMProviderError):
            asyncio.run(client.generate("q"))


if __name__ == "__main__":
    unittest.main()
