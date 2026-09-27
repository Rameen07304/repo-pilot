"""
ExplainPullRequestUseCase — what a pull request changes structurally.

Paste a pull request URL, get the architecture delta between its two
ends. Everything it needs already existed and was pointed at the wrong
thing: the comparison, the rename resolution, the evidence, the page.
The only pieces missing were reading a pull request's two commits and
ingesting at a commit rather than at HEAD.

The comparison runs against the merge base GitHub reports, not the tip
of the base branch. Comparing against the tip would blame this pull
request for every unrelated commit that landed while it was open, which
is the failure mode that makes structural diffs useless on a busy repo.

Both ends have to be parsed, so an unseen pull request costs two
ingests. They are sequential on purpose: two at once would double this
client's GitHub rate-limit burn for no wall-clock win worth having, and
the second is usually most of a cache hit anyway.
"""
from __future__ import annotations

import logging
import re

from app.application.interfaces.github_client import GitHubClient, PullRequestRef
from app.application.use_cases.compare_architecture import CompareArchitectureUseCase
from app.application.use_cases.ingest_repo import IngestRepoUseCase
from app.domain.entities import ArchitectureDelta
from app.domain.value_objects import RepoUrl

logger = logging.getLogger(__name__)

# github.com/owner/repo/pull/123, with or without scheme, trailing path or
# query. Deliberately strict: this string decides which repository the
# server fetches, so it is parsed rather than pattern-matched loosely.
_PR_URL = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/"
    r"(?P<owner>[A-Za-z0-9._-]+)/(?P<repo>[A-Za-z0-9._-]+)"
    r"/pull/(?P<number>\d+)(?:[/?#].*)?$",
    re.IGNORECASE,
)


class NotAPullRequestUrlError(ValueError):
    """Raised when the text given is not a GitHub pull request URL."""


def parse_pull_request_url(raw: str) -> tuple[RepoUrl, int]:
    """
    Splits a pull request URL into the repository and the number.

    Returns a RepoUrl, so the SSRF guard in the domain layer runs on the
    way through exactly as it does for an ordinary ingest.
    """
    match = _PR_URL.match((raw or "").strip())
    if not match:
        raise NotAPullRequestUrlError(
            "That is not a GitHub pull request URL. It should look like "
            "https://github.com/owner/repo/pull/123"
        )
    owner, repo, number = match.group("owner"), match.group("repo"), match.group("number")
    return RepoUrl(f"https://github.com/{owner}/{repo}"), int(number)


class ExplainPullRequestUseCase:
    def __init__(
        self,
        *,
        github_client: GitHubClient,
        ingest_use_case: IngestRepoUseCase,
        compare_use_case: CompareArchitectureUseCase,
    ) -> None:
        self._github = github_client
        self._ingest = ingest_use_case
        self._compare = compare_use_case

    async def execute(self, *, pull_request_url: str) -> tuple[ArchitectureDelta, PullRequestRef]:
        repo, number = parse_pull_request_url(pull_request_url)

        # The private-repo guard runs inside ingest, but the pull request
        # metadata is fetched first, so it runs here too. Without it the
        # server's token would answer questions about a private pull
        # request for whoever pasted the link.
        await self._github.assert_public(repo)

        pull = await self._github.get_pull_request(repo, number)
        if pull.base_sha == pull.head_sha:
            raise NotAPullRequestUrlError(
                f"Pull request #{number} has the same commit at both ends, so there is "
                "nothing to compare."
            )

        for sha in (pull.base_sha, pull.head_sha):
            _manifest, was_cached = await self._ingest.execute(
                repo.normalized, commit_sha=sha
            )
            logger.info(
                "PR #%s: %s at %s", number, "cached" if was_cached else "ingested", sha[:7]
            )

        delta = await self._compare.execute(
            repo_full_name=repo.full_name,
            base_sha=pull.base_sha,
            head_sha=pull.head_sha,
        )
        return delta, pull
