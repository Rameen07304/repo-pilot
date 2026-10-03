"""
Pydantic schemas for the HTTP boundary. Kept separate from
application/dto.py (plain dataclasses) — Pydantic validation is an
interface-layer concern; application/use case code stays framework-free.
"""
from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    repo_url: str = Field(..., max_length=300, description="e.g. https://github.com/owner/repo")


class DetectedTechResponse(BaseModel):
    name: str
    kind: str
    # The file that established it, so the claim is checkable like the rest.
    evidence: str


class IngestResponse(BaseModel):
    repo_full_name: str
    commit_sha: str
    file_count: int
    detected_stack: list[str]
    stack_detail: list[DetectedTechResponse] = []
    summary: str
    was_cached: bool
    skipped_file_count: int = 0
    # Languages in this repo whose grammar would not load here. Those
    # files were chunked generically, so answers about them are weaker.
    degraded_languages: list[str] = []


class ConversationTurnRequest(BaseModel):
    question: str = Field(..., max_length=500)
    answer: str = Field(default="", max_length=4000)
    cited_files: list[str] = Field(default_factory=list, max_length=20)


class AskRequest(BaseModel):
    repo_full_name: str = Field(..., max_length=200)
    commit_sha: str = Field(..., max_length=100)
    question: str = Field(..., min_length=1, max_length=500)
    deep_mode: bool = False
    persona: str = Field(default="newcomer", pattern="^(newcomer|senior|product|learner)$")
    # Conversation history, oldest first, supplied by the client. Never
    # stored server-side: it is used for this request only.
    history: list[ConversationTurnRequest] = Field(default_factory=list, max_length=10)


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
    detail: str = Field(default="balanced", pattern="^(overview|balanced|detailed)$")


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


class HandbookRequest(BaseModel):
    repo_full_name: str = Field(..., max_length=200)
    commit_sha: str = Field(..., max_length=100)


class ArchitectureExportRequest(BaseModel):
    repo_full_name: str = Field(..., max_length=200)
    commit_sha: str = Field(..., max_length=100)


class ArchitectureEvidenceEdgeResponse(BaseModel):
    from_path: str
    to_path: str
    # Every line in from_path that imports to_path, ascending. Empty only
    # when no single line accounts for the edge -- never as a shorthand
    # for "not checked".
    lines: list[int] = []


class ArchitectureEvidenceResponse(BaseModel):
    """
    The import graph as data, with the line each edge was resolved from.

    The HTML export has carried this all along but only ever rendered it.
    An agent asking "what actually depends on this" needs the citation,
    not a picture of it.
    """
    repo_full_name: str
    commit_sha: str
    edges: list[ArchitectureEvidenceEdgeResponse]
    edges_total: int
    edges_evidenced: int
    files_in_repo: int
    files_skipped: int
    unparsed_extensions: dict[str, int] = {}
    degraded_languages: list[str] = []
    detected_stack: list[str] = []
    truncated: bool = False


class CallStepResponse(BaseModel):
    depth: int
    from_file: str
    from_symbol: str
    to_file: str
    to_symbol: str
    # The call SITE, inside from_file -- not the callee's definition.
    line: int
    is_recursive: bool = False


class CallSequenceDataResponse(BaseModel):
    """
    A traced call sequence as data.

    /api/sequence returns a standalone page, which is right for a person
    and unreadable to an agent. Same trace, same use case, no markup.
    """
    repo_full_name: str
    commit_sha: str
    entry_file: str
    entry_symbol: str
    steps: list[CallStepResponse] = []
    # Calls that left the repository, and calls whose name matched
    # several symbols. Both are the reason a step is NOT in the list, so
    # an agent that cannot see them will read the trace as complete.
    unresolved_calls: dict[str, int] = {}
    ambiguous_calls: dict[str, int] = {}
    truncated: bool = False
    max_depth: int = 0


class ChangedEdgeResponse(BaseModel):
    from_path: str
    to_path: str
    lines: list[int] = []


class MovedNodeResponse(BaseModel):
    from_path: str
    to_path: str


class PullRequestDataResponse(BaseModel):
    """What a pull request changes structurally, as data rather than a page."""
    repo_full_name: str
    number: int
    title: str
    state: str
    author: str = ""
    base_sha: str
    head_sha: str
    added_nodes: list[str] = []
    removed_nodes: list[str] = []
    moved_nodes: list[MovedNodeResponse] = []
    added_edges: list[ChangedEdgeResponse] = []
    removed_edges: list[ChangedEdgeResponse] = []
    rerouted_nodes: list[str] = []
    unchanged_edge_count: int = 0


class ArchitectureDeltaRequest(BaseModel):
    repo_full_name: str = Field(..., max_length=200)
    base_sha: str = Field(..., max_length=100)
    head_sha: str = Field(..., max_length=100)


class PullRequestExplainRequest(BaseModel):
    pull_request_url: str = Field(..., max_length=400)


class CallSequenceRequest(BaseModel):
    repo_full_name: str = Field(..., max_length=200)
    commit_sha: str = Field(..., max_length=100)
    entry_file: str = Field(..., max_length=500)
    entry_symbol: str = Field(..., max_length=200)


class FlowQuestionRequest(BaseModel):
    repo_full_name: str = Field(..., max_length=200)
    commit_sha: str = Field(..., max_length=100)
    question: str = Field(..., min_length=3, max_length=500)


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
    # False when no source files were gradable. Without it a caller sees
    # grade A with a score of 100 and reads it as a clean bill of health
    # for a repository nothing was ever checked in.
    was_analysable: bool = True
    score: int
    grade: str
    source_file_count: int
    test_file_count: int
    symbol_count: int
    findings: list[CodeHealthFindingResponse] = []
    deductions: dict[str, int] = {}


class AgentToolingRequest(BaseModel):
    repo_full_name: str = Field(..., max_length=200)
    commit_sha: str = Field(..., max_length=100)


class AgentAssetResponse(BaseModel):
    kind: str
    kind_label: str
    how_to_use: str
    path: str
    name: str
    description: str
    body_excerpt: str
    has_frontmatter: bool
    size_bytes: int


class AgentToolingResponse(BaseModel):
    assets: list[AgentAssetResponse]
