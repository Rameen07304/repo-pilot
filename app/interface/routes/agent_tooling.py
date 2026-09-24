"""
POST /api/agent-tooling — what AI agent tooling this repository ships.

Computed entirely from cached manifest data: no model, no GitHub call,
so it is not rate-limited beyond requiring an ingested repo.

The strings it returns are lifted verbatim from files written by whoever
owns the repository, and those files exist to steer an agent. They are
data on the way out as much as on the way in: the client must render
them as text.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.application.use_cases.detect_agent_tooling import (
    KIND_LABEL,
    KIND_USE,
    RepoNotIngestedError,
)
from app.interface.schemas import (
    AgentAssetResponse,
    AgentToolingRequest,
    AgentToolingResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/agent-tooling", response_model=AgentToolingResponse)
async def agent_tooling(payload: AgentToolingRequest, request: Request) -> AgentToolingResponse:
    container = request.app.state.container
    try:
        report = await container.agent_tooling_use_case.execute(
            repo_full_name=payload.repo_full_name,
            commit_sha=payload.commit_sha,
        )
    except RepoNotIngestedError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.exception("Unexpected error detecting agent tooling")
        raise HTTPException(status_code=500, detail="Failed to inspect agent tooling.") from e

    return AgentToolingResponse(
        assets=[
            AgentAssetResponse(
                kind=a.kind,
                kind_label=KIND_LABEL.get(a.kind, a.kind),
                how_to_use=KIND_USE.get(a.kind, ""),
                path=a.path,
                name=a.name,
                description=a.description,
                body_excerpt=a.body_excerpt,
                has_frontmatter=a.has_frontmatter,
                size_bytes=a.size_bytes,
            )
            for a in report.assets
        ],
    )
