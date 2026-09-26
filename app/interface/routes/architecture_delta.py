"""
Architecture delta between two ingested commits.

  GET  /api/architecture/commits — which commits of a repo can be compared
  POST /api/architecture/delta   — the comparison, as one standalone page

Neither runs a model, so neither touches the daily LLM budget. The
comparison builds a whole document from two parses, so it takes the
per-IP hourly limiter; the listing is a cheap indexed read and does not.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from app.application.use_cases.compare_architecture import SameCommitError
from app.application.use_cases.export_architecture import RepoNotExportableError
from app.interface.architecture_delta_html import render_architecture_delta_html
from app.interface.client_ip import get_client_ip
from app.interface.schemas import ArchitectureDeltaRequest

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/architecture/commits")
async def comparable_commits(
    request: Request, repo_full_name: str = Query(..., max_length=200)
) -> dict:
    container = request.app.state.container
    commits = await container.cache.list_commits(repo_full_name)
    return {
        "repo_full_name": repo_full_name,
        "commits": [
            {
                "commit_sha": c.commit_sha,
                "short_sha": c.commit_sha[:7],
                "cached_at": c.cached_at.isoformat(),
            }
            for c in commits
        ],
    }


@router.post("/api/architecture/delta", response_class=HTMLResponse)
async def architecture_delta(
    payload: ArchitectureDeltaRequest, request: Request
) -> HTMLResponse:
    container = request.app.state.container
    client_ip = get_client_ip(request, trust_proxy_headers=container.trust_proxy_headers)

    allowed, _remaining = container.question_limiter.check_and_increment(client_ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit reached for your IP this hour. Try again later.",
        )

    try:
        delta = await container.architecture_compare_use_case.execute(
            repo_full_name=payload.repo_full_name,
            base_sha=payload.base_sha,
            head_sha=payload.head_sha,
        )
    except SameCommitError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RepoNotExportableError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.exception("Unexpected error building the architecture delta")
        raise HTTPException(
            status_code=500, detail="Failed to build the architecture delta."
        ) from e

    # The repo name reaches a Content-Disposition header, so keep it to
    # characters that cannot break out of the quoted filename.
    safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in delta.repo_full_name)
    name = f"{safe_name}-delta-{delta.base_sha[:7]}-{delta.head_sha[:7]}.html"
    return HTMLResponse(
        render_architecture_delta_html(delta),
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
