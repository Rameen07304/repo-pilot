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
    persona: str = Field(default="newcomer", pattern="^(newcomer|senior|product)$")


class AskResponse(BaseModel):
    answer_text: str
    citations: list[str]
    confidence: float
    mode_used: str
    steps_taken: int
    investigation_log: list[str] = []


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
    # Mermaid node id -> the real path it was drawn from ("N3":
    # "app/main.py"). The rendered SVG only carries the short id, so
    # this is what lets a click on a node resolve back to a file.
    node_paths: dict[str, str] = {}


class OnboardingRequest(BaseModel):
    repo_full_name: str = Field(..., max_length=200)
    commit_sha: str = Field(..., max_length=100)


class ReadingStepResponse(BaseModel):
    file_path: str
    why_it_matters: str
    what_to_understand: str
    depends_on: list[str] = []
    depended_on_by: list[str] = []
    key_symbols: list[str] = []
    is_entry_point: bool = False


class OnboardingResponse(BaseModel):
    repo_full_name: str
    commit_sha: str
    overview: str
    entry_points: list[str]
    reading_order: list[ReadingStepResponse]
    suggested_questions: list[str]
    graph_available: bool = True


class FileSourceRequest(BaseModel):
    repo_full_name: str = Field(..., max_length=200)
    commit_sha: str = Field(..., max_length=100)
    file_path: str = Field(..., max_length=500)


class FileSymbolResponse(BaseModel):
    name: str
    kind: str
    start_line: int
    end_line: int


class FileSourceResponse(BaseModel):
    path: str
    content: str
    language: str | None = None
    size_bytes: int = 0
    line_count: int = 0
    truncated: bool = False
    symbols: list[FileSymbolResponse] = []
    imports: list[str] = []
    imported_by: list[str] = []


class ImpactRequest(BaseModel):
    repo_full_name: str = Field(..., max_length=200)
    commit_sha: str = Field(..., max_length=100)
    file_path: str = Field(..., max_length=500)
    symbol: str | None = Field(default=None, max_length=200)


class ImpactNodeResponse(BaseModel):
    path: str
    depth: int
    symbols: list[str] = []
    via: list[str] = []
    is_direct: bool = False


class ImpactResponse(BaseModel):
    target_path: str
    target_symbol: str | None = None
    direct: list[ImpactNodeResponse] = []
    indirect: list[ImpactNodeResponse] = []
    callers: list[ImpactNodeResponse] = []
    entry_points_reached: list[str] = []
    affected_files: list[str] = []
    max_depth: int = 0
    summary: str = ""
    truncated: bool = False


class ErrorResponse(BaseModel):
    error: str
    detail: str

class CodeHealthRequest(BaseModel):
    repo_full_name: str = Field(..., max_length=200)
    commit_sha: str = Field(..., max_length=100)


class CodeHealthFindingResponse(BaseModel):
    check: str
    severity: str
    title: str
    detail: str
    files: list[str] = []
    symbol: str | None = None
    line: int | None = None
    metric: int | None = None


class CodeHealthResponse(BaseModel):
    score: int
    grade: str
    source_file_count: int
    test_file_count: int
    symbol_count: int
    findings: list[CodeHealthFindingResponse] = []
    deductions: dict[str, int] = {}
