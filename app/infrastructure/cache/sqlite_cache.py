"""
Concrete RepoCache implementation using Python's built-in sqlite3.

Design notes:
  - Both RepoManifest and ParsedRepoIndex are serialized to JSON and
    stored as single blob columns, keyed by (repo_full_name, commit_sha).
    This is deliberately not a fully normalized schema — we only ever
    fetch "everything for this repo+SHA" as one unit, never query
    individual files/symbols with SQL, so a JSON blob per entry is the
    right amount of complexity for a cache, not an under- or over-build.
  - A NEW commit_sha is a completely separate row, never an overwrite
    of a previous one — this is what makes "re-ingest same commit is
    free, new commit gets fresh data" actually true, not just a comment.
  - sqlite3 is sync under the hood; every method wraps its work in
    asyncio.to_thread(...) to keep this honestly async, matching the
    same pattern used for GeminiClient and ChromaVectorStore.
  - A fresh connection is opened per call rather than held open across
    awaits — sqlite3 connections aren't safe to share across threads,
    and asyncio.to_thread may run on a different thread each time.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from app.application.use_cases.ingest_repo import RepoCache
from app.domain.entities import FileNode, ParsedRepoIndex, ParsedSymbol, RepoManifest
from app.domain.value_objects import RepoUrl

logger = logging.getLogger(__name__)

# How long a writer waits for another writer's lock before giving up.
# Ingest writes a manifest and a parsed index while questions read the
# same database, so without this a concurrent pair raises "database is
# locked" immediately instead of waiting the moment out.
_BUSY_TIMEOUT_SECONDS = 10.0


def _connect(db_path: str) -> sqlite3.Connection:
    """
    Open a connection configured for concurrent use.

    WAL lets readers continue while a writer holds the lock, which is
    exactly this app's shape: one ingest writing while several questions
    read. busy_timeout turns the remaining writer-on-writer collision
    from an immediate error into a short wait.
    """
    conn = sqlite3.connect(db_path, timeout=_BUSY_TIMEOUT_SECONDS)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={int(_BUSY_TIMEOUT_SECONDS * 1000)}")
    return conn

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS repo_manifests (
    repo_full_name TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    cached_at TEXT NOT NULL,
    PRIMARY KEY (repo_full_name, commit_sha)
);

CREATE TABLE IF NOT EXISTS parsed_repo_indexes (
    repo_full_name TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    index_json TEXT NOT NULL,
    cached_at TEXT NOT NULL,
    PRIMARY KEY (repo_full_name, commit_sha)
);
"""


class SqliteRepoCache(RepoCache):
    def __init__(self, *, db_path: str, max_repos: int = 0) -> None:
        """
        max_repos caps how many repositories are kept, evicting the least
        recently cached first. 0 disables the cap.

        Anyone can make a public deployment ingest an arbitrary public
        repository, so without a cap the store grows until the disk is
        full and every repo becomes unusable at once. Evicting the oldest
        costs one re-ingest for that repo and nothing for the rest.
        """
        self._db_path = db_path
        self._max_repos = max_repos
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_tables_sync()

    def _evict_oldest_sync(self, conn) -> None:
        if self._max_repos <= 0:
            return
        # Both tables are keyed the same way and written together, so the
        # manifest table is the one that decides what is still wanted.
        rows = conn.execute(
            """
            SELECT repo_full_name, commit_sha FROM repo_manifests
            ORDER BY cached_at DESC LIMIT -1 OFFSET ?
            """,
            (self._max_repos,),
        ).fetchall()
        for repo_full_name, commit_sha in rows:
            conn.execute(
                "DELETE FROM repo_manifests WHERE repo_full_name = ? AND commit_sha = ?",
                (repo_full_name, commit_sha),
            )
            conn.execute(
                "DELETE FROM parsed_repo_indexes WHERE repo_full_name = ? AND commit_sha = ?",
                (repo_full_name, commit_sha),
            )
        if rows:
            logger.info("Cache full: evicted %d least recently cached repo(s).", len(rows))

    def _init_tables_sync(self) -> None:
        conn = _connect(self._db_path)
        try:
            conn.executescript(_CREATE_TABLES_SQL)
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # RepoManifest
    # ------------------------------------------------------------------

    async def get(self, repo_full_name: str, commit_sha: str) -> RepoManifest | None:
        return await asyncio.to_thread(self._get_manifest_sync, repo_full_name, commit_sha)

    def _get_manifest_sync(self, repo_full_name: str, commit_sha: str) -> RepoManifest | None:
        conn = _connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT manifest_json FROM repo_manifests WHERE repo_full_name = ? AND commit_sha = ?",
                (repo_full_name, commit_sha),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return None
        return self._manifest_from_json(row[0])

    async def set(self, manifest: RepoManifest) -> None:
        await asyncio.to_thread(self._set_manifest_sync, manifest)

    def _set_manifest_sync(self, manifest: RepoManifest) -> None:
        payload = self._manifest_to_json(manifest)
        conn = _connect(self._db_path)
        try:
            conn.execute(
                """
                INSERT INTO repo_manifests (repo_full_name, commit_sha, manifest_json, cached_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (repo_full_name, commit_sha) DO UPDATE SET
                    manifest_json = excluded.manifest_json,
                    cached_at = excluded.cached_at
                """,
                (
                    manifest.repo_url.full_name,
                    manifest.commit_sha,
                    payload,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self._evict_oldest_sync(conn)
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # ParsedRepoIndex
    # ------------------------------------------------------------------

    async def get_parsed_index(self, repo_full_name: str, commit_sha: str) -> ParsedRepoIndex | None:
        return await asyncio.to_thread(self._get_index_sync, repo_full_name, commit_sha)

    def _get_index_sync(self, repo_full_name: str, commit_sha: str) -> ParsedRepoIndex | None:
        conn = _connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT index_json FROM parsed_repo_indexes WHERE repo_full_name = ? AND commit_sha = ?",
                (repo_full_name, commit_sha),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return None
        return self._index_from_json(row[0])

    async def set_parsed_index(self, parsed_index: ParsedRepoIndex) -> None:
        await asyncio.to_thread(self._set_index_sync, parsed_index)

    def _set_index_sync(self, parsed_index: ParsedRepoIndex) -> None:
        payload = self._index_to_json(parsed_index)
        conn = _connect(self._db_path)
        try:
            conn.execute(
                """
                INSERT INTO parsed_repo_indexes (repo_full_name, commit_sha, index_json, cached_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (repo_full_name, commit_sha) DO UPDATE SET
                    index_json = excluded.index_json,
                    cached_at = excluded.cached_at
                """,
                (
                    parsed_index.repo_full_name,
                    parsed_index.commit_sha,
                    payload,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _manifest_to_json(manifest: RepoManifest) -> str:
        data = {
            "repo_url": manifest.repo_url.normalized,
            "commit_sha": manifest.commit_sha,
            "files": [asdict(f) for f in manifest.files],
            "detected_stack": manifest.detected_stack,
            "entry_points": manifest.entry_points,
            "readme_content": manifest.readme_content,
            "summary": manifest.summary,
            "ingested_at": manifest.ingested_at.isoformat(),
        }
        return json.dumps(data)

    @staticmethod
    def _manifest_from_json(raw: str) -> RepoManifest:
        data = json.loads(raw)
        return RepoManifest(
            repo_url=RepoUrl(data["repo_url"]),
            commit_sha=data["commit_sha"],
            files=[FileNode(**f) for f in data["files"]],
            detected_stack=data["detected_stack"],
            entry_points=data["entry_points"],
            readme_content=data["readme_content"],
            summary=data["summary"],
            ingested_at=datetime.fromisoformat(data["ingested_at"]),
        )

    @staticmethod
    def _index_to_json(index: ParsedRepoIndex) -> str:
        data = {
            "repo_full_name": index.repo_full_name,
            "commit_sha": index.commit_sha,
            "symbols": [asdict(s) for s in index.symbols],
            "module_edges": [list(edge) for edge in index.module_edges],
            "edge_provenance": [list(edge) for edge in index.edge_provenance],
            # Without this the staleness check on load always saw "" and
            # discarded every cached index, so nothing was ever re-used.
            "parser_version": index.parser_version,
        }
        return json.dumps(data)

    @staticmethod
    def _index_from_json(raw: str) -> ParsedRepoIndex:
        data = json.loads(raw)
        return ParsedRepoIndex(
            repo_full_name=data["repo_full_name"],
            commit_sha=data["commit_sha"],
            symbols=[ParsedSymbol(**s) for s in data["symbols"]],
            module_edges=[tuple(edge) for edge in data["module_edges"]],
            edge_provenance=[tuple(edge) for edge in data.get("edge_provenance", [])],
            parser_version=data.get("parser_version", ""),
        )
