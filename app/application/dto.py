"""
DTOs: shapes that cross the boundary between the interface layer
(FastAPI routes) and the application layer (use cases).

Why separate from domain entities: entities describe the business
concepts (RepoManifest, Question, etc). DTOs describe what a specific
request/response looks like over HTTP. Keeping them separate means a
future API version change (e.g. adding a field to a response) never
forces a change to a core entity, and vice versa.
"""
from dataclasses import dataclass, field

from app.domain.entities import Answer, DiagramSpec, RepoManifest
from app.domain.value_objects import QuestionMode


# ---------- Ingestion ----------

@dataclass(frozen=True)
class IngestRepoRequest:
    """What the /ingest route receives, before it's turned into a RepoUrl."""
    repo_url: str


@dataclass(frozen=True)
class IngestRepoResponse:
    repo_full_name: str
    commit_sha: str
    file_count: int
    detected_stack: list[str]
    summary: str
    was_cached: bool  # True if we served an existing manifest, no re-ingestion


# ---------- Ask / Explainer ----------

@dataclass(frozen=True)
class AskQuestionRequest:
    repo_full_name: str  # must already be ingested
    commit_sha: str
    question_text: str
    mode: QuestionMode = QuestionMode.QUICK


@dataclass(frozen=True)
class AskQuestionResponse:
    answer_text: str
    citations: list[str]  # stringified Citation objects, e.g. "app/main.py:10-25"
    confidence: float
    mode_used: str
    steps_taken: int


# ---------- Diagram ----------

@dataclass(frozen=True)
class GenerateDiagramRequest:
    repo_full_name: str
    commit_sha: str


@dataclass(frozen=True)
class GenerateDiagramResponse:
    mermaid_syntax: str
    modules: list[str]
    granularity: str
    total_files: int


# ---------- Mappers: entity -> response DTO ----------
# Keeping these as free functions (not methods on the entity) is the
# point: domain entities stay ignorant of "how they're serialized for
# HTTP" — that knowledge lives here, in application, not in domain.

def manifest_to_response(manifest: RepoManifest, *, was_cached: bool) -> IngestRepoResponse:
    return IngestRepoResponse(
        repo_full_name=manifest.repo_url.full_name,
        commit_sha=manifest.commit_sha,
        file_count=manifest.file_count,
        detected_stack=list(manifest.detected_stack),
        summary=manifest.summary or "",
        was_cached=was_cached,
    )

def answer_to_response(answer: Answer) -> AskQuestionResponse:
    return AskQuestionResponse(
        answer_text=answer.text,
        citations=[str(c) for c in answer.citations],
        confidence=answer.confidence.value,
        mode_used=answer.mode_used.value,
        steps_taken=answer.steps_taken,
    )


def diagram_to_response(spec: DiagramSpec) -> GenerateDiagramResponse:
    return GenerateDiagramResponse(
        mermaid_syntax=spec.mermaid_syntax,
        modules=list(spec.modules),
        granularity=spec.granularity,
        total_files=spec.total_files,
    )