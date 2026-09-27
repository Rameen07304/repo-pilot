"""
TraceCallSequenceUseCase — what runs when one function runs.

The second diagram type, and the only one of Archify's four that can be
built the way the first one was. A sequence diagram needs participants
and ordered messages between them; tree-sitter already records every call
inside every definition, and this build now records the line each call is
written on, so both come out of the parse rather than out of a model
reading the code and deciding what the steps are.

Archify's sequence diagrams are proposed by an agent and then validated.
That catches a step which contradicts the repository. It cannot catch a
step the agent left out, and it cannot promise the order is the order in
the source. Here the steps ARE the calls, in the order they appear in the
file, so both are properties of the parse.

Three things this refuses to do, because each would put an arrow on the
diagram that might not be real:

  * Guess between same-named symbols. `save` may exist in four files. The
    caller's own file wins, then a file the caller imports; past that the
    call is reported as ambiguous rather than pointed somewhere.
  * Follow calls out of the repository. A call to a framework is counted
    and named, never drawn, because nobody can open it to check.
  * Expand recursion. A symbol already on the call stack is drawn once,
    marked, and not followed.
"""
from __future__ import annotations

import logging

from app.application.use_cases.ingest_repo import RepoCache
from app.domain.entities import CallSequence, CallStep, ParsedRepoIndex, ParsedSymbol

logger = logging.getLogger(__name__)

# Only these can be an entry point. A class is not something that runs:
# it has no single path through it, so a flowchart of one is empty and a
# sequence of one is every method's calls piled together. Worse, a class
# node contains its methods, so its call count is their sum — which put
# classes at the top of every ranking and left the methods people
# actually wanted unreachable.
_CALLABLE_KINDS = frozenset({"function", "method"})


class SequenceNotTraceableError(Exception):
    """Raised when the repo is not parsed, or the entry symbol is not in it."""


class TraceCallSequenceUseCase:
    def __init__(
        self,
        *,
        cache: RepoCache,
        max_depth: int = 6,
        max_steps: int = 120,
    ) -> None:
        self._cache = cache
        self._max_depth = max_depth
        self._max_steps = max_steps

    async def execute(
        self,
        *,
        repo_full_name: str,
        commit_sha: str,
        entry_file: str,
        entry_symbol: str,
        max_depth: int | None = None,
    ) -> CallSequence:
        index = await self._cache.get_parsed_index(repo_full_name, commit_sha)
        if index is None:
            raise SequenceNotTraceableError(
                f"No parsed code index for {repo_full_name}@{commit_sha[:7]}. "
                "Ingest the repo first."
            )
        manifest = await self._cache.get(repo_full_name, commit_sha)
        repo_url = str(manifest.repo_url) if manifest else f"https://github.com/{repo_full_name}"

        entry = self._find_entry(index, entry_file, entry_symbol)
        depth_cap = self._max_depth if max_depth is None else max_depth

        sequence = CallSequence(
            repo_full_name=repo_full_name,
            commit_sha=commit_sha,
            repo_url=repo_url,
            entry_file=entry.file_path,
            entry_symbol=entry.name,
            max_depth=depth_cap,
        )
        self._walk(index=index, entry=entry, depth_cap=depth_cap, sequence=sequence)
        sequence.unresolved_calls = dict(
            sorted(sequence.unresolved_calls.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        sequence.ambiguous_calls = dict(
            sorted(sequence.ambiguous_calls.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        return sequence

    async def suggest_entries(
        self,
        *,
        repo_full_name: str,
        commit_sha: str,
        limit: int = 40,
        within_files: set[str] | None = None,
    ) -> list[dict]:
        """
        Functions worth tracing, best first.

        Functions and methods only, and only those that call something
        inside the same repository — most symbols do neither, so offering
        all of them would mostly offer empty diagrams. Ranked by how many calls
        actually resolve inside the repo, which is the same thing as how
        much of a sequence the trace will have to show.

        `within_files` narrows the search to files something else already
        found relevant. That is what lets a question choose its own
        starting point instead of a reader guessing one.
        """
        index = await self._cache.get_parsed_index(repo_full_name, commit_sha)
        if index is None:
            raise SequenceNotTraceableError(
                f"No parsed code index for {repo_full_name}@{commit_sha[:7]}. "
                "Ingest the repo first."
            )

        imports = self._import_map(index)
        by_name: dict[str, list[ParsedSymbol]] = {}
        for symbol in index.symbols:
            by_name.setdefault(symbol.name, []).append(symbol)

        ranked: list[tuple[int, int, str, str]] = []
        for symbol in index.symbols:
            if symbol.kind not in _CALLABLE_KINDS:
                continue
            # A name the parser could not read is not something a reader
            # can choose from a list.
            if symbol.name.startswith("<"):
                continue
            if within_files is not None and symbol.file_path not in within_files:
                continue
            internal = sum(
                1
                for callee, _line in symbol.call_sites
                if self._resolve(callee, symbol, by_name, imports)[0] is not None
            )
            # Worth offering if it does either thing: calls into the repo,
            # or branches. A function that only branches still makes a
            # flowchart, and one that only calls still makes a sequence.
            if internal or symbol.branch_count:
                ranked.append((internal, symbol.branch_count, symbol.file_path, symbol.name))

        # The client re-sorts this list for whichever diagram is selected,
        # so truncating by one criterion would hide the other's best
        # candidates entirely — ranking by calls alone cut every branchy
        # function, and re-sorting forty branchless build() methods got
        # nowhere. Taking from both orders alternately means the top of
        # each survives the cap.
        by_calls = sorted(ranked, key=lambda r: (-r[0], -r[1], r[2], r[3]))
        by_branches = sorted(ranked, key=lambda r: (-r[1], -r[0], r[2], r[3]))

        chosen: list[tuple[int, int, str, str]] = []
        seen: set[tuple[str, str]] = set()
        for pair in zip(by_calls, by_branches, strict=False):
            for entry in pair:
                key = (entry[2], entry[3])
                if key not in seen and len(chosen) < limit:
                    seen.add(key)
                    chosen.append(entry)
            if len(chosen) >= limit:
                break

        return [
            {
                "file_path": path,
                "symbol": name,
                "internal_calls": calls,
                "branches": branches,
            }
            for calls, branches, path, name in chosen
        ]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _find_entry(
        index: ParsedRepoIndex, entry_file: str, entry_symbol: str
    ) -> ParsedSymbol:
        matches = [
            s
            for s in index.symbols_in_file(entry_file)
            if s.name == entry_symbol
        ]
        if not matches:
            raise SequenceNotTraceableError(
                f"{entry_symbol} was not found in {entry_file} at this commit."
            )
        # A file can define the same name twice (a method on two classes).
        # The first by position is the one a reader means.
        return sorted(matches, key=lambda s: s.start_line)[0]

    def _walk(
        self,
        *,
        index: ParsedRepoIndex,
        entry: ParsedSymbol,
        depth_cap: int,
        sequence: CallSequence,
    ) -> None:
        """
        Depth-first in source order, so the sequence reads the way the
        code does: a call written above another is traced first.
        """
        imports = self._import_map(index)
        by_name: dict[str, list[ParsedSymbol]] = {}
        for symbol in index.symbols:
            by_name.setdefault(symbol.name, []).append(symbol)

        def visit(symbol: ParsedSymbol, depth: int, stack: set[tuple[str, str]]) -> None:
            if depth >= depth_cap:
                if symbol.call_sites:
                    sequence.truncated = True
                return
            for callee_name, line in sorted(symbol.call_sites, key=lambda cs: (cs[1], cs[0])):
                if len(sequence.steps) >= self._max_steps:
                    sequence.truncated = True
                    return
                resolved, why = self._resolve(
                    callee_name, symbol, by_name, imports
                )
                if resolved is None:
                    bucket = (
                        sequence.ambiguous_calls
                        if why == "ambiguous"
                        else sequence.unresolved_calls
                    )
                    bucket[callee_name] = bucket.get(callee_name, 0) + 1
                    continue

                key = (resolved.file_path, resolved.name)
                recursive = key in stack
                sequence.steps.append(
                    CallStep(
                        depth=depth,
                        from_file=symbol.file_path,
                        from_symbol=symbol.name,
                        to_file=resolved.file_path,
                        to_symbol=resolved.name,
                        line=line,
                        is_recursive=recursive,
                    )
                )
                if not recursive:
                    visit(resolved, depth + 1, stack | {key})

        visit(entry, 0, {(entry.file_path, entry.name)})

    @staticmethod
    def _import_map(index: ParsedRepoIndex) -> dict[str, set[str]]:
        """file -> the files it imports, for disambiguating a call by name."""
        imports: dict[str, set[str]] = {}
        for from_mod, to_mod in index.module_edges:
            imports.setdefault(from_mod, set()).add(to_mod)
        return imports

    @staticmethod
    def _resolve(
        name: str,
        caller: ParsedSymbol,
        by_name: dict[str, list[ParsedSymbol]],
        imports: dict[str, set[str]],
    ) -> tuple[ParsedSymbol | None, str]:
        """
        Returns (symbol, reason). A None symbol means the call is not
        drawn, and the reason says whether it left the repository or was
        too ambiguous to place.
        """
        candidates = by_name.get(name)
        if not candidates:
            return None, "external"  # a framework, the standard library, a builtin
        if len(candidates) == 1:
            return candidates[0], "unique"

        # Defined in the caller's own file: the closest thing to a
        # language's own scoping rule that a name-based match can apply.
        same_file = [c for c in candidates if c.file_path == caller.file_path]
        if len(same_file) == 1:
            return same_file[0], "same file"

        # Otherwise, a file the caller actually imports. If exactly one
        # candidate sits in one of those, the import is the evidence.
        reachable = imports.get(caller.file_path, set())
        imported = [c for c in candidates if c.file_path in reachable]
        if len(imported) == 1:
            return imported[0], "imported"

        return None, "ambiguous"
