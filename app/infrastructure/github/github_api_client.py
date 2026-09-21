"""
Concrete GitHubClient implementation - talks to the real GitHub REST
API via httpx. Uses the Trees API (one call gets the whole file list)
and the tarball endpoint for file bodies (one download for the whole
repo, read in memory), falling back to the Blobs API per file only for
anything the archive did not cover. Never uses git clone - no disk
I/O, no cloning noise, works fine on an ephemeral-storage container.

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
import io
import logging
import tarfile

import httpx

from app.application.interfaces.github_client import (
    GitHubClient,
    GitHubRateLimitError,
    GitHubRepoNotFoundError,
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
    # Flutter/Dart: platform-specific scaffolding, hundreds of files
    # of pure boilerplate (Gradle configs, Xcode project files,
    # generated plugin registrants) with zero relevance to the app's
    # actual logic. Confirmed as the cause of very slow ingestion on
    # Flutter repos — these folders alone can be 200+ files.
    "android/", "ios/", "windows/", "macos/", "linux/", "web/",
    ".dart_tool/", ".pub-cache/", ".pub/",
}

_NOISE_FILENAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "poetry.lock", "Pipfile.lock", "Cargo.lock",
    "pubspec.lock",
}


class GitHubApiClient(GitHubClient):
    def __init__(
        self,
        *,
        token: str | None = None,
        timeout_seconds: float = 15.0,
        max_archive_bytes: int = 150 * 1024 * 1024,
        max_concurrent_fetches: int = 10,
    ) -> None:
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._headers = headers
        self._timeout = timeout_seconds
        # The tarball holds the WHOLE repo, including binaries and vendored
        # dirs we never look at; past this size we stop and fall back to
        # per-file fetching rather than hold an arbitrarily large archive.
        self._max_archive_bytes = max_archive_bytes
        self._max_concurrent_fetches = max_concurrent_fetches

    async def get_latest_commit_sha(self, repo: RepoUrl) -> str:
        url = f"{_GITHUB_API_BASE}/repos/{repo.owner}/{repo.repo}/commits"
        try:
            data = await self._get_json_with_retry(url, params={"per_page": 1})
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise GitHubRepoNotFoundError(repo.full_name) from e
            raise
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

        return self._finalize_content(base64.b64decode(data["content"]), path)

    async def get_file_contents(
        self, repo: RepoUrl, *, commit_sha: str, files: list[RemoteFileMeta]
    ) -> dict[str, str]:
        wanted = {f.path: f for f in files}
        contents: dict[str, str] = {}

        try:
            archive = await self._download_tarball(repo, commit_sha)
        except GitHubRateLimitError:
            raise
        except Exception as e:
            logger.warning("Tarball download failed, falling back to per-file fetch: %s", e)
            archive = None

        if archive is not None:
            contents = self._extract_wanted(archive, wanted)
            logger.info(
                "Read %d/%d files from one tarball download", len(contents), len(wanted)
            )

        missing = [wanted[p] for p in wanted if p not in contents]
        if missing:
            contents.update(await self._fetch_individually(repo, missing))
        return contents

    async def _download_tarball(self, repo: RepoUrl, commit_sha: str) -> bytes | None:
        """
        One API request (which counts one rate-limit unit) that 302s to
        codeload; the redirect is followed by hand so the rate-limit
        headers on the API response are still checked. Returns None if
        the archive exceeds the size cap.
        """
        url = f"{_GITHUB_API_BASE}/repos/{repo.owner}/{repo.repo}/tarball/{commit_sha}"
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(self._timeout, read=60.0)) as client:
                    resp = await client.get(url, headers=self._headers, follow_redirects=False)
                    self._raise_for_rate_limit(resp)
                    target = resp.headers.get("location")
                    if resp.is_redirect and target:
                        # codeload needs no auth for public repos; don't forward the token off-origin
                        async with client.stream("GET", target, follow_redirects=True) as stream:
                            stream.raise_for_status()
                            return await self._read_capped(stream)
                    resp.raise_for_status()
                    return resp.content if len(resp.content) <= self._max_archive_bytes else None
            except (httpx.ReadError, httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
                last_error = e
                if attempt < 2:
                    wait_seconds = 1.5 * (attempt + 1)
                    logger.warning(
                        "Transient network error downloading tarball (attempt %d/3), retrying in %.1fs: %s",
                        attempt + 1, wait_seconds, e,
                    )
                    await asyncio.sleep(wait_seconds)
                    continue
                raise
        raise last_error  # pragma: no cover - loop always returns or raises

    async def _read_capped(self, stream: httpx.Response) -> bytes | None:
        buf = io.BytesIO()
        async for chunk in stream.aiter_bytes():
            buf.write(chunk)
            if buf.tell() > self._max_archive_bytes:
                logger.warning(
                    "Tarball exceeded %d bytes, abandoning it in favour of per-file fetch",
                    self._max_archive_bytes,
                )
                return None
        return buf.getvalue()

    def _extract_wanted(self, archive: bytes, wanted: dict[str, RemoteFileMeta]) -> dict[str, str]:
        contents: dict[str, str] = {}
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            for member in tar:
                if not member.isfile():
                    continue
                # Members are prefixed with "<owner>-<repo>-<sha7>/"
                path = member.name.split("/", 1)[1] if "/" in member.name else member.name
                meta = wanted.get(path)
                if meta is None:
                    continue
                if member.size > meta.size_bytes * 2 + 4096:
                    logger.warning("Skipping %s: archive size %d far exceeds tree size %d", path, member.size, meta.size_bytes)
                    continue
                extracted = tar.extractfile(member)
                if extracted is None:
                    continue
                try:
                    contents[path] = self._finalize_content(extracted.read(), path)
                except ValueError as e:
                    logger.warning("Skipping %s: %s", path, e)
        return contents

    async def _fetch_individually(self, repo: RepoUrl, files: list[RemoteFileMeta]) -> dict[str, str]:
        """Per-blob fallback with bounded concurrency; one transient failure never fails the batch."""
        semaphore = asyncio.Semaphore(self._max_concurrent_fetches)

        async def fetch_one(meta: RemoteFileMeta) -> tuple[str, str]:
            async with semaphore:
                return meta.path, await self.get_file_content(repo, path=meta.path, sha=meta.sha)

        results = await asyncio.gather(*[fetch_one(m) for m in files], return_exceptions=True)
        contents: dict[str, str] = {}
        for result in results:
            if isinstance(result, GitHubRateLimitError):
                raise result
            if isinstance(result, Exception):
                logger.warning("Skipping one file due to fetch error: %s", result)
                continue
            path, content = result
            contents[path] = content
        return contents

    def _finalize_content(self, raw_bytes: bytes, path: str) -> str:
        content = self._decode_text(raw_bytes, path)

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
        if filename in _NOISE_FILENAMES:
            return True
        # Dart's code-generation output (json_serializable, freezed,
        # etc) — pure generated boilerplate, not hand-written logic.
        if filename.endswith((".g.dart", ".freezed.dart", ".gr.dart")):
            return True
        return False

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
    @staticmethod
    def _decode_text(raw_bytes: bytes, path: str) -> str:
        """
        Tries a few common encodings before giving up. Plain UTF-8 first
        (the common case), then UTF-8 with BOM (common from Windows
        editors), then UTF-16 (also common from Windows tools saving
        "Unicode" text). Only raises if none of these work — genuinely
        binary content correctly still gets skipped.
        """
        for encoding in ("utf-8", "utf-8-sig", "utf-16"):
            try:
                return raw_bytes.decode(encoding)
            except (UnicodeDecodeError, UnicodeError):
                continue
        raise ValueError(f"File {path} is not decodable as UTF-8, UTF-8-BOM, or UTF-16, skipping")
