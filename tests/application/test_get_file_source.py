"""
GetFileSourceUseCase — reads the cached manifest, never the filesystem.
"""
import asyncio
import unittest

from app.application.use_cases.get_file_source import (
    FileNotFoundInRepoError,
    GetFileSourceUseCase,
    RepoNotIngestedError,
)
from app.domain.entities import FileNode, ParsedRepoIndex, ParsedSymbol, RepoManifest
from app.domain.value_objects import RepoUrl


class StubCache:
    def __init__(self, manifest, index=None):
        self._manifest = manifest
        self._index = index

    async def get(self, repo_full_name, commit_sha):
        return self._manifest

    async def get_parsed_index(self, repo_full_name, commit_sha):
        return self._index


def manifest_with(files):
    return RepoManifest(
        repo_url=RepoUrl("https://github.com/acme/app"), commit_sha="sha1", files=files
    )


def run(use_case, file_path):
    return asyncio.run(
        use_case.execute(repo_full_name="acme/app", commit_sha="sha1", file_path=file_path)
    )


class FileSourceTest(unittest.TestCase):
    def test_returns_the_stored_content_and_metadata(self):
        files = [FileNode(path="app/main.py", size_bytes=12, language="python", content="a\nb\nc\n")]
        source = run(GetFileSourceUseCase(cache=StubCache(manifest_with(files))), "app/main.py")

        self.assertEqual(source.content, "a\nb\nc\n")
        self.assertEqual(source.language, "python")
        self.assertEqual(source.size_bytes, 12)
        self.assertEqual(source.line_count, 4)
        self.assertFalse(source.truncated)

    def test_symbols_and_both_edge_directions_come_from_the_parsed_index(self):
        files = [FileNode(path="api/routes.py", size_bytes=5, content="x")]
        index = ParsedRepoIndex(
            repo_full_name="acme/app",
            commit_sha="sha1",
            symbols=[
                ParsedSymbol(name="handle", kind="function", file_path="api/routes.py",
                             start_line=10, end_line=20),
                ParsedSymbol(name="Router", kind="class", file_path="api/routes.py",
                             start_line=1, end_line=30),
                ParsedSymbol(name="other", kind="function", file_path="core/db.py",
                             start_line=1, end_line=2),
            ],
            module_edges=[("api/routes.py", "core/db.py"), ("cli/main.py", "api/routes.py")],
        )
        source = run(
            GetFileSourceUseCase(cache=StubCache(manifest_with(files), index)), "api/routes.py"
        )

        self.assertEqual([s["name"] for s in source.symbols], ["Router", "handle"])  # by start_line
        self.assertEqual(source.imports, ["core/db.py"])
        self.assertEqual(source.imported_by, ["cli/main.py"])

    def test_missing_parsed_index_still_returns_the_source(self):
        files = [FileNode(path="a.py", size_bytes=1, content="x")]
        source = run(GetFileSourceUseCase(cache=StubCache(manifest_with(files), None)), "a.py")
        self.assertEqual(source.content, "x")
        self.assertEqual(source.symbols, [])

    def test_file_with_no_stored_content_returns_empty_rather_than_none(self):
        files = [FileNode(path="a.bin", size_bytes=1, content=None)]
        source = run(GetFileSourceUseCase(cache=StubCache(manifest_with(files))), "a.bin")
        self.assertEqual(source.content, "")
        self.assertEqual(source.line_count, 0)

    def test_oversized_file_is_truncated_and_flagged(self):
        files = [FileNode(path="big.py", size_bytes=10_000, content="x" * 10_000)]
        source = run(
            GetFileSourceUseCase(cache=StubCache(manifest_with(files)), max_chars=100), "big.py"
        )
        self.assertTrue(source.truncated)
        self.assertEqual(len(source.content), 100)

    def test_unknown_path_is_rejected(self):
        files = [FileNode(path="a.py", size_bytes=1, content="x")]
        with self.assertRaises(FileNotFoundInRepoError):
            run(GetFileSourceUseCase(cache=StubCache(manifest_with(files))), "b.py")

    def test_path_traversal_is_just_an_unknown_path(self):
        files = [FileNode(path="a.py", size_bytes=1, content="x")]
        with self.assertRaises(FileNotFoundInRepoError):
            run(GetFileSourceUseCase(cache=StubCache(manifest_with(files))), "../../../etc/passwd")

    def test_uningested_repo_raises(self):
        with self.assertRaises(RepoNotIngestedError):
            run(GetFileSourceUseCase(cache=StubCache(None)), "a.py")


if __name__ == "__main__":
    unittest.main()
