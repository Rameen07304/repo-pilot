"""
The call sequence and the pull request explanation, as data.

POST /api/sequence/steps        — what runs, step by step
POST /api/architecture/pull/data — what a pull request changes

Both already exist as endpoints that return a standalone HTML document,
which is the right answer for a person and an unreadable one for an
agent: an MCP client would be handed a few hundred lines of markup to
extract four facts from. These run the same use cases and return the
structure the renderers draw from, so the page and the JSON cannot
disagree.

Each one carries its own gaps -- calls that left the repository, names
that matched several symbols, a trace that hit its depth cap. A person
reads those in the margin of the document. An agent only knows what the
fields tell it, so leaving them out would turn a partial trace into a
confident one.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.application.interfaces.github_client import (
    GitHubRateLimitError,
    GitHubRepoNotFoundError,
)
from app.application.use_cases.explain_pull_request import NotAPullRequestUrlError
from app.application.use_cases.export_architecture import RepoNotExportableError
from app.application.use_cases.ingest_repo import IngestionLimitExceeded
from app.application.use_cases.trace_call_sequence import SequenceNotTraceableError
from app.interface.client_ip import get_client_ip
from app.interface.schemas import (
    CallSequenceDataResponse,
    CallSequenceRequest,
    CallStepResponse,
    ChangedEdgeResponse,
    MovedNodeResponse,
    PullRequestDataResponse,
    PullRequestExplainRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# A deep trace can run to thousands of steps. Past this an agent is
# paying context for a tail it will not read, so it is cut and says so.
MAX_STEPS = 300


@router.post("/api/sequence/steps", response_model=CallSequenceDataResponse)
async def sequence_steps(
    payload: CallSequenceRequest, request: Request
) -> CallSequenceDataResponse:
    container = request.app.state.container
    client_ip = get_client_ip(request, trust_proxy_headers=container.trust_proxy_headers)

    # Parse-only, like the HTML version: no model call, so the document
    # budget rather than the question one.
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
        raise HTTPException(status_code=500, detail="Failed to trace the call sequence.") from e

    return CallSequenceDataResponse(
        repo_full_name=traced.repo_full_name,
        commit_sha=traced.commit_sha,
        entry_file=traced.entry_file,
        entry_symbol=traced.entry_symbol,
        steps=[
            CallStepResponse(
                depth=s.depth,
                from_file=s.from_file,
                from_symbol=s.from_symbol,
                to_file=s.to_file,
                to_symbol=s.to_symbol,
                line=s.line,
                is_recursive=s.is_recursive,
            )
            for s in traced.steps[:MAX_STEPS]
        ],
        unresolved_calls=traced.unresolved_calls,
        ambiguous_calls=traced.ambiguous_calls,
        # Either the trace itself hit a cap, or this reply did.
        truncated=traced.truncated or len(traced.steps) > MAX_STEPS,
        max_depth=traced.max_depth,
    )


@router.post("/api/architecture/pull/data", response_model=PullRequestDataResponse)
async def pull_request_data(
    payload: PullRequestExplainRequest, request: Request
) -> PullRequestDataResponse:
    container = request.app.state.container
    client_ip = get_client_ip(request, trust_proxy_headers=container.trust_proxy_headers)

    # Same budget as the HTML version, and for the same reason: an unseen
    # pull request ingests both of its ends, which is real work.
    allowed, _remaining = container.question_limiter.check_and_increment(client_ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit reached for your IP this hour. Try again later.",
        )

    try:
        delta, pull = await container.pull_request_use_case.execute(
            pull_request_url=payload.pull_request_url
        )
    except NotAPullRequestUrlError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except (RepoNotExportableError, GitHubRepoNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except IngestionLimitExceeded as e:
        raise HTTPException(status_code=413, detail=str(e)) from e
    except GitHubRateLimitError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ValueError as e:
        # RepoUrl's SSRF guard and the private-repo refusal both land here.
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Unexpected error explaining a pull request")
        raise HTTPException(status_code=500, detail="Failed to explain that pull request.") from e

    def edges(changed) -> list[ChangedEdgeResponse]:
        return [
            ChangedEdgeResponse(from_path=e.from_path, to_path=e.to_path, lines=e.lines)
            for e in changed
        ]

    return PullRequestDataResponse(
        repo_full_name=delta.repo_full_name,
        number=pull.number,
        title=pull.title,
        state=pull.state,
        author=pull.author,
        base_sha=pull.base_sha,
        head_sha=pull.head_sha,
        added_nodes=delta.added_nodes,
        removed_nodes=delta.removed_nodes,
        moved_nodes=[
            MovedNodeResponse(from_path=m.from_path, to_path=m.to_path)
            for m in delta.moved_nodes
        ],
        added_edges=edges(delta.added_edges),
        removed_edges=edges(delta.removed_edges),
        rerouted_nodes=delta.rerouted_nodes,
        unchanged_edge_count=delta.unchanged_edge_count,
    )
