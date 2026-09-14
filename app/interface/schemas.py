"""
Pydantic schemas for the HTTP boundary. Kept separate from
application/dto.py (plain dataclasses) — Pydantic validation is an
interface-layer concern; application/use case code stays framework-free.
"""
from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    repo_url: str = Field(..., max_length=300, description="e.g. https://github.com/owner/repo")


class IngestResponse(BaseModel):
    repo_full_name: str
    commit_sha: str
    file_count: int
    detected_stack: list[str]
    summary: str
    was_cached: bool
    skipped_file_count: int = 0


class AskRequest(BaseModel):
    repo_full_name: str = Field(..., max_length=200)
    commit_sha: str = Field(..., max_length=100)
    question: str = Field(..., min_length=1, max_length=500)
    deep_mode: bool = False


class AskResponse(BaseModel):
    answer_text: str
    citations: list[str]
    confidence: float
    mode_used: str
    steps_taken: int


class DiagramRequest(BaseModel):
    repo_full_name: str = Field(..., max_length=200)
    commit_sha: str = Field(..., max_length=100)
    orientation: str = Field(default="TD", pattern="^(TD|LR)$")
    group_by_folder: bool = True


class DiagramResponse(BaseModel):
    mermaid_syntax: str
    modules: list[str]
    granularity: str = "file"
    total_files: int = 0


class ErrorResponse(BaseModel):
    error: str
    detail: str