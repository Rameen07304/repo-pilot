"""
One-off smoke test: confirms domain + application layers import and
behave correctly, with zero external dependencies. Delete this once
tests/ has real pytest coverage — this is just a sanity checkpoint.
"""
import sys

# Windows consoles default to cp1252, which can't encode the check marks below.
sys.stdout.reconfigure(encoding="utf-8")

def check_value_objects():
    from app.domain.value_objects import RepoUrl, Citation, ConfidenceScore, QuestionMode

    # Valid URL should parse fine
    repo = RepoUrl("https://github.com/octocat/Hello-World")
    assert repo.owner == "octocat"
    assert repo.repo == "Hello-World"
    assert repo.full_name == "octocat/Hello-World"
    print("✅ RepoUrl valid case:", repo.full_name)

    # Invalid URLs must raise — this IS our SSRF guard, so it must fail loudly
    bad_urls = [
        "http://github.com/octocat/Hello-World",   # wrong scheme
        "https://evil.com/octocat/Hello-World",     # wrong host
        "https://github.com/octocat",               # missing repo
        "not-a-url-at-all",
    ]
    for bad in bad_urls:
        try:
            RepoUrl(bad)
            raise AssertionError(f"❌ Should have rejected: {bad}")
        except ValueError:
            print(f"✅ Correctly rejected: {bad}")

    citation = Citation(file_path="app/main.py", start_line=10, end_line=20)
    print("✅ Citation:", citation)

    score = ConfidenceScore(0.3)
    assert score.is_low() is True
    print("✅ ConfidenceScore.is_low():", score.is_low())

    assert QuestionMode.QUICK.value == "quick"
    print("✅ QuestionMode enum OK")


def check_entities():
    from app.domain.entities import FileNode, RepoManifest, CodeChunk, Question
    from app.domain.value_objects import RepoUrl, QuestionMode

    repo = RepoUrl("https://github.com/octocat/Hello-World")
    node = FileNode(path="README.md", size_bytes=120, language="markdown", content="# Hello")
    manifest = RepoManifest(
        repo_url=repo,
        commit_sha="abc123",
        files=[node],
        detected_stack=["python"],
        entry_points=["main.py"],
        readme_content="# Hello",
        summary="A test repo.",
    )
    assert manifest.file_count == 1
    print("✅ RepoManifest built, file_count:", manifest.file_count)

    chunk = CodeChunk(id="c1", file_path="app/main.py", content="def foo(): pass", start_line=1, end_line=1)
    citation = chunk.to_citation()
    print("✅ CodeChunk.to_citation():", citation)

    q = Question(text="How does auth work?", mode=QuestionMode.QUICK)
    print("✅ Question:", q.text, q.mode)

    try:
        Question(text="   ")
        raise AssertionError("❌ Should have rejected empty question")
    except ValueError:
        print("✅ Correctly rejected empty question")


def check_interfaces_importable():
    from app.application.interfaces.llm_client import LLMClient, LLMProviderError
    from app.application.interfaces.vector_store import VectorStore, VectorQueryResult
    from app.application.interfaces.github_client import GitHubClient, GitHubRateLimitError, RemoteFileMeta
    from app.application.interfaces.code_parser import CodeParser, TraceStep, ModuleEdge
    print("✅ All four interface Protocols import cleanly")


def check_use_case_importable():
    from app.application.use_cases.ingest_repo import IngestRepoUseCase, IngestionLimitExceeded, RepoCache
    print("✅ ingest_repo.py imports cleanly")

def check_chroma_isolation():
    import asyncio
    from app.infrastructure.vector_store.chroma_store import ChromaVectorStore
    from app.domain.entities import CodeChunk

    store = ChromaVectorStore(persist_dir="./data/chroma_test")

    async def run():
        chunk_a = CodeChunk(id="1", file_path="main.py", content="print('repo A')", start_line=1, end_line=1)
        chunk_b = CodeChunk(id="1", file_path="main.py", content="print('repo B')", start_line=1, end_line=1)

        # SAME chunk id ("1") in both repos — this is the exact
        # collision scenario the namespacing is designed to prevent.
        await store.upsert_chunks(
            repo_full_name="ownerA/repoA", commit_sha="shaA",
            chunks=[chunk_a], embeddings=[[0.1, 0.2, 0.3]],
        )
        await store.upsert_chunks(
            repo_full_name="ownerB/repoB", commit_sha="shaB",
            chunks=[chunk_b], embeddings=[[0.1, 0.2, 0.3]],
        )

        results_a = await store.query(
            repo_full_name="ownerA/repoA", commit_sha="shaA",
            query_embedding=[0.1, 0.2, 0.3], top_k=5,
        )
        assert len(results_a) == 1, f"Expected 1 result, got {len(results_a)}"
        assert "repo A" in results_a[0].chunk.content, "Leaked wrong repo's content!"
        print("✅ No cross-repo leakage — repo A query returned only repo A's chunk")

    asyncio.run(run())
def check_parsed_repo_index():
    from app.domain.entities import ParsedSymbol, ParsedRepoIndex

    sym_a = ParsedSymbol(
        name="handle_login", kind="function", file_path="app/auth.py",
        start_line=10, end_line=20, calls=["verify_password", "create_session"],
    )
    sym_b = ParsedSymbol(
        name="verify_password", kind="function", file_path="app/auth.py",
        start_line=25, end_line=30,
    )

    index = ParsedRepoIndex(
        repo_full_name="octocat/Hello-World",
        commit_sha="abc123",
        symbols=[sym_a, sym_b],
        module_edges=[("app/auth.py", "app/db.py")],
    )

    assert len(index.symbols_in_file("app/auth.py")) == 2
    print("✅ symbols_in_file:", len(index.symbols_in_file("app/auth.py")))

    found = index.find_by_name("verify_password")
    assert len(found) == 1
    print("✅ find_by_name:", found[0].name)

    from app.application.interfaces.code_parser import CodeParser
    print("✅ CodeParser interface imports cleanly with updated trace_calls signature")
def check_tree_sitter_parser():
    from app.infrastructure.parsing.tree_sitter_parser import TreeSitterCodeParser
    from app.domain.entities import ParsedRepoIndex

    parser = TreeSitterCodeParser()

    py_code = (
        "def verify_password(pw):\n"
        "    return pw == 'secret'\n\n"
        "def handle_login(username, pw):\n"
        "    ok = verify_password(pw)\n"
        "    return ok\n"
    )

    assert parser.detect_language("app/auth.py") == "python"
    assert parser.detect_language("README.md") is None
    print("✅ detect_language works")

    chunks = parser.chunk_file(file_path="app/auth.py", content=py_code)
    assert len(chunks) == 2, f"expected 2 chunks, got {len(chunks)}"
    print("✅ chunk_file found", len(chunks), "function-level chunks")

    symbols = parser.extract_symbols(file_path="app/auth.py", content=py_code, language="python")
    names = {s.name for s in symbols}
    assert names == {"verify_password", "handle_login"}
    login_symbol = next(s for s in symbols if s.name == "handle_login")
    assert "verify_password" in login_symbol.calls
    print("✅ Symbol extraction + call detection correct:", login_symbol.calls)

    index = ParsedRepoIndex(
        repo_full_name="test/repo", commit_sha="sha1",
        symbols=symbols, module_edges=[],
    )
    trace = parser.trace_calls(
        entry_file="app/auth.py", entry_symbol="handle_login",
        parsed_index=index, max_hops=3,
    )
    traced_names = [t.symbol_name for t in trace]
    assert traced_names == ["handle_login", "verify_password"], traced_names
    print("✅ trace_calls followed the call chain:", traced_names)

def check_sqlite_cache():
    import asyncio
    from app.infrastructure.cache.sqlite_cache import SqliteRepoCache
    from app.domain.entities import FileNode, RepoManifest, ParsedRepoIndex, ParsedSymbol
    from app.domain.value_objects import RepoUrl

    cache = SqliteRepoCache(db_path="./data/test_cache.db")

    async def run():
        manifest = RepoManifest(
            repo_url=RepoUrl("https://github.com/octocat/Hello-World"),
            commit_sha="sha123",
            files=[FileNode(path="main.py", size_bytes=50, language="python", content="print('hi')")],
            detected_stack=["python"],
            entry_points=["main.py"],
            readme_content="# Hello",
            summary="A test repo.",
        )
        await cache.set(manifest)

        # Miss on wrong SHA — proves we don't return stale/wrong-commit data
        miss = await cache.get("octocat/Hello-World", "different_sha")
        assert miss is None
        print("✅ Cache correctly misses on different commit_sha")

        hit = await cache.get("octocat/Hello-World", "sha123")
        assert hit is not None
        assert hit.summary == "A test repo."
        assert hit.files[0].content == "print('hi')"
        print("✅ Cache hit round-tripped RepoManifest correctly")

        index = ParsedRepoIndex(
            repo_full_name="octocat/Hello-World", commit_sha="sha123",
            symbols=[ParsedSymbol(name="main", kind="function", file_path="main.py", start_line=1, end_line=2, calls=[])],
            module_edges=[("main.py", "utils.py")],
        )
        await cache.set_parsed_index(index)
        index_hit = await cache.get_parsed_index("octocat/Hello-World", "sha123")
        assert index_hit is not None
        assert index_hit.symbols[0].name == "main"
        assert index_hit.module_edges == [("main.py", "utils.py")]
        print("✅ Cache hit round-tripped ParsedRepoIndex correctly")

    asyncio.run(run())

if __name__ == "__main__":
    print("--- value_objects ---")
    check_value_objects()
    print("\n--- entities ---")
    check_entities()
    print("\n--- application interfaces ---")
    check_interfaces_importable()
    print("\n--- dto + use case ---")
    check_use_case_importable()
    print("\n🎉 All checks passed — domain + application layers are solid.")



from app.infrastructure.security.secret_scanner import scan_and_redact

result = scan_and_redact('API_KEY = "AIzaSyD1234567890abcdefghijklmnopqrstuv"')
print(result)


from app.infrastructure.security.rate_limiter import FixedWindowRateLimiter

limiter = FixedWindowRateLimiter(max_events=2, window_seconds=3600)
print(limiter.check_and_increment("1.2.3.4"))
print(limiter.check_and_increment("1.2.3.4"))
print(limiter.check_and_increment("1.2.3.4"))
print("\n--- chroma vector store isolation ---")
check_chroma_isolation()
print("\n--- parsed repo index ---")
check_parsed_repo_index()
print("\n--- tree-sitter parser ---")
check_tree_sitter_parser()
print("\n--- sqlite cache ---")
check_sqlite_cache()