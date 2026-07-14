"""
One-off smoke test: confirms domain + application layers import and
behave correctly, with zero external dependencies. Delete this once
tests/ has real pytest coverage — this is just a sanity checkpoint.
"""

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


def check_dto_and_use_case_importable():
    from app.application.dto import (
        IngestRepoRequest, IngestRepoResponse,
        AskQuestionRequest, AskQuestionResponse,
        GenerateDiagramRequest, GenerateDiagramResponse,
        manifest_to_response,
    )
    from app.application.use_cases.ingest_repo import IngestRepoUseCase, IngestionLimitExceeded, RepoCache
    print("✅ dto.py and ingest_repo.py import cleanly")


if __name__ == "__main__":
    print("--- value_objects ---")
    check_value_objects()
    print("\n--- entities ---")
    check_entities()
    print("\n--- application interfaces ---")
    check_interfaces_importable()
    print("\n--- dto + use case ---")
    check_dto_and_use_case_importable()
    print("\n🎉 All checks passed — domain + application layers are solid.")



from app.infrastructure.security.secret_scanner import scan_and_redact

result = scan_and_redact('API_KEY = "AIzaSyD1234567890abcdefghijklmnopqrstuv"')
print(result)


from app.infrastructure.security.rate_limiter import SlidingWindowRateLimiter

limiter = SlidingWindowRateLimiter(max_events=2, window_seconds=3600)
print(limiter.check_and_increment("1.2.3.4"))
print(limiter.check_and_increment("1.2.3.4"))
print(limiter.check_and_increment("1.2.3.4"))