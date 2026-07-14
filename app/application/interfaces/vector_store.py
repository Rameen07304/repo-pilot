"""
Contract for the vector store (Chroma today, swappable later).
"""
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.domain.entities import CodeChunk


@dataclass(frozen=True)
class VectorQueryResult:
    chunk: CodeChunk
    similarity: float  # 0.0-1.0, higher = more similar


@runtime_checkable
class VectorStore(Protocol):
    async def upsert_chunks(
        self,
        *,
        repo_full_name: str,
        commit_sha: str,
        chunks: list[CodeChunk],
        embeddings: list[list[float]],
    ) -> None:
        """
        Store chunks + their embeddings, namespaced by repo + commit SHA.
        Namespacing by commit_sha is what makes our caching strategy work:
        re-ingesting the same commit is a no-op, a new commit gets its
        own isolated set of vectors instead of silently mixing with stale
        ones from a previous version of the repo.
        """
        ...

    async def query(
        self,
        *,
        repo_full_name: str,
        commit_sha: str,
        query_embedding: list[float],
        top_k: int = 8,
    ) -> list[VectorQueryResult]:
        """
        Retrieve the top_k most similar chunks, scoped to one repo+commit.
        Scoping matters: without it, a question about repo A could
        retrieve and leak code from repo B if both were ever indexed
        on the same shared store.
        """
        ...

    async def delete_repo(self, *, repo_full_name: str) -> None:
        """Remove all vectors for a repo (all commits). Used for cleanup/GC."""
        ...