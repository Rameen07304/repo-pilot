"""
IngestRepoUseCase — a partial fetch must never be cached as if complete.
Everything external is a fake; the parser is a stub that produces no
symbols, since call tracing is not what is under test here.
"""
import asyncio
import unittest

from app.application.interfaces.github_client import (
    FetchedFiles,
    RemoteFileMeta,
    RepoIsPrivateError,
)
from app.application.use_cases.ingest_repo import IngestRepoUseCase


class FakeGitHub:
    def __init__(self, contents: dict[str, str], *, private: bool = False, tree_paths=None):
        self.contents = contents
        self.private = private
        self.visibility_checked = False
        # The tree is fixed by default so tests can simulate a partial
        # fetch by passing fewer contents than paths. Override it when the
        # test needs different file types in the tree.
        self.tree_paths = tree_paths
        # Paths that were retrieved but are not text; not failures.
        self.not_text: list[str] = []

    async def assert_public(self, repo):
        self.visibility_checked = True
        if self.private:
            raise RepoIsPrivateError(repo.full_name)

    async def get_latest_commit_sha(self, repo):
        return "abc123"

    async def get_repo_tree(self, repo, *, commit_sha):
        paths = self.tree_paths or ["app/main.py", "app/util.py", "requirements.txt"]
        return [
            RemoteFileMeta(path=p, size_bytes=10, sha=str(i), is_binary_guess=False)
            for i, p in enumerate(paths, start=1)
        ]

    async def get_file_contents(self, repo, *, commit_sha, files):
        return FetchedFiles(
            contents={f.path: self.contents[f.path] for f in files if f.path in self.contents},
            not_text=list(self.not_text),
        )


class FakeLLM:
    async def embed(self, texts):
        return [[0.0] for _ in texts]

    async def generate(self, prompt, **kwargs):
        return "A test project."


class FakeVectors:
    async def upsert_chunks(self, **kwargs):
        pass


class StubParser:
    version = "test"
    unavailable_languages: set = set()

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


class ChunkCapTest(unittest.TestCase):
    """The repo-wide chunk cap keeps every file's leading chunks rather than dropping whole files."""

    def _run(self, contents, max_chunks):
        from app.domain.entities import CodeChunk

        class ChunkyParser(StubParser):
            def chunk_file(self, *, file_path, content):
                # one chunk per line, so chunk counts are easy to reason about
                return [CodeChunk(id=f"{file_path}:{i}", file_path=file_path, content=line, start_line=i + 1, end_line=i + 1)
                        for i, line in enumerate(content.splitlines())]

        class RecordingLLM(FakeLLM):
            def __init__(self):
                self.embedded = []

            async def embed(self, texts):
                self.embedded.extend(texts)
                return [[0.0] for _ in texts]

        llm = RecordingLLM()
        uc = IngestRepoUseCase(
            github_client=FakeGitHub(contents), llm_client=llm, vector_store=FakeVectors(),
            code_parser=ChunkyParser(), cache=RecordingCache(), max_chunks=max_chunks,
        )
        asyncio.run(uc.execute("https://github.com/acme/widgets"))
        return llm.embedded

    def test_under_cap_keeps_everything(self):
        embedded = self._run({"app/main.py": "a\nb\nc", "app/util.py": "d\ne", "requirements.txt": "f"}, max_chunks=100)
        self.assertEqual(sorted(embedded), ["a", "b", "c", "d", "e", "f"])

    def test_over_cap_is_round_robin_across_files(self):
        embedded = self._run({"app/main.py": "a1\na2\na3\na4", "app/util.py": "b1\nb2\nb3", "requirements.txt": "c1"}, max_chunks=5)
        # first chunk of every file, then second of every file, until 5
        self.assertEqual(embedded, ["a1", "b1", "c1", "a2", "b2"])

    def test_embedded_text_is_length_capped(self):
        long_line = "x" * 5000
        from app.domain.entities import CodeChunk

        class OneChunkParser(StubParser):
            def chunk_file(self, *, file_path, content):
                return [CodeChunk(id=file_path, file_path=file_path, content=content, start_line=1, end_line=1)]

        class RecordingLLM(FakeLLM):
            def __init__(self):
                self.embedded = []

            async def embed(self, texts):
                self.embedded.extend(texts)
                return [[0.0] for _ in texts]

        llm = RecordingLLM()
        uc = IngestRepoUseCase(
            github_client=FakeGitHub({"app/main.py": long_line, "app/util.py": "y", "requirements.txt": "z"}),
            llm_client=llm, vector_store=FakeVectors(), code_parser=OneChunkParser(), cache=RecordingCache(),
            max_embed_chars=1500,
        )
        asyncio.run(uc.execute("https://github.com/acme/widgets"))
        self.assertEqual(max(len(t) for t in llm.embedded), 1500)


class PrivateRepoGuardTest(unittest.TestCase):
    """
    The server carries a GitHub token, so without this guard it would
    open any private repository it can see for whoever pasted the URL.
    These are the tests that make hosting it safe.
    """

    def _use_case(self, github, cache=None):
        return IngestRepoUseCase(
            github_client=github, llm_client=FakeLLM(), vector_store=FakeVectors(),
            code_parser=StubParser(), cache=cache or RecordingCache(),
        )

    def test_a_private_repo_is_refused(self):
        uc = self._use_case(FakeGitHub({"app/main.py": "x"}, private=True))
        with self.assertRaises(RepoIsPrivateError):
            asyncio.run(uc.execute("https://github.com/acme/secrets"))

    def test_a_private_repo_is_never_fetched_or_cached(self):
        cache = RecordingCache()
        github = FakeGitHub({"app/main.py": "x"}, private=True)
        uc = self._use_case(github, cache=cache)
        with self.assertRaises(RepoIsPrivateError):
            asyncio.run(uc.execute("https://github.com/acme/secrets"))
        # Nothing about it reached the cache.
        self.assertEqual(cache.manifests, {})
        self.assertEqual(cache.indexes, {})

    def test_visibility_is_checked_before_the_cache(self):
        # A repo made private after being ingested must stop being served,
        # so the check cannot sit behind a cache hit.
        github = FakeGitHub({"app/main.py": "x"}, private=True)
        uc = self._use_case(github)
        with self.assertRaises(RepoIsPrivateError):
            asyncio.run(uc.execute("https://github.com/acme/secrets"))
        self.assertTrue(github.visibility_checked)

    def test_a_public_repo_still_ingests(self):
        github = FakeGitHub({"app/main.py": "x", "requirements.txt": "y"})
        manifest, _cached = asyncio.run(self._use_case(github).execute("https://github.com/acme/widgets"))
        self.assertTrue(github.visibility_checked)
        self.assertEqual(manifest.repo_url.full_name, "acme/widgets")


class DegradedParsingTest(unittest.TestCase):
    """
    A grammar that will not load means generic chunking, no symbols and no
    import edges for that language. Ingest still succeeds, so the only way
    a caller learns the answers got worse is if the response says so.
    """

    class DartParser(StubParser):
        unavailable_languages = {"dart"}

        def detect_language(self, path):
            return "dart" if path.endswith(".dart") else None

    def test_a_missing_grammar_is_reported_when_the_language_is_present(self):
        uc = IngestRepoUseCase(
            github_client=FakeGitHub(
                {"lib/main.dart": "void main() {}", "README.md": "x"},
                tree_paths=["lib/main.dart", "README.md"],
            ),
            llm_client=FakeLLM(), vector_store=FakeVectors(),
            code_parser=self.DartParser(), cache=RecordingCache(),
        )
        manifest, _ = asyncio.run(uc.execute("https://github.com/acme/widgets"))
        self.assertEqual(manifest.degraded_languages, ["dart"])

    def test_a_missing_grammar_is_not_reported_when_the_language_is_absent(self):
        # "Dart is unavailable" on a repo with no Dart in it is noise.
        uc = IngestRepoUseCase(
            github_client=FakeGitHub(
                {"app/main.py": "x", "README.md": "y"},
                tree_paths=["app/main.py", "README.md"],
            ),
            llm_client=FakeLLM(), vector_store=FakeVectors(),
            code_parser=self.DartParser(), cache=RecordingCache(),
        )
        manifest, _ = asyncio.run(uc.execute("https://github.com/acme/widgets"))
        self.assertEqual(manifest.degraded_languages, [])

    def test_a_healthy_parser_reports_nothing(self):
        uc = IngestRepoUseCase(
            github_client=FakeGitHub(
                {"app/main.py": "x", "README.md": "y"},
                tree_paths=["app/main.py", "README.md"],
            ),
            llm_client=FakeLLM(), vector_store=FakeVectors(),
            code_parser=StubParser(), cache=RecordingCache(),
        )
        manifest, _ = asyncio.run(uc.execute("https://github.com/acme/widgets"))
        self.assertEqual(manifest.degraded_languages, [])


class BinaryFilesDoNotBlockCachingTest(unittest.TestCase):
    """
    A repository that ships binaries is still fully ingested.

    Counting "not text" as "could not fetch" marked such a repo incomplete
    and refused to cache it, so every later question re-ran the whole
    ingest. Found on a real repo carrying 228 .ogg files.
    """

    def _run(self, *, not_text):
        github = FakeGitHub(
            {"app/main.py": "x", "README.md": "y"},
            tree_paths=["app/main.py", "README.md", "sound.ogg"],
        )
        github.not_text = not_text
        cache = RecordingCache()
        uc = IngestRepoUseCase(
            github_client=github, llm_client=FakeLLM(), vector_store=FakeVectors(),
            code_parser=StubParser(), cache=cache,
        )
        manifest, _ = asyncio.run(uc.execute("https://github.com/acme/widgets"))
        return manifest, cache

    def test_a_binary_does_not_make_the_ingest_incomplete(self):
        manifest, _ = self._run(not_text=["sound.ogg"])
        self.assertEqual(manifest.skipped_file_count, 0)

    def test_a_binary_does_not_stop_the_result_being_cached(self):
        _manifest, cache = self._run(not_text=["sound.ogg"])
        self.assertEqual(
            len(cache.manifests), 1,
            "a repository shipping binaries must still be cached",
        )

    def test_a_file_that_really_could_not_be_fetched_still_counts(self):
        # Nothing reported it as binary, so it is genuinely missing.
        manifest, cache = self._run(not_text=[])
        self.assertEqual(manifest.skipped_file_count, 1)
        self.assertEqual(cache.manifests, {}, "an incomplete ingest must not be cached")


class StaleParsedIndexTest(unittest.TestCase):
    """
    A cached index outlives the parser that built it. Adding grammars
    gave several languages symbols for the first time, and without this
    every already-ingested repository kept its empty index and looked
    exactly as it had before.
    """

    def _cache_with_index(self, *, version):
        from app.domain.entities import ParsedRepoIndex, RepoManifest
        from app.domain.value_objects import RepoUrl

        cache = RecordingCache()
        key = ("acme/widgets", "abc123")
        cache.manifests[key] = RepoManifest(
            repo_url=RepoUrl("https://github.com/acme/widgets"), commit_sha="abc123",
        )
        cache.indexes[key] = ParsedRepoIndex(
            repo_full_name="acme/widgets", commit_sha="abc123", parser_version=version,
        )
        return cache

    def _ingest(self, cache):
        uc = IngestRepoUseCase(
            github_client=FakeGitHub(
                {"app/main.py": "x", "requirements.txt": "y"},
                tree_paths=["app/main.py", "requirements.txt"],
            ),
            llm_client=FakeLLM(), vector_store=FakeVectors(),
            code_parser=StubParser(), cache=cache,
        )
        return asyncio.run(uc.execute("https://github.com/acme/widgets"))

    def test_an_index_from_the_current_parser_is_served_from_cache(self):
        _manifest, was_cached = self._ingest(self._cache_with_index(version=StubParser.version))
        self.assertTrue(was_cached)

    def test_an_index_from_an_older_parser_is_re_parsed(self):
        _manifest, was_cached = self._ingest(self._cache_with_index(version="1"))
        self.assertFalse(was_cached, "a stale index must not be served")

    def test_an_index_with_no_recorded_version_is_re_parsed(self):
        # Everything cached before the version existed.
        _manifest, was_cached = self._ingest(self._cache_with_index(version=""))
        self.assertFalse(was_cached)

    def test_a_fresh_ingest_stamps_the_current_parser(self):
        cache = RecordingCache()
        self._ingest(cache)
        index = cache.indexes[("acme/widgets", "abc123")]
        self.assertEqual(index.parser_version, StubParser.version)
