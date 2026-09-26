"""
Call sequence: what runs when one function runs.

  GET  /api/sequence/entries — functions worth tracing, best first
  POST /api/sequence         — the trace, as one standalone page

No model runs behind either, so neither touches the daily LLM budget.
The trace builds a whole document from the cached parse, so it takes the
per-IP hourly limiter; the listing is a cheap read and does not.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from app.application.use_cases.trace_call_sequence import SequenceNotTraceableError
from app.interface.client_ip import get_client_ip
from app.interface.schemas import CallSequenceRequest
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

    allowed, _remaining = container.question_limiter.check_and_increment(client_ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit reached for your IP this hour. Try again later.",
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
