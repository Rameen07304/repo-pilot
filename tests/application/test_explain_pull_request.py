"""
Pull requests, as a structural comparison.

Most of this is about the URL, because that string decides which
repository the server fetches on a public deployment. The rest is about
comparing against the right two commits: against the tip of the base
branch instead of the merge base, every unrelated commit landed while
the pull request was open would be blamed on it.
"""
import unittest

from app.application.interfaces.github_client import PullRequestRef
from app.application.use_cases.explain_pull_request import (
    ExplainPullRequestUseCase,
    NotAPullRequestUrlError,
    parse_pull_request_url,
)


class PullRequestUrlTest(unittest.TestCase):
    def test_the_shapes_people_actually_paste(self):
        cases = {
            "https://github.com/gorilla/mux/pull/816": ("gorilla/mux", 816),
            "github.com/owner/repo/pull/1": ("owner/repo", 1),
            "https://www.github.com/a-b/c.d/pull/42/files": ("a-b/c.d", 42),
            "https://github.com/o/r/pull/7?w=1": ("o/r", 7),
            "  https://github.com/o/r/pull/9#discussion  ": ("o/r", 9),
        }
        for url, (full_name, number) in cases.items():
            with self.subTest(url=url):
                repo, parsed = parse_pull_request_url(url)
                self.assertEqual(repo.full_name, full_name)
                self.assertEqual(parsed, number)

    def test_a_host_that_merely_contains_github_is_refused(self):
        # github.com.evil.com is the one that matters: a loose match sends
        # the server's token to somebody else's server.
        for url in (
            "https://github.com.evil.com/o/r/pull/1",
            "https://evil.com/o/r/pull/1",
            "https://notgithub.com/o/r/pull/1",
            "https://evil.com/github.com/o/r/pull/1",
        ):
            with self.subTest(url=url):
                with self.assertRaises(NotAPullRequestUrlError):
                    parse_pull_request_url(url)

    def test_things_that_are_not_pull_requests_are_refused(self):
        for url in (
            "https://github.com/owner/repo",
            "https://github.com/o/r/issues/3",
            "https://github.com/o/r/pull/abc",
            "https://github.com/o/r/pull/",
            "",
            "   ",
        ):
            with self.subTest(url=url):
                with self.assertRaises(NotAPullRequestUrlError):
                    parse_pull_request_url(url)


class FakeGitHub:
    def __init__(self, pull=None, public=True):
        self._pull = pull
        self._public = public
        self.asserted = []

    async def assert_public(self, repo):
        self.asserted.append(repo.full_name)
        if not self._public:
            raise ValueError(f"{repo.full_name} is private")

    async def get_pull_request(self, repo, number):
        return self._pull


class FakeIngest:
    def __init__(self):
        self.calls = []

    async def execute(self, repo_url_raw, *, commit_sha=None):
        self.calls.append(commit_sha)
        return (None, True)


class FakeCompare:
    def __init__(self):
        self.called_with = None

    async def execute(self, *, repo_full_name, base_sha, head_sha):
        self.called_with = (repo_full_name, base_sha, head_sha)
        return "delta"


def pull(**kwargs):
    defaults = dict(
        number=816, title="Do not panic", base_sha="base111", head_sha="head222",
        state="open", author="someone",
    )
    defaults.update(kwargs)
    return PullRequestRef(**defaults)


class ExplainPullRequestTest(unittest.IsolatedAsyncioTestCase):
    def build(self, *, pull_ref=None, public=True):
        self.github = FakeGitHub(pull_ref or pull(), public=public)
        self.ingest = FakeIngest()
        self.compare = FakeCompare()
        return ExplainPullRequestUseCase(
            github_client=self.github,
            ingest_use_case=self.ingest,
            compare_use_case=self.compare,
        )

    async def test_both_ends_are_ingested_and_then_compared(self):
        use_case = self.build()
        delta, ref = await use_case.execute(
            pull_request_url="https://github.com/gorilla/mux/pull/816"
        )
        self.assertEqual(delta, "delta")
        self.assertEqual(ref.number, 816)
        self.assertEqual(self.ingest.calls, ["base111", "head222"])
        self.assertEqual(self.compare.called_with, ("gorilla/mux", "base111", "head222"))

    async def test_it_compares_against_the_merge_base_github_reported(self):
        # Not the tip of the base branch: that would count every unrelated
        # commit landed while the pull request was open.
        use_case = self.build(pull_ref=pull(base_sha="mergebase"))
        await use_case.execute(pull_request_url="https://github.com/o/r/pull/3")
        self.assertEqual(self.compare.called_with[1], "mergebase")

    async def test_a_private_repo_is_refused_before_anything_is_fetched(self):
        use_case = self.build(public=False)
        with self.assertRaises(ValueError):
            await use_case.execute(pull_request_url="https://github.com/o/r/pull/3")
        self.assertEqual(self.ingest.calls, [], "nothing should be ingested")

    async def test_a_pull_request_with_one_commit_at_both_ends_is_refused(self):
        use_case = self.build(pull_ref=pull(base_sha="same", head_sha="same"))
        with self.assertRaises(NotAPullRequestUrlError):
            await use_case.execute(pull_request_url="https://github.com/o/r/pull/3")
        self.assertEqual(self.ingest.calls, [])

    async def test_a_bad_url_never_reaches_github(self):
        use_case = self.build()
        with self.assertRaises(NotAPullRequestUrlError):
            await use_case.execute(pull_request_url="https://evil.com/o/r/pull/1")
        self.assertEqual(self.github.asserted, [])


if __name__ == "__main__":
    unittest.main()
