"""
Concrete VectorStore implementation using Chroma's PersistentClient.

Security guarantees this class enforces (not just convention):
  1. Every stored ID is namespaced as f"{repo_full_name}::{commit_sha}::{chunk.id}"
     — prevents one repo's chunks from silently overwriting another's if
     raw chunk IDs ever collide across repos.
  2. query() and upsert_chunks() require BOTH repo_full_name and commit_sha
     as mandatory keyword args (enforced by the interface's signature,
     not just this implementation) — there is no "search everything" path.
  3. Metadata filtering uses exact-equality $and on both fields — never a
     partial/contains match.
  4. repo_full_name only ever originates from an already-validated RepoUrl
     (see domain/value_objects.py) — never raw, unvalidated user input.

Chroma's Python client is synchronous under the hood, so every call here
is wrapped in asyncio.to_thread(...) to keep our VectorStore interface
honestly async without blocking the event loop.
"""
from __future__ import annotations

import asyncio
import logging

import chromadb
from chromadb.config import Settings

from app.application.interfaces.vector_store import VectorQueryResult, VectorStore
from app.domain.entities import CodeChunk

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "code_chunks"  # single shared collection, scoped via metadata


class ChromaVectorStore(VectorStore):
    def __init__(self, *, persist_dir: str) -> None:
        self._client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    @staticmethod
    def _namespaced_id(*, repo_full_name: str, commit_sha: str, chunk_id: str) -> str:
        return f"{repo_full_name}::{commit_sha}::{chunk_id}"

    async def upsert_chunks(
        self,
        *,
        repo_full_name: str,
        commit_sha: str,
        chunks: list[CodeChunk],
        embeddings: list[list[float]],
    ) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"chunks ({len(chunks)}) and embeddings ({len(embeddings)}) "
                "must be the same length"
            )
        if not chunks:
            return

        ids = [
            self._namespaced_id(
                repo_full_name=repo_full_name, commit_sha=commit_sha, chunk_id=c.id
            )
            for c in chunks
        ]
        documents = [c.content for c in chunks]
        metadatas = [
            {
                "repo_full_name": repo_full_name,
                "commit_sha": commit_sha,
                "file_path": c.file_path,
                "start_line": c.start_line,
                "end_line": c.end_line,
            }
            for c in chunks
        ]

        await asyncio.to_thread(
            self._collection.upsert,
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )
        logger.info(
            "Upserted %d chunks for %s@%s", len(chunks), repo_full_name, commit_sha
        )

    async def query(
        self,
        *,
        repo_full_name: str,
        commit_sha: str,
        query_embedding: list[float],
        top_k: int = 8,
    ) -> list[VectorQueryResult]:
        # Exact-equality $and on both fields — the actual security
        # boundary. No path exists here that skips this filter.
        where_filter = {
            "$and": [
                {"repo_full_name": {"$eq": repo_full_name}},
                {"commit_sha": {"$eq": commit_sha}},
            ]
        }

        result = await asyncio.to_thread(
            self._collection.query,
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where_filter,
        )

        return self._to_query_results(result)

    @staticmethod
    def _to_query_results(raw_result) -> list[VectorQueryResult]:
        ids = raw_result.get("ids", [[]])[0]
        documents = raw_result.get("documents", [[]])[0]
        metadatas = raw_result.get("metadatas", [[]])[0]
        distances = raw_result.get("distances", [[]])[0]

        out: list[VectorQueryResult] = []
        # strict: these are parallel arrays from a single response, so a
        # length mismatch is a Chroma bug. Silently zipping to the
        # shortest would drop results while leaving every remaining
        # citation looking correct.
        for id_, doc, meta, distance in zip(ids, documents, metadatas, distances, strict=True):
            # Chroma returns cosine DISTANCE (lower = more similar).
            # We convert to a 0-1 similarity score to match our
            # VectorQueryResult contract, where higher = more similar.
            similarity = max(0.0, 1.0 - distance)

            # id_ here is the namespaced id (repo::commit::chunk_id) —
            # we reconstruct the original CodeChunk id for the caller,
            # since callers shouldn't need to know about our internal
            # namespacing scheme.
            original_chunk_id = id_.split("::", 2)[-1]

            chunk = CodeChunk(
                id=original_chunk_id,
                file_path=meta["file_path"],
                content=doc,
                start_line=meta["start_line"],
                end_line=meta["end_line"],
            )
            out.append(VectorQueryResult(chunk=chunk, similarity=similarity))

        return out

    async def delete_repo(self, *, repo_full_name: str) -> None:
        # Deletes ALL commits for this repo. Exact match on
        # repo_full_name only — still can't accidentally touch another
        # repo since it's a full equality check, not a prefix/contains.
        await asyncio.to_thread(
            self._collection.delete,
            where={"repo_full_name": {"$eq": repo_full_name}},
        )
        logger.info("Deleted all vectors for %s", repo_full_name)
