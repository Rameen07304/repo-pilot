"""
CompareArchitectureUseCase — what changed structurally between two commits.

Archify can diff two architectures because its diagrams are typed JSON,
and a validator has checked each side against the repository before the
comparison runs. That catches a component the model invented. It cannot
catch one the model forgot, and it cannot tell a real change from the
model simply having described the same code differently on two days.

Both sides here are parses. A difference in this output means the code
differs — there is no third party whose consistency has to be trusted.

The one piece of real work is renames. A file that moves from
`app/db.py` to `app/core/db.py` has not changed the architecture at all,
but a naive set difference reports one removed node, one added node, and
every dependency it touches twice: once as removed, once as added. On a
refactor that is the entire diff, and the one genuinely new dependency
is lost in it. So moves are resolved first and the base graph is rewritten
through them before any edge is compared.
"""
from __future__ import annotations

import logging

from app.application.use_cases.export_architecture import (
    ExportArchitectureUseCase,
    RepoNotExportableError,
)
from app.domain.entities import (
    ArchitectureDelta,
    ArchitectureExport,
    ChangedEdge,
    MovedNode,
)

logger = logging.getLogger(__name__)


class SameCommitError(Exception):
    """Raised when both sides of a comparison are the same commit."""


class CompareArchitectureUseCase:
    def __init__(self, *, export_use_case: ExportArchitectureUseCase) -> None:
        self._export = export_use_case

    async def execute(
        self, *, repo_full_name: str, base_sha: str, head_sha: str
    ) -> ArchitectureDelta:
        if base_sha == head_sha:
            raise SameCommitError(
                "Both sides of the comparison are the same commit; there is nothing to diff."
            )

        # Propagates RepoNotExportableError, which names the side that is
        # missing — a comparison against a commit nobody ingested should
        # say so rather than silently reporting everything as new.
        base = await self._side(repo_full_name, base_sha, "base")
        head = await self._side(repo_full_name, head_sha, "head")

        return self._diff(repo_full_name=repo_full_name, base=base, head=head)

    async def _side(self, repo: str, sha: str, which: str) -> ArchitectureExport:
        try:
            return await self._export.execute(repo_full_name=repo, commit_sha=sha)
        except RepoNotExportableError as e:
            raise RepoNotExportableError(f"The {which} commit is not available: {e}") from e

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _diff(
        self, *, repo_full_name: str, base: ArchitectureExport, head: ArchitectureExport
    ) -> ArchitectureDelta:
        base_nodes, head_nodes = set(base.nodes), set(head.nodes)
        renames = self._detect_renames(base_nodes - head_nodes, head_nodes - base_nodes)

        def rename(path: str) -> str:
            return renames.get(path, path)

        base_edges = {(rename(e.from_path), rename(e.to_path)): e for e in base.edges}
        head_edges = {(e.from_path, e.to_path): e for e in head.edges}

        added = [head_edges[k] for k in head_edges.keys() - base_edges.keys()]
        removed = [base_edges[k] for k in base_edges.keys() - head_edges.keys()]

        moved_sources = set(renames)
        moved_targets = set(renames.values())

        return ArchitectureDelta(
            repo_full_name=repo_full_name,
            base=base,
            head=head,
            added_nodes=sorted(head_nodes - base_nodes - moved_targets),
            removed_nodes=sorted(base_nodes - head_nodes - moved_sources),
            moved_nodes=[
                MovedNode(from_path=src, to_path=dst) for src, dst in sorted(renames.items())
            ],
            added_edges=sorted(
                (ChangedEdge(e.from_path, e.to_path, list(e.lines)) for e in added),
                key=lambda e: (e.from_path, e.to_path),
            ),
            removed_edges=sorted(
                (ChangedEdge(e.from_path, e.to_path, list(e.lines)) for e in removed),
                key=lambda e: (e.from_path, e.to_path),
            ),
            # Only files that still exist. A deleted file's dependencies
            # obviously changed; saying so twice buries the files a
            # reviewer can actually go and read.
            rerouted_nodes=sorted(
                ({e.from_path for e in added} | {rename(e.from_path) for e in removed})
                & (head_nodes | moved_targets)
            ),
            unchanged_edge_count=len(base_edges.keys() & head_edges.keys()),
        )

    @staticmethod
    def _detect_renames(only_base: set[str], only_head: set[str]) -> dict[str, str]:
        """
        Pair a disappearing path with an appearing one when they share a
        file name, and only when that name is unambiguous on both sides.

        Two files called `utils.py` moving in the same commit would be a
        coin flip, so neither is paired: they are reported as one added
        and one removed, which is the truthful answer when the evidence
        does not identify a move. Guessing here would put a wrong arrow
        in a review.
        """
        def by_name(paths: set[str]) -> dict[str, list[str]]:
            grouped: dict[str, list[str]] = {}
            for path in paths:
                grouped.setdefault(path.rsplit("/", 1)[-1], []).append(path)
            return grouped

        gone, arrived = by_name(only_base), by_name(only_head)
        renames: dict[str, str] = {}
        for name, sources in gone.items():
            targets = arrived.get(name)
            if targets and len(sources) == 1 and len(targets) == 1:
                renames[sources[0]] = targets[0]
        return renames
