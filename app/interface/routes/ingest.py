"""
POST /api/ingest — triggers the Explorer Agent's ingestion pipeline.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.application.use_cases.ingest_repo import IngestionLimitExceeded
from app.application.interfaces.github_client import GitHubRateLimitError
from app.infrastructure.llm.quota_aware_llm_client import DailyQuotaExceededError
from app.interface.schemas import IngestRequest, IngestResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/ingest", response_model=IngestResponse)
async def ingest_repo(payload: IngestRequest, request: Request) -> IngestResponse:
    container = request.app.state.container

    try:
        manifest, was_cached = await container.ingest_use_case.execute(payload.repo_url)
    except ValueError as e:
        # Covers RepoUrl validation failures (our SSRF guard) — bad
        # input, not a server problem.
        raise HTTPException(status_code=400, detail=str(e)) from e
    except IngestionLimitExceeded as e:
        raise HTTPException(status_code=413, detail=str(e)) from e
    except GitHubRateLimitError as e:
        raise HTTPException(
            status_code=429,
            detail=f"GitHub API rate limit hit. Retry after {e.retry_after_seconds}s.",
        ) from e
    except DailyQuotaExceededError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        logger.exception("Unexpected error during ingestion")
        raise HTTPException(status_code=500, detail="Ingestion failed unexpectedly.") from e

    return IngestResponse(
        repo_full_name=manifest.repo_url.full_name,
        commit_sha=manifest.commit_sha,
        file_count=manifest.file_count,
        detected_stack=manifest.detected_stack,
        summary=manifest.summary or "",
        was_cached=was_cached,
        skipped_file_count=manifest.skipped_file_count,
    )