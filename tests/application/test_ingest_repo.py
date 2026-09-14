"""
IngestRepoUseCase — a partial fetch must never be cached as if complete.
Everything external is a fake; the parser is a stub that produces no
symbols, since call tracing is not what is under test here.
"""
import asyncio
import unittest

from app.application.interfaces.github_client import RemoteFileMeta
from app.application.use_cases.ingest_repo import IngestRepoUseCase


class FakeGitHub:
    def __init__(self, contents: dict[str, str]):
        self.contents = contents

    async def get_latest_commit_sha(self, repo):
        return "abc123"

    async def get_repo_tree(self, repo, *, commit_sha):
        return [
            RemoteFileMeta(path="app/main.py", size_bytes=10, sha="1", is_binary_guess=False),
            RemoteFileMeta(path="app/util.py", size_bytes=10, sha="2", is_binary_guess=False),
            RemoteFileMeta(path="requirements.txt", size_bytes=10, sha="3", is_binary_guess=False),
        ]

    async def get_file_contents(self, repo, *, commit_sha, files):
        return {f.path: self.contents[f.path] for f in files if f.path in self.contents}


class FakeLLM:
    async def embed(self, texts):
        return [[0.0] for _ in texts]

    async def generate(self, prompt, **kwargs):
        return "A test project."


class FakeVectors:
    async def upsert_chunks(self, **kwargs):
        pass


class StubParser:
    def detect_language(self, path):
        return None

    def chunk_file(self, *, file_path, content):
        return []

    def extract_symbols(self, **kwargs):
        return []

    def build_module_graph(self, all_files):
        return []


class RecordingCache:
    def __init__(self):
        self.manifests = {}
        self.indexes = {}

    async def get(self, repo_full_name, commit_sha):
        return self.manifests.get((repo_full_name, commit_sha))

    async def set(self, manifest):
        self.manifests[(manifest.repo_url.full_name, manifest.commit_sha)] = manifest

    async def get_parsed_index(self, repo_full_name, commit_sha):
        return self.indexes.get((repo_full_name, commit_sha))

    async def set_parsed_index(self, parsed_index):
        self.indexes[(parsed_index.repo_full_name, parsed_index.commit_sha)] = parsed_index


def build(contents):
    cache = RecordingCache()
    uc = IngestRepoUseCase(
        github_client=FakeGitHub(contents),
        llm_client=FakeLLM(),
        vector_store=FakeVectors(),
        code_parser=StubParser(),
        cache=cache,
    )
    return uc, cache


class PartialIngestTest(unittest.TestCase):
    def test_complete_ingest_is_cached(self):
        uc, cache = build({"app/main.py": "x", "app/util.py": "y", "requirements.txt": "fastapi"})
        manifest, was_cached = asyncio.run(uc.execute("https://github.com/acme/widgets"))
        self.assertFalse(was_cached)
        self.assertEqual(manifest.skipped_file_count, 0)
        self.assertIn("fastapi", manifest.detected_stack)
        self.assertEqual(len(cache.manifests), 1)
        self.assertEqual(len(cache.indexes), 1)
        # second run is served from cache with zero fetches
        _, was_cached = asyncio.run(uc.execute("https://github.com/acme/widgets"))
        self.assertTrue(was_cached)

    def test_partial_ingest_is_reported_and_not_cached(self):
        uc, cache = build({"app/main.py": "x"})  # util.py and requirements.txt could not be fetched
        manifest, _ = asyncio.run(uc.execute("https://github.com/acme/widgets"))
        self.assertEqual(manifest.skipped_file_count, 2)
        self.assertEqual(manifest.file_count, 1)
        self.assertEqual(cache.manifests, {})
        self.assertEqual(cache.indexes, {})
        # and a retry really does re-ingest instead of returning the partial result
        _, was_cached = asyncio.run(uc.execute("https://github.com/acme/widgets"))
        self.assertFalse(was_cached)


if __name__ == "__main__":
    unittest.main()
