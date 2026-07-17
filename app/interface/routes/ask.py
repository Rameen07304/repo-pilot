"""
POST /api/ask — Explainer Agent, quick or deep mode.

Rate limiting happens HERE, before any use case executes — a rejected
request costs zero LLM calls. Two independent per-IP limiters: a
looser one for quick questions, a tighter one for deep investigations
(since deep mode costs up to 5x a quick question).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.application.use_cases.answer_question import RepoNotIngestedError
from app.domain.entities import Question
from app.domain.value_objects import QuestionMode
from app.infrastructure.llm.quota_aware_llm_client import DailyQuotaExceededError
from app.interface.schemas import AskRequest, AskResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_client_ip(request: Request) -> str:
    # Behind a reverse proxy (HF Spaces), the real client IP is often
    # in X-Forwarded-For. Fall back to the direct connection IP if
    # that header isn't present. Take the FIRST address only — the
    # leftmost entry is the original client, later entries can be
    # appended by intermediate proxies and aren't trustworthy for
    # identifying the original requester.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/api/ask", response_model=AskResponse)
async def ask_question(payload: AskRequest, request: Request) -> AskResponse:
    container = request.app.state.container
    client_ip = _get_client_ip(request)

    mode = QuestionMode.DEEP if payload.deep_mode else QuestionMode.QUICK

    # Rate limit check BEFORE spending any LLM call.
    if mode == QuestionMode.DEEP:
        allowed, remaining = container.deep_question_limiter.check_and_increment(client_ip)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Deep investigation rate limit reached for your IP this hour. Try again later, or use quick mode.",
            )
    else:
        allowed, remaining = container.question_limiter.check_and_increment(client_ip)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Question rate limit reached for your IP this hour. Try again later.",
            )

    try:
        question = Question(text=payload.question, mode=mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        # NOTE: deep mode isn't implemented yet (Step 5, upcoming) —
        # for now both modes route through quick-mode logic. Once
        # investigation_graph.py exists, this branches to it instead.
        answer = await container.ask_use_case.execute(
            question=question,
            repo_full_name=payload.repo_full_name,
            commit_sha=payload.commit_sha,
        )
    except RepoNotIngestedError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except DailyQuotaExceededError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        logger.exception("Unexpected error answering question")
        raise HTTPException(status_code=500, detail="Failed to answer question.") from e

    return AskResponse(
        answer_text=answer.text,
        citations=[str(c) for c in answer.citations],
        confidence=answer.confidence.value,
        mode_used=answer.mode_used.value,
        steps_taken=answer.steps_taken,
    )