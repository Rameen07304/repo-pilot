"""
POST /api/handbook — the whole repository as one Markdown document.

Shares the ordinary per-IP question limiter, because the onboarding guide
underneath it makes at most one bounded LLM call; the maintainability
section costs nothing beyond cached data.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.application.use_cases.generate_onboarding import RepoNotIngestedError
from app.infrastructure.llm.quota_aware_llm_client import DailyQuotaExceededError
from app.interface.client_ip import get_client_ip
from app.interface.handbook_markdown import render_handbook_markdown
from app.interface.schemas import HandbookRequest

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/handbook", response_class=PlainTextResponse)
async def handbook(payload: HandbookRequest, request: Request) -> PlainTextResponse:
    container = request.app.state.container
    client_ip = get_client_ip(request, trust_proxy_headers=container.trust_proxy_headers)

    allowed, _remaining = container.question_limiter.check_and_increment(client_ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit reached for your IP this hour. Try again later.",
        )

    try:
        doc = await container.handbook_use_case.execute(
            repo_full_name=payload.repo_full_name,
            commit_sha=payload.commit_sha,
        )
    except RepoNotIngestedError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except DailyQuotaExceededError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        logger.exception("Unexpected error building the handbook")
        raise HTTPException(status_code=500, detail="Failed to build the handbook.") from e

    # The repo name reaches a Content-Disposition header, so keep it to
    # characters that cannot break out of the quoted filename.
    safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in doc.repo_full_name)
    return PlainTextResponse(
        render_handbook_markdown(doc),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}-handbook.md"'},
    )
