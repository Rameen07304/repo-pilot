"""
Call sequence: what runs when one function runs.

  GET  /api/sequence/entries — functions worth tracing, best first
  POST /api/sequence         — the trace, as one standalone page
  POST /api/sequence/flow    — the same page, from a question

The first two read the cached parse and run no model, so they are on
the generous document limiter and not on the daily LLM budget. /flow
makes one bounded call to choose a starting point, so it is on the
question limiter and the budget like any other question. The trace itself
is parse-driven either way.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from app.application.use_cases.trace_call_sequence import SequenceNotTraceableError
from app.application.use_cases.trace_flow_for_question import NoFlowFoundError
from app.infrastructure.llm.quota_aware_llm_client import DailyQuotaExceededError
from app.interface.client_ip import get_client_ip
from app.interface.limits import enforce_daily_budget
from app.interface.schemas import CallSequenceRequest, FlowQuestionRequest
from app.interface.sequence_html import render_sequence_html

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/sequence/entries")
async def sequence_entries(
    request: Request,
    repo_full_name: str = Query(..., max_length=200),
    commit_sha: str = Query(..., max_length=100),
) -> dict:
    container = request.app.state.container
    try:
        entries = await container.sequence_use_case.suggest_entries(
            repo_full_name=repo_full_name, commit_sha=commit_sha
        )
    except SequenceNotTraceableError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"repo_full_name": repo_full_name, "entries": entries}


@router.post("/api/sequence", response_class=HTMLResponse)
async def sequence(payload: CallSequenceRequest, request: Request) -> HTMLResponse:
    container = request.app.state.container
    client_ip = get_client_ip(request, trust_proxy_headers=container.trust_proxy_headers)

    allowed, _remaining = container.document_limiter.check_and_increment(client_ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many documents generated from your IP this hour. Try again shortly.",
        )

    try:
        traced = await container.sequence_use_case.execute(
            repo_full_name=payload.repo_full_name,
            commit_sha=payload.commit_sha,
            entry_file=payload.entry_file,
            entry_symbol=payload.entry_symbol,
        )
    except SequenceNotTraceableError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.exception("Unexpected error tracing the call sequence")
        raise HTTPException(
            status_code=500, detail="Failed to trace the call sequence."
        ) from e

    # Both reach a Content-Disposition header, so keep them to characters
    # that cannot break out of the quoted filename.
    def safe(text: str) -> str:
        return "".join(c if c.isalnum() or c in "-_" else "-" for c in text)

    name = f"{safe(traced.repo_full_name)}-{safe(traced.entry_symbol)}-sequence.html"
    return HTMLResponse(
        render_sequence_html(traced),
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/api/sequence/flow", response_class=HTMLResponse)
async def sequence_flow(payload: FlowQuestionRequest, request: Request) -> HTMLResponse:
    container = request.app.state.container
    client_ip = get_client_ip(request, trust_proxy_headers=container.trust_proxy_headers)

    enforce_daily_budget(container, client_ip)

    allowed, _remaining = container.question_limiter.check_and_increment(client_ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit reached for your IP this hour. Try again later.",
        )

    try:
        flow = await container.flow_use_case.execute(
            repo_full_name=payload.repo_full_name,
            commit_sha=payload.commit_sha,
            question=payload.question,
        )
    except NoFlowFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except SequenceNotTraceableError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except DailyQuotaExceededError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        logger.exception("Unexpected error tracing a flow for a question")
        raise HTTPException(
            status_code=500, detail="Failed to trace a flow for that question."
        ) from e

    def safe(text: str) -> str:
        return "".join(c if c.isalnum() or c in "-_" else "-" for c in text)

    name = f"{safe(flow.sequence.repo_full_name)}-{safe(flow.entry_symbol)}-flow.html"
    return HTMLResponse(
        render_sequence_html(flow.sequence, flow=flow),
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
