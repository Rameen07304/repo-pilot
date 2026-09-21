"""
AnalyzeCodeHealthUseCase — a maintainability report card for the repo.

Every finding is computed from the cached ParsedRepoIndex and
RepoManifest; no LLM is involved anywhere in this use case, so the
report is deterministic and free. Six checks:

  circular imports   cycles in the module graph (Tarjan SCC)
  hub modules        files with high fan-in — the ones a change is
                     most likely to break
  untested modules   source files no test file imports, ranked by
                     how many modules depend on them
  long functions     symbols over a line threshold
  large files        files over a line threshold
  unreferenced       symbols never named by any other symbol's calls
                     (name-based, so only reported when the name is
                     unique in the repo — a shared name like `get`
                     would otherwise produce nonsense)

The score is a heuristic: it starts at 100 and each finding deducts
by severity, with per-check caps so one category cannot zero it. It
is a signal for prioritising, not a verdict.
"""
from __future__ import annotations

import logging
import re
from collections import Counter

from app.application.use_cases.ingest_repo import RepoCache
from app.domain.entities import CodeHealthFinding, CodeHealthReport, ParsedRepoIndex

logger = logging.getLogger(__name__)

_SOURCE_SUFFIXES = (
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte",
    ".dart", ".go", ".rs", ".rb", ".java", ".php",
)
_TEST_DIR_SEGMENTS = {"test", "tests", "__tests__", "spec", "specs", "e2e"}
_TEST_FILE_MARKERS = ("test_", "_test.", ".test.", ".spec.", "_spec.", "spec_")
_GENERATED_MARKERS = (".g.dart", ".freezed.dart", ".min.js", "migrations/")
_SUPPORTING_DIRS = {"examples", "example", "docs", "doc", "benchmarks", "samples", "demo", "demos", "scripts"}
_FRAMEWORK_HOOK_NAMES = {
    # Called by a framework or runtime, never by repo code — not dead.
    "main", "setup", "teardown", "handle", "ready", "run", "init", "__init__",
    "get", "post", "put", "patch", "delete", "dispatch", "render", "save", "clean",
    "setUp", "tearDown", "configure", "register", "apply", "build", "create",
}

_SEVERITY_WEIGHT = {"high": 8, "medium": 3, "low": 1}
_CHECK_CAP = {
    "circular_import": 24,
    "hub_module": 12,
    "untested_module": 20,
    "long_function": 16,
    "large_file": 12,
    "unreferenced_symbol": 8,
}


class RepoNotIndexedError(Exception):
    """Raised when a report is requested for a repo+commit with no cached parsed index."""


class AnalyzeCodeHealthUseCase:
    def __init__(
        self,
        *,
        cache: RepoCache,
        long_function_lines: int = 60,
        very_long_function_lines: int = 120,
        large_file_lines: int = 500,
        very_large_file_lines: int = 1000,
        hub_fan_in: int = 6,
        max_findings_per_check: int = 15,
    ) -> None:
        self._cache = cache
        self._long_function = long_function_lines
        self._very_long_function = very_long_function_lines
        self._large_file = large_file_lines
        self._very_large_file = very_large_file_lines
        self._hub_fan_in = hub_fan_in
        self._max_per_check = max_findings_per_check

    async def execute(self, *, repo_full_name: str, commit_sha: str) -> CodeHealthReport:
        manifest = await self._cache.get(repo_full_name, commit_sha)
        parsed_index = await self._cache.get_parsed_index(repo_full_name, commit_sha)
        if manifest is None or parsed_index is None:
            raise RepoNotIndexedError(
                f"No parsed code index for {repo_full_name}@{commit_sha}. Ingest the repo first."
            )

        source_files = [
            f.path for f in manifest.files
            if f.path.lower().endswith(_SOURCE_SUFFIXES)
            and not self._is_generated(f.path)
            and not self._is_supporting(f.path)
        ]
        source_set = set(source_files)
        contents = {f.path: f.content for f in manifest.files if f.content and f.path in source_set}
        line_counts = {path: text.count("\n") + 1 for path, text in contents.items()}
        in_edges, out_edges = self._edge_maps(parsed_index)

        findings: list[CodeHealthFinding] = []
        findings += self._circular_imports(out_edges)
        findings += self._hub_modules(in_edges, source_files)
        findings += self._untested_modules(in_edges, source_files)
        findings += self._long_functions(parsed_index, source_files)
        findings += self._large_files(line_counts)
        findings += self._unreferenced_symbols(parsed_index, source_files, contents)

        score, deductions = self._score(findings)
        test_files = [p for p in source_files if self._is_test_file(p)]

        return CodeHealthReport(
            repo_full_name=repo_full_name,
            commit_sha=commit_sha,
            score=score,
            grade=self._grade(score),
            source_file_count=len(source_files),
            test_file_count=len(test_files),
            symbol_count=sum(1 for s in parsed_index.symbols if s.file_path in set(source_files)),
            findings=findings,
            deductions=deductions,
        )

    # ------------------------------------------------------------------
    # Classification helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_generated(path: str) -> bool:
        lowered = path.lower()
        return any(marker in lowered for marker in _GENERATED_MARKERS)

    @staticmethod
    def _is_supporting(path: str) -> bool:
        """Examples, docs, benchmarks: real code, but not the product being graded."""
        return any(seg in _SUPPORTING_DIRS for seg in path.lower().split("/")[:-1])

    @staticmethod
    def _is_package_init(path: str) -> bool:
        return path.rsplit("/", 1)[-1] == "__init__.py"

    @staticmethod
    def _is_test_file(path: str) -> bool:
        lowered = path.lower()
        segments = lowered.split("/")
        if any(segment in _TEST_DIR_SEGMENTS for segment in segments[:-1]):
            return True
        return any(marker in segments[-1] for marker in _TEST_FILE_MARKERS)

    @staticmethod
    def _edge_maps(parsed_index: ParsedRepoIndex) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
        in_edges: dict[str, set[str]] = {}
        out_edges: dict[str, set[str]] = {}
        for from_mod, to_mod in parsed_index.module_edges:
            if from_mod == to_mod:
                continue
            in_edges.setdefault(to_mod, set()).add(from_mod)
            out_edges.setdefault(from_mod, set()).add(to_mod)
        return in_edges, out_edges

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------

    def _circular_imports(self, out_edges: dict[str, set[str]]) -> list[CodeHealthFinding]:
        """Tarjan's SCC; every component of size > 1 is a cycle."""
        index_of: dict[str, int] = {}
        low: dict[str, int] = {}
        on_stack: set[str] = set()
        stack: list[str] = []
        components: list[list[str]] = []
        counter = 0

        # A package __init__ that re-exports from its submodules while
        # they import the package's globals is the standard Python
        # layout, not a smell — but it registers as a cycle through
        # __init__. Drop those aggregator nodes so only module-to-module
        # cycles are reported.
        def is_aggregator(path: str) -> bool:
            return path.rsplit("/", 1)[-1] == "__init__.py"

        out_edges = {
            src: {dst for dst in dsts if not is_aggregator(dst)}
            for src, dsts in out_edges.items()
            if not is_aggregator(src)
        }
        nodes = sorted(set(out_edges) | {t for targets in out_edges.values() for t in targets})

        def strongconnect(v: str) -> None:
            nonlocal counter
            index_of[v] = low[v] = counter
            counter += 1
            stack.append(v)
            on_stack.add(v)
            for w in sorted(out_edges.get(v, ())):
                if w not in index_of:
                    strongconnect(w)
                    low[v] = min(low[v], low[w])
                elif w in on_stack:
                    low[v] = min(low[v], index_of[w])
            if low[v] == index_of[v]:
                component = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    component.append(w)
                    if w == v:
                        break
                if len(component) > 1:
                    components.append(sorted(component))

        for node in nodes:
            if node not in index_of:
                strongconnect(node)

        findings = []
        for component in sorted(components, key=lambda c: (-len(c), c))[: self._max_per_check]:
            findings.append(CodeHealthFinding(
                check="circular_import",
                severity="medium",
                title=f"{len(component)} modules import each other in a cycle",
                detail="Cycles make modules impossible to test or reuse in isolation and cause import-order bugs. Break the cycle by moving the shared piece into a module both can import.",
                files=component,
            ))
        return findings

    def _hub_modules(self, in_edges: dict[str, set[str]], source_files: list[str]) -> list[CodeHealthFinding]:
        source = set(source_files)
        ranked = sorted(
            ((path, len(importers)) for path, importers in in_edges.items()
             if path in source and not self._is_package_init(path)),
            key=lambda item: (-item[1], item[0]),
        )
        findings = []
        for path, fan_in in ranked[: self._max_per_check]:
            if fan_in < self._hub_fan_in:
                break
            findings.append(CodeHealthFinding(
                check="hub_module",
                severity="medium" if fan_in >= self._hub_fan_in * 2 else "low",
                title=f"{fan_in} modules depend on {path.rsplit('/', 1)[-1]}",
                detail="A change here has a wide blast radius. Keep its public surface small and stable, and make sure it has tests.",
                files=[path],
                metric=fan_in,
            ))
        return findings

    def _untested_modules(self, in_edges: dict[str, set[str]], source_files: list[str]) -> list[CodeHealthFinding]:
        tests = {p for p in source_files if self._is_test_file(p)}
        if not source_files:
            return []  # nothing to test
        if not tests:
            return [CodeHealthFinding(
                check="untested_module",
                severity="high",
                title="No test files found",
                detail="Nothing in the repo looks like a test (no tests/ directory, no test_*/ *.test.* files). Every module is unverified.",
                files=[],
            )]

        covered = {
            target for target, importers in in_edges.items() if any(imp in tests for imp in importers)
        }
        # `import flask` resolves to src/flask/__init__.py. A test that
        # imports a package is exercising what the package exports, so
        # treat the package's own modules (one level down) as covered.
        for target in list(covered):
            if self._is_package_init(target):
                package_dir = target.rsplit("/", 1)[0] if "/" in target else ""
                prefix = package_dir + "/" if package_dir else ""
                for path in source_files:
                    if path.startswith(prefix):
                        covered.add(path)
        candidates = []
        for path in source_files:
            if path in tests or path in covered or self._is_package_init(path):
                continue
            dependents = len(in_edges.get(path, ()))
            if dependents == 0:
                continue  # leaf with no dependents: low value, and often an entry point
            candidates.append((path, dependents))

        candidates.sort(key=lambda item: (-item[1], item[0]))
        findings = []
        for path, dependents in candidates[: self._max_per_check]:
            findings.append(CodeHealthFinding(
                check="untested_module",
                severity="high" if dependents >= self._hub_fan_in else "medium",
                title=f"{path.rsplit('/', 1)[-1]} has no test importing it",
                detail=f"{dependents} module(s) depend on it, but no test file imports it directly. A regression here is only caught indirectly, if at all.",
                files=[path],
                metric=dependents,
            ))
        return findings

    def _long_functions(self, parsed_index: ParsedRepoIndex, source_files: list[str]) -> list[CodeHealthFinding]:
        source = set(source_files)
        long_symbols = [
            s for s in parsed_index.symbols
            if s.file_path in source and s.kind != "class"
            and (s.end_line - s.start_line + 1) >= self._long_function
        ]
        long_symbols.sort(key=lambda s: (-(s.end_line - s.start_line), s.file_path, s.name))
        findings = []
        for s in long_symbols[: self._max_per_check]:
            length = s.end_line - s.start_line + 1
            findings.append(CodeHealthFinding(
                check="long_function",
                severity="medium" if length >= self._very_long_function else "low",
                title=f"{s.name}() is {length} lines",
                detail="Long functions hide multiple responsibilities and are hard to test in isolation. Look for a natural seam to split it.",
                files=[s.file_path],
                symbol=s.name,
                line=s.start_line,
                metric=length,
            ))
        return findings

    def _large_files(self, line_counts: dict[str, int]) -> list[CodeHealthFinding]:
        ranked = sorted(
            ((p, n) for p, n in line_counts.items() if not self._is_test_file(p)),
            key=lambda item: (-item[1], item[0]),
        )
        findings = []
        for path, lines in ranked[: self._max_per_check]:
            if lines < self._large_file:
                break
            findings.append(CodeHealthFinding(
                check="large_file",
                severity="medium" if lines >= self._very_large_file else "low",
                title=f"{path.rsplit('/', 1)[-1]} is {lines} lines",
                detail="Very large files usually bundle several concerns. Splitting by responsibility makes each part easier to navigate and review.",
                files=[path],
                metric=lines,
            ))
        return findings

    def _unreferenced_symbols(
        self, parsed_index: ParsedRepoIndex, source_files: list[str], contents: dict[str, str]
    ) -> list[CodeHealthFinding]:
        source = set(source_files)
        symbols = [s for s in parsed_index.symbols if s.file_path in source]
        name_counts = Counter(s.name for s in symbols)
        called: set[str] = set()
        for s in symbols:
            called.update(s.calls)

        candidates = []
        for s in symbols:
            if s.kind == "class" or self._is_test_file(s.file_path):
                continue
            if name_counts[s.name] != 1 or s.name in called or s.name in _FRAMEWORK_HOOK_NAMES:
                continue
            if s.name.startswith("test") or s.name.startswith("__"):
                continue
            if self._referenced_in_text(s.name, s.file_path, s.start_line, contents):
                continue
            candidates.append(s)

        candidates.sort(key=lambda s: (-(s.end_line - s.start_line), s.file_path, s.name))
        findings = []
        for s in candidates[: self._max_per_check]:
            findings.append(CodeHealthFinding(
                check="unreferenced_symbol",
                severity="low",
                title=f"{s.name}() is never called by name",
                detail="No other function in the repo calls this by name. It may be dead code, a framework hook, or only used via a reference — check before deleting.",
                files=[s.file_path],
                symbol=s.name,
                line=s.start_line,
                metric=s.end_line - s.start_line + 1,
            ))
        return findings

    @staticmethod
    def _referenced_in_text(name: str, own_file: str, def_line: int, contents: dict[str, str]) -> bool:
        """
        The call graph only sees `name(...)`. A symbol is also live if
        any file names it anywhere other than its own definition line —
        a JSX tag (<Login />), an import, an export, a decorator
        argument, a route table entry, a callback reference.
        """
        pattern = re.compile(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])")
        for path, text in contents.items():
            if path == own_file:
                lines = text.split("\n")
                text = "\n".join(l for i, l in enumerate(lines, 1) if i != def_line)
            if pattern.search(text):
                return True
        return False

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _score(findings: list[CodeHealthFinding]) -> tuple[int, dict[str, int]]:
        """
        Per check, deduct the severity-weighted total but saturate
        smoothly towards the check's cap rather than hitting it on the
        third finding: cap * (1 - 1 / (1 + points / cap)). Ten low
        findings cost about half the cap; it takes far more to reach it.
        That keeps a mature project with a handful of long functions
        from scoring the same as one with fifty.
        """
        raw: dict[str, int] = {}
        for f in findings:
            raw[f.check] = raw.get(f.check, 0) + _SEVERITY_WEIGHT[f.severity]
        deductions = {}
        for check, points in raw.items():
            cap = _CHECK_CAP[check]
            deductions[check] = round(cap * (1 - 1 / (1 + points / cap)))
        return max(0, 100 - sum(deductions.values())), deductions

    @staticmethod
    def _grade(score: int) -> str:
        if score >= 90:
            return "A"
        if score >= 75:
            return "B"
        if score >= 60:
            return "C"
        if score >= 40:
            return "D"
        return "F"
