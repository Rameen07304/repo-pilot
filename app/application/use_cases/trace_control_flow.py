"""
TraceControlFlowUseCase — one function as a flowchart.

The third diagram type, and the one most people mean by "diagram the
flow": boxes, diamonds, a Yes and a No. Same rule as the other two —
the shapes come from the syntax tree, so a decision on the chart is an
`if` in the file and nothing else.

It reuses the call sequence's entry points rather than inventing its own
picker: the question "which function" has one answer, and asking it twice
in two places would be two lists to keep in step.
"""
from __future__ import annotations

import logging

from app.application.use_cases.ingest_repo import RepoCache
from app.application.use_cases.trace_call_sequence import SequenceNotTraceableError
from app.domain.entities import ControlFlowChart
from app.infrastructure.parsing.control_flow import flow_for_symbol

logger = logging.getLogger(__name__)


class TraceControlFlowUseCase:
    def __init__(self, *, cache: RepoCache) -> None:
        self._cache = cache

    async def execute(
        self, *, repo_full_name: str, commit_sha: str, entry_file: str, entry_symbol: str
    ) -> ControlFlowChart:
        index = await self._cache.get_parsed_index(repo_full_name, commit_sha)
        manifest = await self._cache.get(repo_full_name, commit_sha)
        if index is None or manifest is None:
            raise SequenceNotTraceableError(
                f"{repo_full_name} has not been ingested at {commit_sha[:7]}. Ingest it first."
            )

        matches = [
            s for s in index.symbols_in_file(entry_file) if s.name == entry_symbol
        ]
        if not matches:
            raise SequenceNotTraceableError(
                f"{entry_symbol} was not found in {entry_file} at this commit."
            )
        symbol = sorted(matches, key=lambda s: s.start_line)[0]

        node = next((f for f in manifest.files if f.path == entry_file), None)
        if node is None or not node.content:
            raise SequenceNotTraceableError(
                f"{entry_file} was not fetched at this commit, so it cannot be charted."
            )

        language = self._language_of(entry_file)
        result = (
            flow_for_symbol(
                content=node.content,
                language=language,
                symbol=entry_symbol,
                start_line=symbol.start_line,
            )
            if language
            else None
        )
        if result is None:
            # Not an error: some languages have no grammar here, and some
            # definitions have no body to chart. The page says which.
            return ControlFlowChart(
                repo_full_name=repo_full_name,
                commit_sha=commit_sha,
                repo_url=str(manifest.repo_url),
                file_path=entry_file,
                symbol=entry_symbol,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
                items=[],
                charted=False,
            )

        items, truncated = result
        return ControlFlowChart(
            repo_full_name=repo_full_name,
            commit_sha=commit_sha,
            repo_url=str(manifest.repo_url),
            file_path=entry_file,
            symbol=entry_symbol,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            items=items,
            truncated=truncated,
            charted=True,
        )

    @staticmethod
    def _language_of(path: str) -> str | None:
        # Only the languages whose control-flow node types were checked
        # against a real parse tree.
        table = {
            ".py": "python", ".js": "javascript", ".jsx": "javascript",
            ".mjs": "javascript", ".cjs": "javascript",
            ".ts": "typescript", ".tsx": "typescript",
            ".dart": "dart", ".go": "go", ".java": "java",
            ".rb": "ruby", ".rs": "rust", ".php": "php",
        }
        lower = path.lower()
        for ext, lang in table.items():
            if lower.endswith(ext):
                return lang
        return None
