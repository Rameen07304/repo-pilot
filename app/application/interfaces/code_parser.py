"""
Contract for code structure analysis (tree-sitter implementation).

Two jobs, kept in one interface since both need the same parse trees:
  1. Chunking — split a file into semantically meaningful pieces for
     embedding (not naive line-count splitting).
  2. Call-graph tracing — follow imports/function calls a few hops
     from a starting point, so Explainer answers are grounded in
     actual code structure, not just "vector search vibes."

CRITICAL SECURITY NOTE for every implementation: tree-sitter only ever
PARSES syntax trees. No implementation of this Protocol may execute,
eval, import, or otherwise run any file content, under any
circumstance. Parsing is 100% static analysis.
"""
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.domain.entities import CodeChunk, ParsedRepoIndex, ParsedSymbol


@dataclass(frozen=True)
class TraceStep:
    """One hop in a call-graph trace."""
    file_path: str
    symbol_name: str  # function/class name found at this hop
    start_line: int
    end_line: int
    snippet: str


@dataclass(frozen=True)
class ModuleEdge:
    """
    One edge in the dependency graph, used by the Diagram Agent.

    `line` is the 1-based line in `from_module` holding the import this
    edge was resolved from, so a reader can check any edge against the
    source instead of taking the diagram on trust. It is None only for an
    edge no single line accounts for.
    """
    from_module: str
    to_module: str
    line: int | None = None


@runtime_checkable
class CodeParser(Protocol):
    @property
    def version(self) -> str:
        """
        Identifies this parser's output.

        A cached index outlives the code that built it. Adding grammars
        gave several languages symbols for the first time, but anything
        already cached kept its empty index and looked unchanged. Callers
        compare this against what a cached index was built with and
        re-parse when they differ.
        """
        ...

    @property
    def unavailable_languages(self) -> set[str]:
        """
        Languages this parser would support but whose grammar did not load
        here. Files in them still get generic chunking, so ingestion
        succeeds; callers surface the degradation rather than letting the
        answers quietly get worse.
        """
        ...

    def detect_language(self, file_path: str) -> str | None:
        """Extension/heuristic-based detection. No parsing needed for this."""
        ...

    def reads_dependencies_from(self, file_path: str) -> bool:
        """
        Whether build_module_graph reads any outgoing dependency from this
        file. Broader than detect_language: a language can contribute
        edges without a grammar, and an HTML file contributes its asset
        links without being a language at all.

        False means the graph contains nothing leaving this file, whatever
        the file actually depends on. That is a blind spot rather than a
        detail, so it is on the interface: the architecture export states
        it instead of presenting a graph that silently omits whole parts
        of a repository.
        """
        ...

    def chunk_file(self, *, file_path: str, content: str) -> list[CodeChunk]:
        """
        Split into function/class-level chunks where possible (falls
        back to fixed-size windows for unsupported languages or plain
        text files like README).
        """
        ...

    def trace_calls(
    self,
    *,
    entry_file: str,
    entry_symbol: str,
    parsed_index: "ParsedRepoIndex",
    max_hops: int = 3,
    ) -> list[TraceStep]:
        """
        Follow imports/calls a bounded number of hops from a starting
        symbol. `max_hops` is a hard cap — this must never run
        unbounded, both for latency and to avoid infinite loops on
        circular imports.
        """
        ...

    def build_module_graph(self, all_files: dict[str, str]) -> list[ModuleEdge]:
        """
        Build the whole-repo import graph. This is what the Diagram
        Agent draws from directly — deterministic, no LLM involved in
        producing the edges themselves.
        """
        ...

    def extract_symbols(
        self, *, file_path: str, content: str, language: str
    ) -> list["ParsedSymbol"]:
        """
        Extract all functions/classes/methods from one file's content.
        Used by IngestRepoUseCase to build the ParsedRepoIndex once
        during ingestion — a legitimate application-layer need, so
        this must be a public interface method, not a reach into a
        concrete implementation's internals.
        """
        ...
