"""
Composition root: builds every infrastructure singleton once, at app
startup, and wires them into the use cases. This is the ONE file in
the whole codebase allowed to import concrete infrastructure classes
directly and hand them to use cases as their Protocol-typed dependencies.

Built once in main.py's lifespan and stored on app.state — routes pull
already-constructed use cases from there via FastAPI's dependency
injection (Depends), never construct their own.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from app.application.use_cases.answer_question import AnswerQuestionUseCase
from app.application.use_cases.generate_diagram import GenerateDiagramUseCase
from app.application.use_cases.ingest_repo import IngestRepoUseCase
from app.infrastructure.cache.sqlite_cache import SqliteRepoCache
from app.infrastructure.github.github_api_client import GitHubApiClient
from app.infrastructure.llm.composite_llm_client import CompositeLLMClient
from app.infrastructure.llm.fallback_llm_client import FallbackLLMClient
from app.infrastructure.llm.gemini_client import GeminiClient
from app.infrastructure.llm.groq_client import GroqClient
from app.infrastructure.llm.local_embedding_client import LocalEmbeddingClient
from app.infrastructure.llm.quota_aware_llm_client import QuotaAwareLLMClient
from app.infrastructure.parsing.tree_sitter_parser import TreeSitterCodeParser
from app.infrastructure.security.rate_limiter import DailyQuotaGuard, FixedWindowRateLimiter
from app.infrastructure.vector_store.chroma_store import ChromaVectorStore


@dataclass
class AppContainer:
    """Holds every constructed singleton + ready-to-use case instances."""
    ingest_use_case: IngestRepoUseCase
    ask_use_case: AnswerQuestionUseCase
    diagram_use_case: GenerateDiagramUseCase

    quota_guard: DailyQuotaGuard
    question_limiter: FixedWindowRateLimiter
    deep_question_limiter: FixedWindowRateLimiter

    # Only true when a proxy we control sits in front of the app (HF
    # Spaces, nginx); otherwise X-Forwarded-For is attacker-controlled.
    trust_proxy_headers: bool = False


def build_container() -> AppContainer:
    api_key = os.getenv("GEMINI_API_KEY", "")
    github_token = os.getenv("GITHUB_TOKEN") or None

    if not api_key or api_key == "your_gemini_api_key_here":
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and add a real key."
        )

    max_files = int(os.getenv("MAX_FILES_PER_REPO", "500"))
    max_file_size = int(os.getenv("MAX_FILE_SIZE_BYTES", str(100 * 1024)))
    max_total_size = int(os.getenv("MAX_TOTAL_REPO_SIZE_BYTES", str(5 * 1024 * 1024)))

    chroma_dir = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma")
    sqlite_path = os.getenv("SQLITE_DB_PATH", "./data/app.db")

    daily_cap = int(os.getenv("LLM_FREE_TIER_DAILY_REQUEST_CAP", "1500"))
    soft_limit_percent = int(os.getenv("DAILY_QUOTA_SOFT_LIMIT_PERCENT", "80"))

    questions_per_hour = int(os.getenv("RATE_LIMIT_QUESTIONS_PER_HOUR_PER_IP", "10"))
    deep_per_hour = int(os.getenv("RATE_LIMIT_DEEP_INVESTIGATIONS_PER_HOUR_PER_IP", "3"))
    trust_proxy_headers = os.getenv("TRUST_PROXY_HEADERS", "false").strip().lower() in ("1", "true", "yes")

    # --- Infrastructure singletons ---
    github_client = GitHubApiClient(token=github_token)

    # Gemini's AQ.-format keys currently hit a known, unresolved
    # Google-side auth bug (ACCESS_TOKEN_TYPE_UNSUPPORTED). Gemini stays
    # the PRIMARY generate() provider — once Google fixes this, calls
    # succeed again automatically, no code change needed. Groq is the
    # fallback for generate() only. Embeddings are ALWAYS local (never
    # fallback-routed) to keep the vector space consistent across every
    # ingestion, regardless of which text-gen path is currently live.
    gemini_client = GeminiClient(
        api_key=api_key,
        model_name=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
    )

    groq_api_key = os.getenv("GROQ_API_KEY", "")
    if not groq_api_key or groq_api_key == "your_groq_api_key_here":
        raise RuntimeError(
            "GROQ_API_KEY is not set. Get a free key at https://console.groq.com/keys "
            "and add it to .env."
        )
    groq_client = GroqClient(
        api_key=groq_api_key,
        model_name=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
    )

    primary_provider = os.getenv("LLM_PRIMARY_PROVIDER", "gemini").strip().lower()
    if primary_provider not in ("gemini", "groq"):
        raise RuntimeError(f"LLM_PRIMARY_PROVIDER must be 'gemini' or 'groq', got {primary_provider!r}.")
    providers = {"gemini": gemini_client, "groq": groq_client}
    secondary_provider = "groq" if primary_provider == "gemini" else "gemini"
    generate_client = FallbackLLMClient(
        primary=providers[primary_provider],
        secondary=providers[secondary_provider],
        primary_name=primary_provider,
        secondary_name=secondary_provider,
    )
    embed_client = LocalEmbeddingClient()
    raw_llm_client = CompositeLLMClient(generate_client=generate_client, embed_client=embed_client)

    quota_guard = DailyQuotaGuard(daily_cap=daily_cap, soft_limit_percent=soft_limit_percent)
    llm_client = QuotaAwareLLMClient(
        wrapped=raw_llm_client, quota_guard=quota_guard, provider_name="gemini/groq+local"
    )

    vector_store = ChromaVectorStore(persist_dir=chroma_dir)
    code_parser = TreeSitterCodeParser()
    cache = SqliteRepoCache(db_path=sqlite_path)

    question_limiter = FixedWindowRateLimiter(max_events=questions_per_hour, window_seconds=3600)
    deep_question_limiter = FixedWindowRateLimiter(max_events=deep_per_hour, window_seconds=3600)

    # --- Use cases, built once, sharing the singletons above ---
    ingest_use_case = IngestRepoUseCase(
        github_client=github_client,
        llm_client=llm_client,
        vector_store=vector_store,
        code_parser=code_parser,
        cache=cache,
        max_files=max_files,
        max_file_size_bytes=max_file_size,
        max_total_size_bytes=max_total_size,
    )

    ask_use_case = AnswerQuestionUseCase(
        llm_client=llm_client,
        vector_store=vector_store,
        code_parser=code_parser,
        cache=cache,
    )

    diagram_use_case = GenerateDiagramUseCase(
        cache=cache,
        llm_client=llm_client,
        use_llm_labels=False,  # keep diagram generation zero-LLM-cost by default
    )

    return AppContainer(
        ingest_use_case=ingest_use_case,
        ask_use_case=ask_use_case,
        diagram_use_case=diagram_use_case,
        quota_guard=quota_guard,
        question_limiter=question_limiter,
        deep_question_limiter=deep_question_limiter,
        trust_proxy_headers=trust_proxy_headers,
    )