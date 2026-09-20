"""
POST /api/onboarding — the Onboarding Guide Agent.

One bounded LLM call at most (narration only), so this shares the
ordinary per-IP question limiter rather than the tighter deep one.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.application.use_cases.generate_onboarding import RepoNotIngestedError
from app.infrastructure.llm.quota_aware_llm_client import DailyQuotaExceededError
from app.interface.client_ip import get_client_ip
from app.interface.schemas import OnboardingRequest, OnboardingResponse, ReadingStepResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/onboarding", response_model=OnboardingResponse)
async def generate_onboarding(payload: OnboardingRequest, request: Request) -> OnboardingResponse:
    container = request.app.state.container
    client_ip = get_client_ip(request, trust_proxy_headers=container.trust_proxy_headers)

    allowed, _remaining = container.question_limiter.check_and_increment(client_ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit reached for your IP this hour. Try again later.",
        )

    try:
        guide = await container.onboarding_use_case.execute(
            repo_full_name=payload.repo_full_name,
            commit_sha=payload.commit_sha,
        )
    except RepoNotIngestedError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except DailyQuotaExceededError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        logger.exception("Unexpected error generating onboarding guide")
        raise HTTPException(status_code=500, detail="Failed to build the onboarding guide.") from e

    return OnboardingResponse(
        repo_full_name=guide.repo_full_name,
        commit_sha=guide.commit_sha,
        overview=guide.overview,
        entry_points=guide.entry_points,
        reading_order=[
            ReadingStepResponse(
                file_path=step.file_path,
                why_it_matters=step.why_it_matters,
                what_to_understand=step.what_to_understand,
                depends_on=step.depends_on,
                depended_on_by=step.depended_on_by,
                key_symbols=step.key_symbols,
                is_entry_point=step.is_entry_point,
            )
            for step in guide.reading_order
        ],
        suggested_questions=guide.suggested_questions,
        graph_available=guide.graph_available,
    )
