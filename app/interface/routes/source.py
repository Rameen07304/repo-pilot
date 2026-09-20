"""
POST /api/file — the source behind a clicked diagram node.

No LLM and no network: this reads the already-cached manifest, so it
is not rate limited. The path is only ever looked up in that manifest's
file list — it never touches the filesystem, so a traversal-shaped
path simply misses and 404s.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.application.use_cases.get_file_source import (
    FileNotFoundInRepoError,
    RepoNotIngestedError,
)
from app.interface.schemas import FileSourceRequest, FileSourceResponse, FileSymbolResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/file", response_model=FileSourceResponse)
async def get_file_source(payload: FileSourceRequest, request: Request) -> FileSourceResponse:
    container = request.app.state.container

    try:
        source = await container.file_source_use_case.execute(
            repo_full_name=payload.repo_full_name,
            commit_sha=payload.commit_sha,
            file_path=payload.file_path,
        )
    except RepoNotIngestedError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except FileNotFoundInRepoError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.exception("Unexpected error loading file source")
        raise HTTPException(status_code=500, detail="Failed to load the file.") from e

    return FileSourceResponse(
        path=source.path,
        content=source.content,
        language=source.language,
        size_bytes=source.size_bytes,
        line_count=source.line_count,
        truncated=source.truncated,
        symbols=[FileSymbolResponse(**s) for s in source.symbols],
        imports=source.imports,
        imported_by=source.imported_by,
    )
