"""
GitHubApiClient.get_file_contents — the single-archive path and its
per-file fallback. No network: the tarball download and the per-blob
fetch are stubbed at the method boundary.
"""
import asyncio
import io
import tarfile
import unittest
from unittest.mock import patch

import httpx

from app.application.interfaces.github_client import (
    GitHubRateLimitError,
    GitHubRepoNotFoundError,
    RemoteFileMeta,
    RepoIsPrivateError,
)
from app.domain.value_objects import RepoUrl
from app.infrastructure.github.github_api_client import GitHubApiClient

REPO = RepoUrl("https://github.com/acme/widgets")


def make_tarball(files: dict[str, bytes], prefix="acme-widgets-1ea2fdb") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(f"{prefix}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def meta(path: str, size: int) -> RemoteFileMeta:
    return RemoteFileMeta(path=path, size_bytes=size, sha="x" * 40, is_binary_guess=False)


class TarballFetchTest(unittest.TestCase):
    def setUp(self):
        self.client = GitHubApiClient(token=None)
        self.blob_calls: list[str] = []

        async def fake_blob(repo, *, path, sha):
            self.blob_calls.append(path)
            if path == "app/broken.py":
                raise ConnectionError("dns")
            return f"# blob {path}"

        self.client.get_file_content = fake_blob

    def run_fetch(self, files, tarball):
        async def fake_download(repo, sha):
            if isinstance(tarball, Exception):
                raise tarball
            return tarball

        self.client._download_tarball = fake_download
        self._last = asyncio.run(
            self.client.get_file_contents(REPO, commit_sha="1ea2fdb", files=files)
        )
        # The existing tests assert on the contents; not_text is reachable
        # through _last for the ones that care which is which.
        return self._last.contents

    def test_reads_everything_from_one_archive_with_no_blob_calls(self):
        tarball = make_tarball({"app/main.py": b"print(1)\n", "README.md": b"# hi\n", "img.png": b"\x89PNG"})
        files = [meta("app/main.py", 9), meta("README.md", 5)]
        out = self.run_fetch(files, tarball)
        self.assertEqual(out, {"app/main.py": "print(1)\n", "README.md": "# hi\n"})
        self.assertEqual(self.blob_calls, [])

    def test_secret_redaction_applies_to_archive_content(self):
        tarball = make_tarball({"cfg.py": b'TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"\n', ".env": b"SECRET=1\n"})
        out = self.run_fetch([meta("cfg.py", 60), meta(".env", 9)], tarball)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz0123456789", out["cfg.py"])
        self.assertIn("FILE OMITTED", out[".env"])

    def test_files_missing_from_archive_fall_back_to_blob_fetch(self):
        tarball = make_tarball({"app/main.py": b"x = 1\n"})
        files = [meta("app/main.py", 6), meta("app/extra.py", 10), meta("app/broken.py", 10)]
        out = self.run_fetch(files, tarball)
        self.assertEqual(out["app/main.py"], "x = 1\n")
        self.assertEqual(out["app/extra.py"], "# blob app/extra.py")
        self.assertNotIn("app/broken.py", out)  # failed fetch is simply absent
        self.assertEqual(sorted(self.blob_calls), ["app/broken.py", "app/extra.py"])

    def test_archive_failure_falls_back_entirely(self):
        out = self.run_fetch([meta("app/main.py", 6)], RuntimeError("codeload down"))
        self.assertEqual(out, {"app/main.py": "# blob app/main.py"})

    def test_oversized_archive_returns_none_and_falls_back(self):
        out = self.run_fetch([meta("app/main.py", 6)], None)  # None = exceeded the size cap
        self.assertEqual(out, {"app/main.py": "# blob app/main.py"})

    def test_rate_limit_during_archive_is_fatal(self):
        with self.assertRaises(GitHubRateLimitError):
            self.run_fetch([meta("app/main.py", 6)], GitHubRateLimitError(retry_after_seconds=30))

    def test_undecodable_member_is_reported_as_not_text_not_refetched(self):
        # 3 bytes: invalid UTF-8, and an odd length so UTF-16 fails too.
        #
        # This used to fall through to the per-blob fallback, which
        # re-downloaded the file over the network only to fail the same
        # decode again. On a repository shipping 228 audio files that was
        # 228 wasted requests and about eight minutes. The archive has
        # already proved the bytes are not text, so it is recorded and
        # left alone.
        tarball = make_tarball({"a.py": b"ok\n", "b.py": b"\xff\xff\xff"})
        out = self.run_fetch([meta("a.py", 3), meta("b.py", 3)], tarball)
        self.assertEqual(out["a.py"], "ok\n")
        self.assertNotIn("b.py", out)
        self.assertEqual(self._last.not_text, ["b.py"])

    def test_not_text_is_not_the_same_as_missing(self):
        # A binary is retrievable and simply not source. Counting it as a
        # failure is what stopped such a repository ever being cached.
        tarball = make_tarball({"a.py": b"ok\n", "b.py": b"\xff\xff\xff"})
        self.run_fetch([meta("a.py", 3), meta("b.py", 3)], tarball)
        self.assertEqual(self._last.not_text, ["b.py"])
        self.assertEqual(list(self._last.contents), ["a.py"])


if __name__ == "__main__":
    unittest.main()


class NotFoundTest(unittest.TestCase):
    def test_404_on_commit_lookup_is_a_typed_not_found(self):
        import httpx

        from app.application.interfaces.github_client import GitHubRepoNotFoundError

        client = GitHubApiClient(token=None)

        async def fake_get(url, **kwargs):
            request = httpx.Request("GET", url)
            raise httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))

        client._get_json_with_retry = fake_get
        with self.assertRaises(GitHubRepoNotFoundError) as ctx:
            asyncio.run(client.get_latest_commit_sha(REPO))
        self.assertIn("acme/widgets", str(ctx.exception))
        self.assertIn("private", str(ctx.exception))

    def test_other_http_errors_still_propagate(self):
        import httpx

        client = GitHubApiClient(token=None)

        async def fake_get(url, **kwargs):
            request = httpx.Request("GET", url)
            raise httpx.HTTPStatusError("500", request=request, response=httpx.Response(500, request=request))

        client._get_json_with_retry = fake_get
        with self.assertRaises(httpx.HTTPStatusError):
            asyncio.run(client.get_latest_commit_sha(REPO))


class VisibilityGuardTest(unittest.IsolatedAsyncioTestCase):
    """
    The guard that makes a hosted instance safe: the server's token must
    never be used to open a repository the visitor could not open.
    """

    async def _assert_public(self, payload, *, status=200):
        client = GitHubApiClient(token="t")

        async def fake_get_json(url, params=None):
            if status == 404:
                response = httpx.Response(404, request=httpx.Request("GET", url))
                raise httpx.HTTPStatusError("not found", request=response.request, response=response)
            return payload

        client._get_json_with_retry = fake_get_json
        await client.assert_public(RepoUrl("https://github.com/acme/widgets"))

    async def test_public_repo_passes(self):
        await self._assert_public({"private": False, "visibility": "public"})

    async def test_private_repo_is_refused(self):
        with self.assertRaises(RepoIsPrivateError):
            await self._assert_public({"private": True, "visibility": "private"})

    async def test_internal_repo_is_refused(self):
        # Enterprise orgs have a third state that is not private but is
        # also not public; refusing anything not plainly public covers it.
        with self.assertRaises(RepoIsPrivateError):
            await self._assert_public({"private": False, "visibility": "internal"})

    async def test_missing_repo_is_not_found_rather_than_private(self):
        with self.assertRaises(GitHubRepoNotFoundError):
            await self._assert_public({}, status=404)

    async def test_absent_visibility_field_falls_back_to_private_flag(self):
        # Older GitHub Enterprise responses omit "visibility".
        await self._assert_public({"private": False})
        with self.assertRaises(RepoIsPrivateError):
            await self._assert_public({"private": True})


class RenamedRepositoryTest(unittest.IsolatedAsyncioTestCase):
    """
    A renamed or transferred repository answers 301 with its new home.
    This is common -- tiangolo/fastapi is fastapi/fastapi now -- and
    refusing the redirect turned a perfectly valid old URL into a 500
    reading "Ingestion failed unexpectedly".
    """

    def _client(self, handler):
        client = GitHubApiClient(token=None)
        transport = httpx.MockTransport(handler)
        original = httpx.AsyncClient

        def patched(*args, **kwargs):
            kwargs["transport"] = transport
            return original(*args, **kwargs)

        self._patch = patch("httpx.AsyncClient", patched)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        return client

    async def test_a_renamed_repository_is_followed_to_its_new_home(self):
        seen = []

        def handler(request):
            seen.append(str(request.url))
            if "tiangolo" in str(request.url):
                return httpx.Response(
                    301,
                    headers={"location": "https://api.github.com/repositories/160919119"},
                )
            return httpx.Response(200, json={"private": False, "full_name": "fastapi/fastapi"})

        client = self._client(handler)
        await client.assert_public(RepoUrl("https://github.com/tiangolo/fastapi"))
        self.assertEqual(len(seen), 2, "it should have followed the redirect")
        self.assertIn("repositories/160919119", seen[1])

    async def test_a_redirect_off_the_api_host_is_refused(self):
        # The token rides on these requests. A redirect that leaves
        # api.github.com must not carry it along.
        def handler(request):
            return httpx.Response(301, headers={"location": "https://evil.example.com/x"})

        client = self._client(handler)
        with self.assertRaises(GitHubRepoNotFoundError):
            await client.assert_public(RepoUrl("https://github.com/acme/widgets"))
