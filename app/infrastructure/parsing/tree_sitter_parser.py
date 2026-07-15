"""
Concrete CodeParser implementation using tree-sitter.

Design decisions locked in during discussion:
  - Supports Python and JavaScript/TypeScript only at launch. Adding a
    third language later means adding a grammar + query strings here,
    not restructuring this class.
  - Call resolution is NAME-BASED, not full semantic analysis (no real
    "go to definition"). This is a deliberate simplification: true
    scope-aware resolution is effectively a separate project per
    language. To reduce false matches from name collisions, matches
    are preferred in this order: (1) same file, (2) files reachable
    via this file's imports, (3) anywhere else in the repo. This is
    documented here explicitly so nobody mistakes it for more than it is.
  - Symbols are parsed ONCE during ingestion (see IngestRepoUseCase)
    and handed to trace_calls() via a ParsedRepoIndex — this class
    never re-parses the whole repo per question.
  - Chunking always succeeds: supported languages get function/class-
    level chunks; anything else falls back to paragraph/fixed-window
    chunking so ingestion never fails just because a file's language
    isn't one of our two supported ones.
"""
from __future__ import annotations

import logging
import re

import tree_sitter_javascript as ts_javascript
import tree_sitter_python as ts_python
import tree_sitter_typescript as ts_typescript
from tree_sitter import Language, Node, Parser

from app.application.interfaces.code_parser import ModuleEdge, TraceStep
from app.domain.entities import CodeChunk, ParsedRepoIndex, ParsedSymbol

logger = logging.getLogger(__name__)

_EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
}

# Tree-sitter node types that count as a "symbol" worth extracting,
# per language — different grammars name these differently.
_DEFINITION_NODE_TYPES = {
    "python": {"function_definition": "function", "class_definition": "class"},
    "javascript": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
    },
    "typescript": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
    },
}

_CALL_NODE_TYPE = {
    "python": "call",
    "javascript": "call_expression",
    "typescript": "call_expression",
}

_NAME_FIELD = "name"  # consistent across these grammars for def/class nodes


class TreeSitterCodeParser:
    def __init__(self) -> None:
        self._languages: dict[str, Language] = {
            "python": Language(ts_python.language()),
            "javascript": Language(ts_javascript.language()),
            "typescript": Language(ts_typescript.language_typescript()),
        }
        self._parsers: dict[str, Parser] = {
            lang: Parser(grammar) for lang, grammar in self._languages.items()
        }

    # ------------------------------------------------------------------
    # Public interface methods
    # ------------------------------------------------------------------

    def detect_language(self, file_path: str) -> str | None:
        for ext, lang in _EXTENSION_TO_LANGUAGE.items():
            if file_path.lower().endswith(ext):
                return lang
        return None

    def extract_symbols(
        self, *, file_path: str, content: str, language: str
    ) -> list[ParsedSymbol]:
        """
        Extract all functions/classes/methods from one file's content.
        Public because IngestRepoUseCase legitimately needs this to
        build the ParsedRepoIndex once during ingestion — not an
        implementation detail to be reached around.
        """
        parser = self._parsers[language]
        tree = parser.parse(content.encode("utf-8"))
        def_types = _DEFINITION_NODE_TYPES[language]

        symbols: list[ParsedSymbol] = []

        def walk(node: Node) -> None:
            if node.type in def_types:
                name_node = node.child_by_field_name(_NAME_FIELD)
                name = name_node.text.decode("utf-8") if name_node else "<anonymous>"
                calls = self._extract_calls_within(node, language)
                symbols.append(
                    ParsedSymbol(
                        name=name,
                        kind=def_types[node.type],
                        file_path=file_path,
                        start_line=node.start_point[0] + 1,  # tree-sitter is 0-indexed
                        end_line=node.end_point[0] + 1,
                        calls=calls,
                    )
                )
            for child in node.children:
                walk(child)

        walk(tree.root_node)
        return symbols

    def chunk_file(self, *, file_path: str, content: str) -> list[CodeChunk]:
        language = self.detect_language(file_path)

        if language is None:
            return self._chunk_fallback(file_path=file_path, content=content)

        symbols = self.extract_symbols(file_path=file_path, content=content, language=language)
        if not symbols:
            # Parsed fine but found no functions/classes (e.g. a config
            # file with just constants) — fall back rather than return
            # nothing, so the file still contributes to search.
            return self._chunk_fallback(file_path=file_path, content=content)

        lines = content.splitlines()
        chunks: list[CodeChunk] = []
        for i, sym in enumerate(symbols):
            snippet = "\n".join(lines[sym.start_line - 1 : sym.end_line])
            chunks.append(
                CodeChunk(
                    id=f"{file_path}:{sym.start_line}-{sym.end_line}:{i}",
                    file_path=file_path,
                    content=snippet,
                    start_line=sym.start_line,
                    end_line=sym.end_line,
                )
            )
        return chunks

    def build_module_graph(self, all_files: dict[str, str]) -> list[ModuleEdge]:
        edges: list[ModuleEdge] = []
        for path, content in all_files.items():
            language = self.detect_language(path)
            if language is None:
                continue
            imports = self._extract_imports(content=content, language=language)
            for imported in imports:
                resolved = self._resolve_import_to_path(
                    importing_file=path, import_name=imported, all_files=all_files
                )
                if resolved:
                    edges.append(ModuleEdge(from_module=path, to_module=resolved))
        return edges

    def trace_calls(
        self,
        *,
        entry_file: str,
        entry_symbol: str,
        parsed_index: ParsedRepoIndex,
        max_hops: int = 3,
    ) -> list[TraceStep]:
        visited: set[tuple[str, str]] = set()
        steps: list[TraceStep] = []

        entry_candidates = [
            s for s in parsed_index.symbols_in_file(entry_file) if s.name == entry_symbol
        ]
        if not entry_candidates:
            logger.warning("trace_calls: entry symbol %s not found in %s", entry_symbol, entry_file)
            return steps

        self._trace_recursive(
            symbol=entry_candidates[0],
            parsed_index=parsed_index,
            remaining_hops=max_hops,
            visited=visited,
            steps=steps,
        )
        return steps

    # ------------------------------------------------------------------
    # Internal: call + import extraction
    # ------------------------------------------------------------------

    def _extract_calls_within(self, node: Node, language: str) -> list[str]:
        call_type = _CALL_NODE_TYPE[language]
        calls: list[str] = []

        def walk(n: Node) -> None:
            if n.type == call_type:
                # The callee is usually the first named child (the
                # function/identifier being called before the parens).
                callee = n.child_by_field_name("function") or (
                    n.named_children[0] if n.named_children else None
                )
                if callee is not None:
                    text = callee.text.decode("utf-8")
                    # For `obj.method()` grab just `method` — name-based
                    # matching works on the bare name, not the full path.
                    simple_name = text.rsplit(".", 1)[-1]
                    calls.append(simple_name)
            for child in n.children:
                walk(child)

        walk(node)
        return calls

    def _extract_imports(self, *, content: str, language: str) -> list[str]:
        """
        Returns raw imported module names/paths as written in source —
        resolution to an actual file path happens in _resolve_import_to_path.
        """
        imports: list[str] = []
        if language == "python":
            for line in content.splitlines():
                stripped = line.strip()
                match = re.match(r"^from\s+([\w.]+)\s+import", stripped) or re.match(
                    r"^import\s+([\w.]+)", stripped
                )
                if match:
                    imports.append(match.group(1))
        else:  # javascript / typescript
            for line in content.splitlines():
                match = re.search(r"""(?:from|require\()\s*['"]([^'"]+)['"]""", line)
                if match:
                    imports.append(match.group(1))
        return imports

    def _resolve_import_to_path(
        self, *, importing_file: str, import_name: str, all_files: dict[str, str]
    ) -> str | None:
        """
        Best-effort mapping from an import string to a file we actually
        have. External/third-party packages (e.g. "requests", "react")
        correctly resolve to None — we only draw edges for internal
        modules, never guess at external library internals.
        """
        candidate_suffixes = [
            import_name.replace(".", "/") + ".py",
            import_name.replace(".", "/") + "/__init__.py",
            import_name.lstrip("./") + ".js",
            import_name.lstrip("./") + ".ts",
            import_name.lstrip("./") + "/index.js",
        ]
        for path in all_files:
            for suffix in candidate_suffixes:
                if path == suffix or path.endswith("/" + suffix):
                    return path
        return None

    # ------------------------------------------------------------------
    # Internal: call tracing with import-aware disambiguation
    # ------------------------------------------------------------------

    def _trace_recursive(
        self,
        *,
        symbol: ParsedSymbol,
        parsed_index: ParsedRepoIndex,
        remaining_hops: int,
        visited: set[tuple[str, str]],
        steps: list[TraceStep],
    ) -> None:
        key = (symbol.file_path, symbol.name)
        if key in visited:
            return  # avoid infinite loops on circular/recursive calls
        visited.add(key)

        steps.append(
            TraceStep(
                file_path=symbol.file_path,
                symbol_name=symbol.name,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
                snippet=f"({symbol.kind} {symbol.name}, lines {symbol.start_line}-{symbol.end_line})",
            )
        )

        if remaining_hops <= 0:
            return

        # Which files does this symbol's file import? Used for the
        # "prefer imported files" disambiguation tier. module_edges
        # live on parsed_index, built once during ingestion via
        # build_module_graph, stored as (from_module, to_module) tuples.
        imported_files = {
            to_mod for (from_mod, to_mod) in parsed_index.module_edges
            if from_mod == symbol.file_path
        }

        for called_name in symbol.calls:
            candidates = parsed_index.find_by_name(called_name)
            if not candidates:
                continue  # unresolvable (builtin, external lib) — stop this branch

            best = self._pick_best_candidate(
                candidates=candidates,
                current_file=symbol.file_path,
                imported_files=imported_files,
            )
            if best is not None:
                self._trace_recursive(
                    symbol=best,
                    parsed_index=parsed_index,
                    remaining_hops=remaining_hops - 1,
                    visited=visited,
                    steps=steps,
                )

    @staticmethod
    def _pick_best_candidate(
        *, candidates: list[ParsedSymbol], current_file: str, imported_files: set[str]
    ) -> ParsedSymbol | None:
        # Tier 1: same file
        same_file = [c for c in candidates if c.file_path == current_file]
        if same_file:
            return same_file[0]
        # Tier 2: a file this one imports
        in_imports = [c for c in candidates if c.file_path in imported_files]
        if in_imports:
            return in_imports[0]
        # Tier 3: anywhere else — least confident, but still name-based
        # by design (see class docstring)
        return candidates[0]

    # ------------------------------------------------------------------
    # Internal: fallback chunking for unsupported languages
    # ------------------------------------------------------------------

    def _chunk_fallback(self, *, file_path: str, content: str) -> list[CodeChunk]:
        if not content.strip():
            return []

        if file_path.lower().endswith((".md", ".txt", ".rst")):
            paragraphs = [p for p in content.split("\n\n") if p.strip()]
            chunks: list[CodeChunk] = []
            line_cursor = 1
            for i, para in enumerate(paragraphs):
                line_count = para.count("\n") + 1
                chunks.append(
                    CodeChunk(
                        id=f"{file_path}:para:{i}",
                        file_path=file_path,
                        content=para,
                        start_line=line_cursor,
                        end_line=line_cursor + line_count - 1,
                    )
                )
                line_cursor += line_count + 1  # +1 for the blank line separator
            return chunks

        # Generic fallback: fixed-size line windows
        lines = content.splitlines()
        window = 40
        chunks = []
        for i in range(0, len(lines), window):
            block = lines[i : i + window]
            chunks.append(
                CodeChunk(
                    id=f"{file_path}:lines:{i}",
                    file_path=file_path,
                    content="\n".join(block),
                    start_line=i + 1,
                    end_line=min(i + window, len(lines)),
                )
            )
        return chunks