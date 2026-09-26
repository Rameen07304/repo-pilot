"""
Core entities: the main objects our system operates on.
Pure Python — no FastAPI, no DB, no HTTP. Infrastructure adapts to
these shapes, not the other way around.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.domain.value_objects import Citation, ConfidenceScore, Persona, QuestionMode, RepoUrl


@dataclass
class FileNode:
    """A single file in the repo. content is None until we decide it's worth fetching."""
    path: str
    size_bytes: int
    language: Optional[str] = None
    content: Optional[str] = None
    is_binary: bool = False


@dataclass
class RepoManifest:
    """
    The Explorer Agent's output. This is the shared context that both
    the Explainer and Diagram agents read from — built once per ingestion.
    """
    repo_url: RepoUrl
    commit_sha: str
    files: list[FileNode] = field(default_factory=list)
    detected_stack: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    readme_content: Optional[str] = None
    summary: Optional[str] = None
    skipped_file_count: int = 0  # files that could not be fetched; >0 means this manifest is incomplete
    # Languages present in this repo whose grammar would not load, so those
    # files were chunked generically with no symbols and no import edges.
    degraded_languages: list[str] = field(default_factory=list)
    ingested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def file_count(self) -> int:
        return len(self.files)


@dataclass
class CodeChunk:
    """
    A piece of code that gets embedded and stored for retrieval.
    The actual embedding vector lives in the vector store (infrastructure
    concern) — this just describes what the chunk is and where it's from.
    """
    id: str
    file_path: str
    content: str
    start_line: int
    end_line: int

    def to_citation(self) -> Citation:
        return Citation(
            file_path=self.file_path,
            start_line=self.start_line,
            end_line=self.end_line,
        )


@dataclass
class ConversationTurn:
    """
    One earlier question and the gist of its answer, supplied by the
    client so a follow-up ("and where is that validated?") can be
    understood. History is never stored server-side: it arrives with
    the request and is used for this request only.
    """
    question: str
    answer: str = ""
    cited_files: list[str] = field(default_factory=list)


@dataclass
class Question:
    """A user's question, and which Explainer mode should handle it."""
    text: str
    mode: QuestionMode = QuestionMode.QUICK
    persona: Persona = Persona.NEWCOMER
    history: list[ConversationTurn] = field(default_factory=list)

    def __post_init__(self):
        if not self.text.strip():
            raise ValueError("Question text cannot be empty")

    @property
    def is_follow_up(self) -> bool:
        return bool(self.history)

    def retrieval_text(self) -> str:
        """
        What to embed when searching. A follow-up is often unsearchable
        alone ("and where is that validated?" matches nothing), so it is
        prefixed with the previous question and the files that answer
        cited — real repo terms, which is what the vector search needs.
        The user's own words stay last so they dominate the embedding.
        """
        if not self.history:
            return self.text
        recent = self.history[-1]
        parts = [recent.question]
        if recent.cited_files:
            parts.append(" ".join(recent.cited_files[:3]))
        parts.append(self.text)
        return " ".join(p for p in parts if p.strip())


@dataclass
class Answer:
    """Explainer Agent's output — the result of answering a Question."""
    question: Question
    text: str
    citations: list[Citation] = field(default_factory=list)
    confidence: ConfidenceScore = field(default_factory=lambda: ConfidenceScore(0.5))
    mode_used: QuestionMode = QuestionMode.QUICK
    steps_taken: int = 1  # 1 for quick mode, up to DEEP_INVESTIGATION_MAX_STEPS for deep mode
    investigation_log: list[str] = field(default_factory=list)  # deep mode: one line per search round

@dataclass
class DiagramSpec:
    """The Diagram Agent's output: ready-to-render Mermaid syntax."""
    mermaid_syntax: str
    modules: list[str] = field(default_factory=list)
    granularity: str = "file"  # "file" or "folder" — folder when a big repo was aggregated for readability
    total_files: int = 0  # files in the full import graph before any capping/aggregation
    # Mermaid node id -> the real repo path it was drawn from. The
    # rendered SVG only carries the short id, so this is what lets the
    # UI resolve a clicked node back to a file.
    node_paths: dict[str, str] = field(default_factory=dict)
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

@dataclass
class ParsedSymbol:
    """
    One function/class/method tree-sitter found in a file, plus the
    names it calls (used for name-based call tracing, with import-aware
    disambiguation — see CodeParser.trace_calls).
    """
    name: str
    kind: str  # "function" | "class" | "method"
    file_path: str
    start_line: int
    end_line: int
    calls: list[str] = field(default_factory=list)
    # The same calls, each with the line inside this symbol where it is
    # written: (callee_name, line). Kept beside `calls` rather than
    # replacing it because every existing consumer matches on the name
    # alone, and only the call-sequence diagram needs the position.
    call_sites: list[tuple[str, int]] = field(default_factory=list)

    def lines_calling(self, callee: str) -> list[int]:
        """Every line in this symbol that calls `callee`, ascending."""
        return sorted(line for name, line in self.call_sites if name == callee)


@dataclass
class ParsedRepoIndex:
    """
    Explorer Agent's second output (alongside RepoManifest) — the
    parsed structure Explainer and Diagram agents actually work from.
    Kept separate from RepoManifest deliberately: RepoManifest is the
    human-readable "what is this repo" summary, this is the large,
    purely-internal data structure nobody looks at directly.
    Cached under the same repo_full_name + commit_sha as the manifest.
    """
    repo_full_name: str
    commit_sha: str
    symbols: list[ParsedSymbol] = field(default_factory=list)
    module_edges: list[tuple[str, str]] = field(default_factory=list)  # (from_module, to_module)
    # The import line each edge was resolved from: (from_module, to_module,
    # line). Kept beside module_edges rather than widening it, because every
    # consumer of the graph wants the pair and only the export wants the
    # line. An edge missing here is an edge no single line accounts for.
    edge_provenance: list[tuple[str, str, int]] = field(default_factory=list)
    # Which parser produced this. A cached index outlives the code that
    # built it: adding grammars for Go, Rust, Ruby, Java and PHP gave
    # those repositories symbols for the first time, but anything already
    # cached kept its empty one and looked unchanged. Bump the constant
    # in tree_sitter_parser when parsing changes and stale indexes are
    # re-parsed on next use instead of being served forever.
    parser_version: str = ""

    def symbols_in_file(self, file_path: str) -> list[ParsedSymbol]:
        return [s for s in self.symbols if s.file_path == file_path]

    def find_by_name(self, name: str) -> list[ParsedSymbol]:
        """May return multiple matches across files — disambiguation happens in trace_calls."""
        return [s for s in self.symbols if s.name == name]

    def provenance_for(self, from_module: str, to_module: str) -> list[int]:
        """
        Every line in from_module that imports to_module, ascending.

        A file can import the same module twice — two `from x import`
        lines, or a require beside an ES import — so this is a list, and
        an empty one means the edge has no line recorded rather than that
        the edge is unsupported.
        """
        return sorted(
            line for f, t, line in self.edge_provenance if f == from_module and t == to_module
        )

@dataclass
class ReadingStep:
    """One stop on the onboarding reading order."""
    file_path: str
    why_it_matters: str
    what_to_understand: str
    depends_on: list[str] = field(default_factory=list)   # modules this file imports
    depended_on_by: list[str] = field(default_factory=list)  # modules that import this file
    key_symbols: list[str] = field(default_factory=list)
    is_entry_point: bool = False


@dataclass
class OnboardingGuide:
    """
    The Onboarding Agent's output: where to start reading an unfamiliar
    repo and why. The ranking is deterministic (import-graph centrality
    + entry points); the LLM only turns those already-computed facts
    into prose, exactly like the Diagram Agent's optional labels.
    """
    repo_full_name: str
    commit_sha: str
    overview: str = ""
    entry_points: list[str] = field(default_factory=list)
    reading_order: list[ReadingStep] = field(default_factory=list)
    suggested_questions: list[str] = field(default_factory=list)
    graph_available: bool = True  # False when the repo has no internal import edges to rank on


@dataclass
class ImpactNode:
    """One node in a blast radius, with the distance it sits from the changed target."""
    path: str
    depth: int  # 1 = direct dependent, 2+ = indirect
    symbols: list[str] = field(default_factory=list)  # symbols in this file implicated at this depth
    via: list[str] = field(default_factory=list)  # paths this was reached through (one hop closer)

    @property
    def is_direct(self) -> bool:
        return self.depth == 1


@dataclass
class ImpactReport:
    """
    "What breaks if I change X?" — computed purely from the parsed
    import graph and call graph. No LLM decides what depends on what;
    an LLM may only summarize the finished numbers.
    """
    target_path: str
    target_symbol: Optional[str] = None
    direct: list[ImpactNode] = field(default_factory=list)
    indirect: list[ImpactNode] = field(default_factory=list)
    callers: list[ImpactNode] = field(default_factory=list)  # reverse call graph, symbol targets only
    entry_points_reached: list[str] = field(default_factory=list)
    max_depth: int = 0
    summary: str = ""
    truncated: bool = False  # traversal hit the node cap

    @property
    def affected_files(self) -> list[str]:
        seen: list[str] = []
        for node in self.direct + self.indirect:
            if node.path not in seen:
                seen.append(node.path)
        return seen


@dataclass
class CodeHealthFinding:
    """One maintainability observation, computed from the parsed index — never from an LLM."""
    check: str  # circular_import | hub_module | untested_module | long_function | large_file | unreferenced_symbol
    severity: str  # high | medium | low
    title: str
    detail: str
    files: list[str] = field(default_factory=list)
    symbol: Optional[str] = None
    line: Optional[int] = None
    metric: Optional[int] = None  # the number the finding is about: lines, dependents, cycle size


@dataclass
class CodeHealthReport:
    """
    Maintainability report card. The score is a heuristic for
    prioritising — deductions per finding, capped per check — not a
    judgement of the code's correctness.
    """
    repo_full_name: str
    commit_sha: str
    score: int
    grade: str
    source_file_count: int = 0
    test_file_count: int = 0
    symbol_count: int = 0
    findings: list[CodeHealthFinding] = field(default_factory=list)
    deductions: dict[str, int] = field(default_factory=dict)  # check -> points deducted

    @property
    def was_analysable(self) -> bool:
        """
        Whether there was any source to grade.

        A repository of documentation, or one whose only code sits in
        docs/ and scripts/ (which are deliberately not graded), produces
        no findings, and no findings scores 100. Reporting that as
        "Grade A, nothing needs attention" is false confidence about a
        repository nothing was ever checked in. Callers must say which
        of the two happened.
        """
        return self.source_file_count > 0


@dataclass
class Handbook:
    """
    Everything Repo Pilot worked out about a repository, gathered into one
    document a team can commit next to the code.

    Deliberately a composition rather than a new analysis: the reading
    order, the maintainability findings and the stack are already decided
    elsewhere, and re-deriving any of them here would let the handbook and
    the screens it came from disagree.

    health is optional because the guide is the point. A repository whose
    parsed index is missing still produces a useful handbook; one without a
    reading order does not.
    """
    repo_full_name: str
    commit_sha: str
    guide: OnboardingGuide
    detected_stack: list[str] = field(default_factory=list)
    file_count: int = 0
    skipped_file_count: int = 0  # >0 means the underlying ingest was incomplete
    health: Optional[CodeHealthReport] = None
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class AgentAsset:
    """
    One file in a repository that configures an AI coding agent: a Claude
    skill, a subagent, a slash command, project instructions, a Cursor
    rule, an MCP server declaration.

    name and description come from the file's own YAML frontmatter where
    it has one, so they are read rather than inferred. Both are written
    by whoever owns the repository and are therefore untrusted: these
    files exist specifically to steer an agent, which makes them the one
    kind of content in this app that is designed to be obeyed. Render
    them as text, never as instructions, and never hand them to a model
    without the same delimiting a README gets.
    """
    kind: str            # claude_skill | claude_subagent | claude_command | ...
    path: str
    name: str            # from frontmatter, else derived from the path
    description: str = ""
    body_excerpt: str = ""
    has_frontmatter: bool = False
    size_bytes: int = 0


@dataclass
class AgentToolingReport:
    """
    Which AI agent tooling a repository ships, and what each piece does.

    Entirely path matching and frontmatter parsing over the manifest the
    ingest already produced: no model, no extra GitHub call, identical
    every run.
    """
    repo_full_name: str
    commit_sha: str
    assets: list[AgentAsset] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.assets

    def by_kind(self) -> dict[str, list[AgentAsset]]:
        grouped: dict[str, list[AgentAsset]] = {}
        for asset in self.assets:
            grouped.setdefault(asset.kind, []).append(asset)
        return grouped


@dataclass(frozen=True)
class ExportedEdge:
    """
    One dependency in the exported architecture, with the lines it came from.

    `lines` holds every line in `from_path` that imports `to_path`,
    ascending. It is empty only when no single line accounts for the
    edge — never as a shorthand for "we did not check".
    """
    from_path: str
    to_path: str
    lines: list[int] = field(default_factory=list)

    @property
    def is_evidenced(self) -> bool:
        return bool(self.lines)


@dataclass
class ArchitectureCoverage:
    """
    What the export could see, and what it could not.

    An architecture picture is only worth trusting if it says where it
    stops. Every field here is counted from the ingest and the parse, so
    the exported document can state its own blind spots rather than
    presenting a partial graph as a complete one.
    """
    files_in_repo: int = 0
    files_skipped: int = 0
    # Extension -> number of files with that extension that no grammar
    # could parse, so none of their imports are in the graph.
    unparsed_extensions: dict[str, int] = field(default_factory=dict)
    # Languages this build would parse but whose grammar did not load here.
    degraded_languages: list[str] = field(default_factory=list)
    nodes_drawn: int = 0
    nodes_total: int = 0
    edges_drawn: int = 0
    edges_total: int = 0
    granularity: str = "file"

    @property
    def is_complete(self) -> bool:
        return (
            self.files_skipped == 0
            and not self.unparsed_extensions
            and not self.degraded_languages
            and self.nodes_drawn == self.nodes_total
            and self.edges_drawn == self.edges_total
        )

    @property
    def unparsed_file_count(self) -> int:
        return sum(self.unparsed_extensions.values())


@dataclass
class ArchitectureExport:
    """
    A standalone architecture document for one commit.

    Rendered to a single HTML file with no network dependency, so it can
    be committed, attached to a review, or opened offline years later and
    still say exactly which commit it describes.
    """
    repo_full_name: str
    commit_sha: str
    repo_url: str
    edges: list[ExportedEdge] = field(default_factory=list)
    detected_stack: list[str] = field(default_factory=list)
    coverage: ArchitectureCoverage = field(default_factory=ArchitectureCoverage)
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def nodes(self) -> list[str]:
        seen: dict[str, None] = {}
        for edge in self.edges:
            seen.setdefault(edge.from_path, None)
            seen.setdefault(edge.to_path, None)
        return list(seen)

    @property
    def evidenced_edge_count(self) -> int:
        return sum(1 for e in self.edges if e.is_evidenced)


@dataclass(frozen=True)
class MovedNode:
    """A file that changed path between two commits, matched by name."""
    from_path: str
    to_path: str


@dataclass(frozen=True)
class ChangedEdge:
    """
    A dependency that exists in one commit and not the other.

    `lines` are the import lines in whichever commit holds it, so a
    reviewer can open the change and read the statement that appeared or
    disappeared rather than taking the delta's word for it.
    """
    from_path: str
    to_path: str
    lines: list[int] = field(default_factory=list)


@dataclass
class ArchitectureDelta:
    """
    What changed structurally between two commits of one repository.

    Both sides are parses, not proposals, so a difference here means the
    code differs. Nothing in this object can move because a model
    described the same architecture two ways on two days.

    Renames are resolved before edges are compared: a file that only
    moved produces one MovedNode and no edge churn, instead of every
    dependency it touches appearing once as removed and once as added.
    """
    repo_full_name: str
    base: "ArchitectureExport"
    head: "ArchitectureExport"
    added_nodes: list[str] = field(default_factory=list)
    removed_nodes: list[str] = field(default_factory=list)
    moved_nodes: list[MovedNode] = field(default_factory=list)
    added_edges: list[ChangedEdge] = field(default_factory=list)
    removed_edges: list[ChangedEdge] = field(default_factory=list)
    # Files present in both commits whose own set of dependencies changed.
    # Derived from the edge lists, and the most useful single column in a
    # review: it names the files whose role in the repository moved.
    rerouted_nodes: list[str] = field(default_factory=list)
    unchanged_edge_count: int = 0
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def base_sha(self) -> str:
        return self.base.commit_sha

    @property
    def head_sha(self) -> str:
        return self.head.commit_sha

    @property
    def is_unchanged(self) -> bool:
        return not (
            self.added_nodes
            or self.removed_nodes
            or self.moved_nodes
            or self.added_edges
            or self.removed_edges
        )

    @property
    def change_count(self) -> int:
        return (
            len(self.added_nodes)
            + len(self.removed_nodes)
            + len(self.moved_nodes)
            + len(self.added_edges)
            + len(self.removed_edges)
        )


@dataclass(frozen=True)
class CachedCommit:
    """One commit of a repository that is already ingested and parsed."""
    commit_sha: str
    cached_at: datetime


@dataclass(frozen=True)
class CallStep:
    """
    One call in a traced sequence: who called whom, and from which line.

    `line` is inside `from_file`, at the call site — not at the callee's
    definition. That is the line a reader opens to check the arrow.
    """
    depth: int
    from_file: str
    from_symbol: str
    to_file: str
    to_symbol: str
    line: int
    # True when the callee is already on the current call stack. The step
    # is drawn and then not followed, because recursion is worth seeing
    # and not worth expanding forever.
    is_recursive: bool = False


@dataclass
class CallSequence:
    """
    What actually happens when one function runs, as far as the parse can
    tell.

    Every step comes from a call tree-sitter found inside a definition and
    resolved to a symbol in this repository. Calls that leave the
    repository — a framework, the standard library — are counted in
    `unresolved_calls` rather than drawn: this diagram is about what this
    codebase does, and a step nobody can open is not evidence.
    """
    repo_full_name: str
    commit_sha: str
    repo_url: str
    entry_file: str
    entry_symbol: str
    steps: list[CallStep] = field(default_factory=list)
    # Callee name -> how many times it was called and not resolved to a
    # symbol in this repository, most frequent first.
    unresolved_calls: dict[str, int] = field(default_factory=dict)
    # Names that matched several symbols with nothing to choose between
    # them. Guessing would draw a call that may not happen, so they are
    # reported instead.
    ambiguous_calls: dict[str, int] = field(default_factory=dict)
    truncated: bool = False
    max_depth: int = 0
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def participants(self) -> list[str]:
        """Files taking part, in the order the sequence first reaches them."""
        seen: dict[str, None] = {self.entry_file: None}
        for step in self.steps:
            seen.setdefault(step.from_file, None)
            seen.setdefault(step.to_file, None)
        return list(seen)

    @property
    def is_empty(self) -> bool:
        return not self.steps

    @property
    def unresolved_total(self) -> int:
        return sum(self.unresolved_calls.values())
