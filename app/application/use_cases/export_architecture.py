"""
ExportArchitectureUseCase — the import graph as a standalone document.

The point of the export is that it can be trusted away from the tool that
made it. Two properties do that work, and both are decided here rather
than at render time:

  1. Every edge carries the line it was resolved from. Not a sample, not
     the important ones — all of them, because an edge nobody can check
     is indistinguishable from an edge somebody made up. The lines come
     from the parse itself (ParsedRepoIndex.edge_provenance), so this is
     bookkeeping rather than a second analysis.

  2. The export states what it could not see. Files the ingest never
     fetched, extensions no grammar reads, languages whose grammar did
     not load, and nodes dropped to keep the picture legible are all
     counted and carried in ArchitectureCoverage.

The second one is the part a diagram generated *from* a model cannot
honestly do. Validating a proposed diagram against a repository can
reject a dependency that does not exist; it cannot notice one the
proposal left out, because there is nothing in the proposal to check.
Here the edges are the resolved imports, so completeness is a property of
the parse, and the gaps in the parse are knowable and printed.

No LLM is involved at any point.
"""
from __future__ import annotations

import logging

from app.application.interfaces.code_parser import CodeParser
from app.application.use_cases.ingest_repo import RepoCache
from app.domain.entities import (
    ArchitectureCoverage,
    ArchitectureExport,
    ExportedEdge,
    ParsedRepoIndex,
    RepoManifest,
)

logger = logging.getLogger(__name__)

# Extensions that are plainly not code, so the fact that no grammar reads
# them is not a blind spot worth reporting. Anything else with no import
# language is reported, including extensions nobody thought of — an
# unfamiliar extension is exactly the case worth surfacing.
_NOT_CODE_EXTENSIONS = frozenset(
    {
        ".md", ".markdown", ".rst", ".txt", ".adoc",
        ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".properties",
        ".lock", ".sum", ".mod",
        ".csv", ".tsv", ".xml", ".sql",
        ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".avif",
        ".woff", ".woff2", ".ttf", ".otf", ".eot",
        ".mp3", ".ogg", ".wav", ".flac", ".mp4", ".webm", ".mov",
        ".pdf", ".zip", ".gz", ".tar", ".whl", ".wasm",
        ".gitignore", ".gitkeep", ".gitattributes", ".editorconfig", ".env", ".example",
    }
)


class RepoNotExportableError(Exception):
    """Raised when an export is requested for a repo+commit that was never ingested."""


class ExportArchitectureUseCase:
    def __init__(
        self,
        *,
        cache: RepoCache,
        parser: CodeParser,
        max_nodes: int = 120,
        max_edges: int = 400,
    ) -> None:
        self._cache = cache
        self._parser = parser
        self._max_nodes = max_nodes
        self._max_edges = max_edges

    async def execute(self, *, repo_full_name: str, commit_sha: str) -> ArchitectureExport:
        manifest = await self._cache.get(repo_full_name, commit_sha)
        if manifest is None:
            raise RepoNotExportableError(
                f"{repo_full_name} has not been ingested at {commit_sha[:7]}. "
                "Ingest it first, then export the architecture."
            )
        parsed_index = await self._cache.get_parsed_index(repo_full_name, commit_sha)
        if parsed_index is None:
            raise RepoNotExportableError(
                f"No parsed code index for {repo_full_name}@{commit_sha[:7]}. "
                "The architecture export is built from the parse, not from prose."
            )

        edges_total = self._dedupe(parsed_index)
        edges = self._cap(edges_total)
        nodes_total = self._node_count(edges_total)

        return ArchitectureExport(
            repo_full_name=repo_full_name,
            commit_sha=commit_sha,
            repo_url=str(manifest.repo_url),
            edges=edges,
            detected_stack=list(manifest.detected_stack),
            coverage=ArchitectureCoverage(
                files_in_repo=manifest.file_count,
                files_skipped=manifest.skipped_file_count,
                unparsed_extensions=self._unparsed_extensions(manifest),
                degraded_languages=list(manifest.degraded_languages),
                nodes_drawn=self._node_count(edges),
                nodes_total=nodes_total,
                edges_drawn=len(edges),
                edges_total=len(edges_total),
                granularity="file",
            ),
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _dedupe(parsed_index: ParsedRepoIndex) -> list[ExportedEdge]:
        """
        One ExportedEdge per distinct dependency, carrying every line that
        produced it. A file importing the same module twice is one edge
        with two lines, not two edges — the second line is evidence, not
        a second dependency.
        """
        lines_by_pair: dict[tuple[str, str], set[int]] = {}
        for from_mod, to_mod, line in parsed_index.edge_provenance:
            if from_mod != to_mod:
                lines_by_pair.setdefault((from_mod, to_mod), set()).add(line)

        ordered: list[ExportedEdge] = []
        seen: set[tuple[str, str]] = set()
        for from_mod, to_mod in parsed_index.module_edges:
            if from_mod == to_mod or (from_mod, to_mod) in seen:
                continue
            seen.add((from_mod, to_mod))
            ordered.append(
                ExportedEdge(
                    from_path=from_mod,
                    to_path=to_mod,
                    lines=sorted(lines_by_pair.get((from_mod, to_mod), ())),
                )
            )
        return ordered

    @staticmethod
    def _node_count(edges: list[ExportedEdge]) -> int:
        return len({p for e in edges for p in (e.from_path, e.to_path)})

    def _cap(self, edges: list[ExportedEdge]) -> list[ExportedEdge]:
        """
        Keep the most-connected nodes when the graph is too large to draw.

        Dropping the least-connected nodes keeps the shape of the
        repository rather than an alphabetical slice of it. Whatever is
        dropped is counted in the coverage, so the document says a picture
        is partial instead of looking complete.
        """
        degree: dict[str, int] = {}
        for edge in edges:
            degree[edge.from_path] = degree.get(edge.from_path, 0) + 1
            degree[edge.to_path] = degree.get(edge.to_path, 0) + 1

        nodes = sorted(degree, key=lambda n: (-degree[n], n))
        if len(nodes) > self._max_nodes:
            kept = set(nodes[: self._max_nodes])
            edges = [e for e in edges if e.from_path in kept and e.to_path in kept]
            logger.info(
                "Architecture export capped at %d of %d nodes", self._max_nodes, len(nodes)
            )

        if len(edges) > self._max_edges:
            logger.info(
                "Architecture export capped at %d of %d edges", self._max_edges, len(edges)
            )
            edges = edges[: self._max_edges]
        return edges

    def _unparsed_extensions(self, manifest: RepoManifest) -> dict[str, int]:
        """
        Extensions present in the repository whose imports this parser
        cannot read, with a count each. These files may well depend on
        other files; nothing in the graph says so, and a reader deserves
        to know which part of the repository the picture is silent about.
        """
        counts: dict[str, int] = {}
        for node in manifest.files:
            if self._parser.reads_dependencies_from(node.path):
                continue
            name = node.path.rsplit("/", 1)[-1]
            ext = ("." + name.rsplit(".", 1)[-1]).lower() if "." in name else ""
            if not ext or ext in _NOT_CODE_EXTENSIONS:
                continue
            counts[ext] = counts.get(ext, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
