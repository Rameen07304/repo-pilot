"""
GitHubApiClient.get_file_contents — the single-archive path and its
per-file fallback. No network: the tarball download and the per-blob
fetch are stubbed at the method boundary.
"""
import asyncio
import io
import tarfile
import unittest

from app.application.interfaces.github_client import GitHubRateLimitError, RemoteFileMeta
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
        return asyncio.run(self.client.get_file_contents(REPO, commit_sha="1ea2fdb", files=files))

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

    def test_undecodable_member_is_skipped_not_fatal(self):
        # 3 bytes: invalid UTF-8, and an odd length so UTF-16 fails too
        tarball = make_tarball({"a.py": b"ok\n", "b.py": b"\xff\xff\xff"})
        out = self.run_fetch([meta("a.py", 3), meta("b.py", 3)], tarball)
        self.assertEqual(out["a.py"], "ok\n")
        self.assertEqual(out["b.py"], "# blob b.py")  # the archive skipped it; the per-file fallback picked it up


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
