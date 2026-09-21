"""
Contract for fetching repo data from GitHub. Concrete implementation
uses httpx + the GitHub REST API (Trees + Contents), never `git clone`.
"""
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.domain.value_objects import RepoUrl


@dataclass(frozen=True)
class RemoteFileMeta:
    """One entry from the repo tree — metadata only, no content yet."""
    path: str
    size_bytes: int
    sha: str
    is_binary_guess: bool  # based on extension, refined later if needed


@runtime_checkable
class GitHubClient(Protocol):
    async def get_latest_commit_sha(self, repo: RepoUrl) -> str:
        """
        Cheap call, used first: if this SHA matches what we already
        have cached, skip the rest of ingestion entirely.
        """
        ...

    async def get_repo_tree(self, repo: RepoUrl, *, commit_sha: str) -> list[RemoteFileMeta]:
        """
        Full file tree at a given commit. Implementations MUST apply
        the noise filters here (node_modules, vendor, dist, lockfiles,
        binaries) before returning — so nothing downstream even sees
        files we're never going to use.
        """
        ...

    async def get_file_content(self, repo: RepoUrl, *, path: str, sha: str) -> str:
        """
        Fetch one file's raw content by blob SHA. Implementations MUST
        enforce MAX_FILE_SIZE_BYTES here — reject before download
        completes, not after, to avoid wasting bandwidth/memory on a
        single oversized file.
        """
        ...

    async def get_file_contents(
        self, repo: RepoUrl, *, commit_sha: str, files: list[RemoteFileMeta]
    ) -> dict[str, str]:
        """
        Bulk fetch: path -> content for every file in `files` that could
        be retrieved. Files missing from the result failed; the caller
        decides what that means. Implementations are expected to do
        this in as few requests as possible (one archive download, not
        one call per file) so a large repo neither exhausts the API
        rate limit nor multiplies the effect of a flaky connection.
        The same secret redaction as get_file_content applies.
        """
        ...


class GitHubRateLimitError(Exception):
    """
    Raised when GitHub's rate limit is hit (60/hr unauthenticated,
    5000/hr with a token). Distinct from a generic failure so the
    use case can surface a clear "try again later" message instead of
    a generic error.
    """
    def __init__(self, retry_after_seconds: int | None = None):
        self.retry_after_seconds = retry_after_seconds
        super().__init__("GitHub API rate limit exceeded")


class GitHubRepoNotFoundError(Exception):
    """
    GitHub answered 404 for the repo: it does not exist, or it is
    private and we have no access. GitHub deliberately does not
    distinguish the two for unauthenticated callers.
    """
    def __init__(self, repo_full_name: str):
        self.repo_full_name = repo_full_name
        super().__init__(f"Repository {repo_full_name} was not found, or is private.")
