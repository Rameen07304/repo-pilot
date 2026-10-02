"""
POST /api/architecture/evidence — the import graph as data, not a picture.

/api/architecture has always returned a finished HTML document, which is
right for a person and useless to an agent: the thing that makes this
graph worth trusting is that every edge carries the line it was resolved
from, and an agent cannot read that out of rendered markup.

Same use case as the HTML export and /proof, so the three cannot drift
apart. Parse-only -- no model call -- so it takes the document limiter
rather than the question budget.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.application.use_cases.export_architecture import RepoNotExportableError
from app.interface.client_ip import get_client_ip
from app.interface.schemas import (
    ArchitectureEvidenceEdgeResponse,
    ArchitectureEvidenceResponse,
    ArchitectureExportRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# A large repo can resolve several thousand edges. Past this the reply is
# bigger than any agent's context, so it is cut and says it was cut --
# the counts above it are still the real totals.
MAX_EDGES = 400


@router.post("/api/architecture/evidence", response_model=ArchitectureEvidenceResponse)
async def architecture_evidence(
    payload: ArchitectureExportRequest, request: Request
) -> ArchitectureEvidenceResponse:
    container = request.app.state.container
    client_ip = get_client_ip(request, trust_proxy_headers=container.trust_proxy_headers)

    allowed, _remaining = container.document_limiter.check_and_increment(client_ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many documents generated from your IP this hour. Try again shortly.",
        )

    try:
        export = await container.architecture_export_use_case.execute(
            repo_full_name=payload.repo_full_name,
            commit_sha=payload.commit_sha,
        )
    except RepoNotExportableError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.exception("Unexpected error building the architecture evidence")
        raise HTTPException(status_code=500, detail="Failed to build the architecture.") from e

    coverage = export.coverage
    return ArchitectureEvidenceResponse(
        repo_full_name=export.repo_full_name,
        commit_sha=export.commit_sha,
        edges=[
            ArchitectureEvidenceEdgeResponse(
                from_path=e.from_path, to_path=e.to_path, lines=e.lines
            )
            for e in export.edges[:MAX_EDGES]
        ],
        edges_total=len(export.edges),
        edges_evidenced=export.evidenced_edge_count,
        files_in_repo=coverage.files_in_repo,
        files_skipped=coverage.files_skipped,
        unparsed_extensions=coverage.unparsed_extensions,
        degraded_languages=coverage.degraded_languages,
        detected_stack=export.detected_stack,
        truncated=len(export.edges) > MAX_EDGES,
    )
