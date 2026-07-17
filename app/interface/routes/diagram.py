"""
POST /api/diagram — Diagram Agent.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.application.use_cases.generate_diagram import RepoNotIndexedError
from app.interface.schemas import DiagramRequest, DiagramResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/diagram", response_model=DiagramResponse)
async def generate_diagram(payload: DiagramRequest, request: Request) -> DiagramResponse:
    container = request.app.state.container

    try:
        spec = await container.diagram_use_case.execute(
            repo_full_name=payload.repo_full_name,
            commit_sha=payload.commit_sha,
        )
    except RepoNotIndexedError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.exception("Unexpected error generating diagram")
        raise HTTPException(status_code=500, detail="Failed to generate diagram.") from e

    return DiagramResponse(mermaid_syntax=spec.mermaid_syntax, modules=spec.modules)