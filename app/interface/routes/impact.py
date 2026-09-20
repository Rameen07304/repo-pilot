"""
POST /api/impact — "what breaks if I change X?"

The blast radius itself is pure graph traversal over already-cached
data. Only the closing summary sentence costs an LLM call, and it
falls back to computed text when that fails — so this shares the
ordinary question limiter rather than the deep one.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.application.use_cases.analyze_impact import RepoNotIndexedError, TargetNotFoundError
from app.infrastructure.llm.quota_aware_llm_client import DailyQuotaExceededError
from app.interface.client_ip import get_client_ip
from app.interface.schemas import ImpactNodeResponse, ImpactRequest, ImpactResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _node(node) -> ImpactNodeResponse:
    return ImpactNodeResponse(
        path=node.path,
        depth=node.depth,
        symbols=node.symbols,
        via=node.via,
        is_direct=node.is_direct,
    )


@router.post("/api/impact", response_model=ImpactResponse)
async def analyze_impact(payload: ImpactRequest, request: Request) -> ImpactResponse:
    container = request.app.state.container
    client_ip = get_client_ip(request, trust_proxy_headers=container.trust_proxy_headers)

    allowed, _remaining = container.question_limiter.check_and_increment(client_ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit reached for your IP this hour. Try again later.",
        )

    try:
        report = await container.impact_use_case.execute(
            repo_full_name=payload.repo_full_name,
            commit_sha=payload.commit_sha,
            file_path=payload.file_path,
            symbol=payload.symbol,
        )
    except RepoNotIndexedError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except TargetNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except DailyQuotaExceededError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        logger.exception("Unexpected error running impact analysis")
        raise HTTPException(status_code=500, detail="Failed to analyze impact.") from e

    return ImpactResponse(
        target_path=report.target_path,
        target_symbol=report.target_symbol,
        direct=[_node(n) for n in report.direct],
        indirect=[_node(n) for n in report.indirect],
        callers=[_node(n) for n in report.callers],
        entry_points_reached=report.entry_points_reached,
        affected_files=report.affected_files,
        max_depth=report.max_depth,
        summary=report.summary,
        truncated=report.truncated,
    )
