"""
POST /api/code-health — maintainability report card.

Entirely computed from cached parsed data: no LLM call, no GitHub
call, so it is not rate-limited beyond requiring an ingested repo.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.application.use_cases.analyze_code_health import RepoNotIndexedError
from app.interface.schemas import CodeHealthFindingResponse, CodeHealthRequest, CodeHealthResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/code-health", response_model=CodeHealthResponse)
async def code_health(payload: CodeHealthRequest, request: Request) -> CodeHealthResponse:
    container = request.app.state.container
    try:
        report = await container.code_health_use_case.execute(
            repo_full_name=payload.repo_full_name,
            commit_sha=payload.commit_sha,
        )
    except RepoNotIndexedError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.exception("Unexpected error computing code health")
        raise HTTPException(status_code=500, detail="Failed to compute code health.") from e

    return CodeHealthResponse(
        score=report.score,
        grade=report.grade,
        source_file_count=report.source_file_count,
        test_file_count=report.test_file_count,
        symbol_count=report.symbol_count,
        findings=[
            CodeHealthFindingResponse(
                check=f.check, severity=f.severity, title=f.title, detail=f.detail,
                files=f.files, symbol=f.symbol, line=f.line, metric=f.metric,
            )
            for f in report.findings
        ],
        deductions=report.deductions,
    )
