"""
GetFileSourceUseCase — the source behind a clicked diagram node.

There is deliberately no new storage here. Ingestion already keeps
every fetched file's (secret-redacted) content on the cached
RepoManifest, and the ParsedRepoIndex already knows that file's
symbols and both directions of its import edges. This use case only
reads those two, so a clicked node shows exactly the same source the
Explainer answered from.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.application.use_cases.ingest_repo import RepoCache


class FileNotFoundInRepoError(Exception):
    """Raised when the requested path is not part of the ingested manifest."""


class RepoNotIngestedError(Exception):
    """Raised when source is requested for a repo+commit we have no cached data for."""


@dataclass
class FileSource:
    path: str
    content: str
    language: str | None = None
    size_bytes: int = 0
    line_count: int = 0
    truncated: bool = False
    symbols: list[dict] = field(default_factory=list)  # {name, kind, start_line, end_line}
    imports: list[str] = field(default_factory=list)  # modules this file imports
    imported_by: list[str] = field(default_factory=list)  # modules that import this file


class GetFileSourceUseCase:
    def __init__(self, *, cache: RepoCache, max_chars: int = 200_000) -> None:
        self._cache = cache
        self._max_chars = max_chars

    async def execute(self, *, repo_full_name: str, commit_sha: str, file_path: str) -> FileSource:
        manifest = await self._cache.get(repo_full_name, commit_sha)
        if manifest is None:
            raise RepoNotIngestedError(
                f"No ingested manifest for {repo_full_name}@{commit_sha}. Ingest the repo first."
            )

        node = next((f for f in manifest.files if f.path == file_path), None)
        if node is None:
            raise FileNotFoundInRepoError(
                f"{file_path} is not part of the ingested file list for this commit."
            )

        content = node.content or ""
        truncated = len(content) > self._max_chars
        if truncated:
            content = content[: self._max_chars]

        parsed_index = await self._cache.get_parsed_index(repo_full_name, commit_sha)
        symbols: list[dict] = []
        imports: list[str] = []
        imported_by: list[str] = []
        if parsed_index is not None:
            symbols = [
                {
                    "name": s.name,
                    "kind": s.kind,
                    "start_line": s.start_line,
                    "end_line": s.end_line,
                }
                for s in sorted(parsed_index.symbols_in_file(file_path), key=lambda s: s.start_line)
            ]
            imports = sorted(
                {to_mod for from_mod, to_mod in parsed_index.module_edges
                 if from_mod == file_path and to_mod != file_path}
            )
            imported_by = sorted(
                {from_mod for from_mod, to_mod in parsed_index.module_edges
                 if to_mod == file_path and from_mod != file_path}
            )

        return FileSource(
            path=file_path,
            content=content,
            language=node.language,
            size_bytes=node.size_bytes,
            line_count=content.count("\n") + 1 if content else 0,
            truncated=truncated,
            symbols=symbols,
            imports=imports,
            imported_by=imported_by,
        )
