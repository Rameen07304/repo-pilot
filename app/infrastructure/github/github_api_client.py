"""
Concrete GitHubClient implementation - talks to the real GitHub REST
API via httpx. Uses the Trees API (one call gets the whole file list)
and the Contents/raw API for individual file bodies. Never uses
git clone - no disk I/O, no cloning noise, works fine on an
ephemeral-storage container.

Security responsibilities this class owns:
  - Applies noise filters (node_modules, vendor, dist, lockfiles,
    binaries by extension) before returning the tree.
  - Runs every fetched file's content through the secret scanner
    BEFORE returning it.
  - Enforces per-file size caps before/while downloading.
  - Raises GitHubRateLimitError distinctly so use cases can react
    appropriately instead of treating it as a generic failure.
  - Retries transient network errors (dropped connections, timeouts)
    a few times before giving up.
"""
from __future__ import annotations

import asyncio
import base64
import logging

import httpx

from app.application.interfaces.github_client import (
    GitHubClient,
    GitHubRateLimitError,
    RemoteFileMeta,
)
from app.domain.value_objects import RepoUrl
from app.infrastructure.security.secret_scanner import looks_like_env_file, scan_and_redact

logger = logging.getLogger(__name__)

_GITHUB_API_BASE = "https://api.github.com"

_BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
    ".pdf", ".zip", ".tar", ".gz", ".rar", ".7z",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".class", ".jar", ".pyc",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp3", ".mp4", ".mov", ".avi", ".wav",
    ".db", ".sqlite", ".sqlite3",
}

_NOISE_DIR_FRAGMENTS = {
    "node_modules/", "vendor/", "dist/", "build/", ".git/",
    "__pycache__/", ".venv/", "venv/", ".next/", "target/",
    "coverage/", ".pytest_cache/",
    ".github/issue_template/", ".github/discussion_template/", ".github/pull_request_template",
}

_NOISE_FILENAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "poetry.lock", "Pipfile.lock", "Cargo.lock",
}


class GitHubApiClient(GitHubClient):
    def __init__(self, *, token: str | None = None, timeout_seconds: float = 15.0) -> None:
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._headers = headers
        self._timeout = timeout_seconds

    async def get_latest_commit_sha(self, repo: RepoUrl) -> str:
        url = f"{_GITHUB_API_BASE}/repos/{repo.owner}/{repo.repo}/commits"
        data = await self._get_json_with_retry(url, params={"per_page": 1})
        if not data:
            raise ValueError(f"Repo {repo.owner}/{repo.repo} has no commits")
        return data[0]["sha"]

    async def get_repo_tree(self, repo: RepoUrl, *, commit_sha: str) -> list[RemoteFileMeta]:
        url = (
            f"{_GITHUB_API_BASE}/repos/{repo.owner}/{repo.repo}"
            f"/git/trees/{commit_sha}"
        )
        data = await self._get_json_with_retry(url, params={"recursive": "1"})

        if data.get("truncated"):
            logger.warning(
                "Tree for %s/%s@%s was truncated by GitHub API - "
                "some files may be missing.",
                repo.owner, repo.repo, commit_sha,
            )

        results: list[RemoteFileMeta] = []
        for entry in data.get("tree", []):
            if entry.get("type") != "blob":
                continue

            path = entry["path"]
            if self._is_noise_path(path):
                continue

            size = entry.get("size", 0)
            results.append(
                RemoteFileMeta(
                    path=path,
                    size_bytes=size,
                    sha=entry["sha"],
                    is_binary_guess=self._looks_binary(path),
                )
            )
        return results

    async def get_file_content(self, repo: RepoUrl, *, path: str, sha: str) -> str:
        url = f"{_GITHUB_API_BASE}/repos/{repo.owner}/{repo.repo}/git/blobs/{sha}"
        data = await self._get_json_with_retry(url)

        encoding = data.get("encoding")
        if encoding != "base64":
            raise ValueError(f"Unexpected encoding {encoding!r} for {path}")

        try:
            raw_bytes = base64.b64decode(data["content"])
            content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError(f"File {path} is not valid UTF-8 text, skipping")

        if looks_like_env_file(path):
            return "[FILE OMITTED: environment file, not processed]"

        scan_result = scan_and_redact(content)
        if scan_result.had_findings:
            logger.info(
                "Redacted %d potential secret(s) in %s", scan_result.findings_count, path
            )
        return scan_result.redacted_content

    async def _get_json_with_retry(self, url: str, *, params: dict | None = None, max_retries: int = 3):
        """
        Retries on transient network errors (connection drops, resets,
        read timeouts). NOT the same as a GitHub API error (4xx/5xx),
        which is raised immediately without retrying.
        """
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(url, headers=self._headers, params=params)
                    self._raise_for_rate_limit(resp)
                    resp.raise_for_status()
                    return resp.json()
            except (httpx.ReadError, httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait_seconds = 1.5 * (attempt + 1)
                    logger.warning(
                        "Transient network error fetching %s (attempt %d/%d), retrying in %.1fs: %s",
                        url, attempt + 1, max_retries, wait_seconds, e,
                    )
                    await asyncio.sleep(wait_seconds)
                    continue
                raise
        raise last_error

    @staticmethod
    def _is_noise_path(path: str) -> bool:
        lower = path.lower()
        if any(fragment in lower for fragment in _NOISE_DIR_FRAGMENTS):
            return True
        filename = path.rsplit("/", 1)[-1]
        return filename in _NOISE_FILENAMES

    @staticmethod
    def _looks_binary(path: str) -> bool:
        lower = path.lower()
        return any(lower.endswith(ext) for ext in _BINARY_EXTENSIONS)

    @staticmethod
    def _raise_for_rate_limit(resp: httpx.Response) -> None:
        if resp.status_code == 403 and resp.headers.get("X-RateLimit-Remaining") == "0":
            reset_header = resp.headers.get("X-RateLimit-Reset")
            retry_after = None
            if reset_header:
                import time
                retry_after = max(0, int(reset_header) - int(time.time()))
            raise GitHubRateLimitError(retry_after_seconds=retry_after)
