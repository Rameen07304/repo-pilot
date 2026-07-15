"""
End-to-end smoke test for the Explorer Agent: wires up every real
infrastructure implementation (Gemini, GitHub, Chroma, tree-sitter,
SQLite) and runs IngestRepoUseCase against an actual public repo.

This is the first proof the whole pipeline works together, not just
each piece in isolation. Requires GEMINI_API_KEY and GITHUB_TOKEN set
in .env — falls back to unauthenticated GitHub access (60 req/hr) if
GITHUB_TOKEN is missing, but that will fail fast on anything but a
tiny repo.

Usage:
    python run_ingest_test.py
    python run_ingest_test.py https://github.com/owner/repo
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from app.application.use_cases.ingest_repo import (
    IngestionLimitExceeded,
    IngestRepoUseCase,
)
from app.application.interfaces.github_client import GitHubRateLimitError
from app.application.interfaces.llm_client import LLMProviderError
from app.infrastructure.cache.sqlite_cache import SqliteRepoCache
from app.infrastructure.github.github_api_client import GitHubApiClient
from app.infrastructure.llm.gemini_client import GeminiClient
from app.infrastructure.parsing.tree_sitter_parser import TreeSitterCodeParser
from app.infrastructure.vector_store.chroma_store import ChromaVectorStore

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Small, fast, public — good default target so this runs quickly.
DEFAULT_REPO_URL = "https://github.com/octocat/Hello-World"


def build_use_case() -> IngestRepoUseCase:
    """
    Constructs every real infrastructure implementation from .env
    config and wires them into IngestRepoUseCase. This is the only
    place in the whole app, so far, where concrete infrastructure
    classes and the use case meet — everywhere else only ever sees
    the Protocol interfaces.
    """
    gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
    if not gemini_api_key:
        raise SystemExit(
            "GEMINI_API_KEY is missing from .env. Get a free key at "
            "https://aistudio.google.com/apikey and add it to .env before running this."
        )

    github_token = os.environ.get("GITHUB_TOKEN") or None
    if not github_token:
        logger.warning(
            "No GITHUB_TOKEN set — using unauthenticated GitHub access "
            "(60 requests/hour). This will likely fail on anything but "
            "a very small repo. Add a free token to .env for 5000/hour."
        )

    llm_client = GeminiClient(
        api_key=gemini_api_key,
        model_name=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
    )
    github_client = GitHubApiClient(token=github_token)
    vector_store = ChromaVectorStore(
        persist_dir=os.environ.get("CHROMA_PERSIST_DIR", "./data/chroma")
    )
    code_parser = TreeSitterCodeParser()
    cache = SqliteRepoCache(
        db_path=os.environ.get("SQLITE_DB_PATH", "./data/app.db")
    )

    return IngestRepoUseCase(
        github_client=github_client,
        llm_client=llm_client,
        vector_store=vector_store,
        code_parser=code_parser,
        cache=cache,
        max_files=int(os.environ.get("MAX_FILES_PER_REPO", 500)),
        max_file_size_bytes=int(os.environ.get("MAX_FILE_SIZE_BYTES", 100 * 1024)),
        max_total_size_bytes=int(os.environ.get("MAX_TOTAL_REPO_SIZE_BYTES", 5 * 1024 * 1024)),
    )


async def main() -> None:
    load_dotenv()

    repo_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_REPO_URL
    logger.info("Ingesting: %s", repo_url)

    use_case = build_use_case()

    try:
        manifest, was_cached = await use_case.execute(repo_url)
    except ValueError as e:
        logger.error("Invalid repo URL: %s", e)
        return
    except IngestionLimitExceeded as e:
        logger.error("Repo exceeds configured limits: %s", e)
        return
    except GitHubRateLimitError as e:
        logger.error(
            "GitHub rate limit hit (retry after %s seconds). "
            "Add a GITHUB_TOKEN to .env if you haven't.",
            e.retry_after_seconds,
        )
        return
    except LLMProviderError as e:
        logger.error(
            "LLM call failed (provider=%s, quota_exceeded=%s): %s",
            e.provider, e.is_quota_exceeded, e,
        )
        return

    print("\n" + "=" * 60)
    print(f"INGESTION {'(served from cache)' if was_cached else '(fresh)'}")
    print("=" * 60)
    print(f"Repo:            {manifest.repo_url.full_name}")
    print(f"Commit SHA:      {manifest.commit_sha}")
    print(f"Files ingested:  {manifest.file_count}")
    print(f"Detected stack:  {', '.join(manifest.detected_stack) or 'none detected'}")
    print(f"Entry points:    {', '.join(manifest.entry_points) or 'none detected'}")
    print(f"\nSummary:\n{manifest.summary}")
    print("=" * 60)

    # Run it again to prove the cache actually works — second call
    # should return was_cached=True and take a fraction of the time.
    logger.info("Running again to verify caching works...")
    manifest2, was_cached2 = await use_case.execute(repo_url)
    assert was_cached2 is True, "Second call should have hit the cache!"
    print(f"\n✅ Second call correctly served from cache (commit {manifest2.commit_sha})")


if __name__ == "__main__":
    asyncio.run(main())