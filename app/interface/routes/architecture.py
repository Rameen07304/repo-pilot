"""
POST /api/architecture — the import graph as one standalone HTML file.

No model runs behind this, so it does not touch the daily LLM budget. It
does take the per-IP hourly limiter: the response is built from cached
data, but it is a whole document per call and one client should not be
able to spin on it.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.application.use_cases.export_architecture import RepoNotExportableError
from app.interface.architecture_html import render_architecture_html
from app.interface.client_ip import get_client_ip
from app.interface.schemas import ArchitectureExportRequest

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/architecture", response_class=HTMLResponse)
async def architecture(payload: ArchitectureExportRequest, request: Request) -> HTMLResponse:
    container = request.app.state.container
    client_ip = get_client_ip(request, trust_proxy_headers=container.trust_proxy_headers)

    allowed, _remaining = container.question_limiter.check_and_increment(client_ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit reached for your IP this hour. Try again later.",
        )

    try:
        export = await container.architecture_export_use_case.execute(
            repo_full_name=payload.repo_full_name,
            commit_sha=payload.commit_sha,
        )
    except RepoNotExportableError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.exception("Unexpected error building the architecture export")
        raise HTTPException(
            status_code=500, detail="Failed to build the architecture export."
        ) from e

    # The repo name reaches a Content-Disposition header, so keep it to
    # characters that cannot break out of the quoted filename.
    safe_name = "".join(
        c if c.isalnum() or c in "-_" else "-" for c in export.repo_full_name
    )
    short_sha = export.commit_sha[:7] or "unknown"
    return HTMLResponse(
        render_architecture_html(export),
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{safe_name}-architecture-{short_sha}.html"'
            )
        },
    )
