"""
The handbook use case gathers; it does not analyse. What matters here is
which failures it survives and which it refuses to paper over.
"""
import unittest

from app.application.use_cases.analyze_code_health import RepoNotIndexedError
from app.application.use_cases.generate_handbook import GenerateHandbookUseCase
from app.application.use_cases.generate_onboarding import RepoNotIngestedError
from app.domain.entities import CodeHealthReport, OnboardingGuide, RepoManifest
from app.domain.value_objects import RepoUrl


def manifest(**kwargs):
    defaults = dict(
        repo_url=RepoUrl("https://github.com/acme/widgets"),
        commit_sha="abc123",
        files=[],
        detected_stack=["Python"],
        skipped_file_count=0,
    )
    defaults.update(kwargs)
    return RepoManifest(**defaults)


class StubCache:
    def __init__(self, value):
        self._value = value

    async def get(self, repo_full_name, commit_sha):
        return self._value


class StubOnboarding:
    def __init__(self, guide=None, raises=None):
        self._guide = guide
        self._raises = raises
        self.calls = 0

    async def execute(self, *, repo_full_name, commit_sha):
        self.calls += 1
        if self._raises:
            raise self._raises
        return self._guide


class StubHealth:
    def __init__(self, report=None, raises=None):
        self._report = report
        self._raises = raises

    async def execute(self, *, repo_full_name, commit_sha):
        if self._raises:
            raise self._raises
        return self._report


def build(*, cache_value="default", onboarding=None, health=None):
    if cache_value == "default":
        cache_value = manifest()
    return GenerateHandbookUseCase(
        onboarding_use_case=onboarding or StubOnboarding(guide=OnboardingGuide(
            repo_full_name="acme/widgets", commit_sha="abc123")),
        code_health_use_case=health or StubHealth(report=CodeHealthReport(
            repo_full_name="acme/widgets", commit_sha="abc123", score=90, grade="A")),
        cache=StubCache(cache_value),
    )


class GenerateHandbookTest(unittest.IsolatedAsyncioTestCase):
    async def test_gathers_manifest_facts_rather_than_recomputing_them(self):
        use_case = build(cache_value=manifest(detected_stack=["Go", "Rust"], skipped_file_count=4))
        doc = await use_case.execute(repo_full_name="acme/widgets", commit_sha="abc123")
        self.assertEqual(doc.detected_stack, ["Go", "Rust"])
        self.assertEqual(doc.skipped_file_count, 4)

    async def test_refuses_when_the_repo_was_never_ingested(self):
        use_case = build(cache_value=None)
        with self.assertRaises(RepoNotIngestedError):
            await use_case.execute(repo_full_name="acme/widgets", commit_sha="abc123")

    async def test_does_not_call_the_onboarding_agent_for_an_uningested_repo(self):
        onboarding = StubOnboarding(guide=OnboardingGuide(repo_full_name="a/b", commit_sha="x"))
        use_case = build(cache_value=None, onboarding=onboarding)
        with self.assertRaises(RepoNotIngestedError):
            await use_case.execute(repo_full_name="acme/widgets", commit_sha="abc123")
        self.assertEqual(onboarding.calls, 0, "should fail before spending an LLM call")

    async def test_a_failed_reading_order_loses_the_handbook(self):
        # Without a reading order there is no handbook, so this propagates.
        use_case = build(onboarding=StubOnboarding(raises=RepoNotIngestedError("gone")))
        with self.assertRaises(RepoNotIngestedError):
            await use_case.execute(repo_full_name="acme/widgets", commit_sha="abc123")

    async def test_a_missing_parsed_index_only_loses_the_health_section(self):
        use_case = build(health=StubHealth(raises=RepoNotIndexedError("no index")))
        doc = await use_case.execute(repo_full_name="acme/widgets", commit_sha="abc123")
        self.assertIsNone(doc.health)
        self.assertIsNotNone(doc.guide)

    async def test_an_unexpected_health_failure_also_leaves_the_document_standing(self):
        use_case = build(health=StubHealth(raises=RuntimeError("boom")))
        doc = await use_case.execute(repo_full_name="acme/widgets", commit_sha="abc123")
        self.assertIsNone(doc.health)
        self.assertIsNotNone(doc.guide)
