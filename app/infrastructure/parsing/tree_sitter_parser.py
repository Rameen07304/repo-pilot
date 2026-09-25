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
  - UI-file relations (Python -> HTML template, template -> static
    JS/CSS) are deliberately regex-based, NOT a real HTML parser — we
    have no HTML grammar wired into tree-sitter. This covers the two
    connection types that actually matter for a diagram without
    adding a new dependency or parsing HTML structurally.
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

try:
    from tree_sitter_language_pack import get_language as get_ts_language
    _DART_AVAILABLE = True
except ImportError:
    _DART_AVAILABLE = False

logger = logging.getLogger(__name__)

_EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".dart": "dart",
}

# Languages we can build an import graph for WITHOUT a tree-sitter
# grammar — import extraction is regex-based for every language here,
# so these get module edges (and therefore show up in the diagram) but
# fall back to window chunking for search. Vue/Svelte single-file
# components carry plain ES imports in their <script> block, so the
# JS import regex works on them as-is.
_IMPORT_ONLY_EXTENSION_TO_LANGUAGE = {
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".vue": "javascript",
    ".svelte": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".java": "java",
    ".php": "php",
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
    # Best-effort node type names for Dart — not independently verified
    # against a real parse tree the way Python/JS/TS were. If symbol
    # extraction comes back empty on real Dart files, these names are
    # the first thing to check and adjust.
    "dart": {
        "class_definition": "class",
        "mixin_declaration": "class",
        "method_signature": "method",
        "function_signature": "function",
    },
}

_CALL_NODE_TYPE = {
    "python": "call",
    "javascript": "call_expression",
    "typescript": "call_expression",
    "dart": "method_invocation",  # best-effort, same caveat as above
}

_NAME_FIELD = "name"  # consistent across these grammars for def/class nodes

_JS_RESOLVE_SUFFIXES = (
    "", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte",
    "/index.js", "/index.ts", "/index.jsx", "/index.tsx",
)

# Matches render("x.html", ...) or render_template("x.html", ...) —
# covers both Flask's render_template and Django's render(request, "x.html", ...)
_RENDER_TEMPLATE_PATTERN = re.compile(
    r"render(?:_template)?\([^)]*?[\"']([^\"']+\.html)[\"']", re.DOTALL
)
# Matches <script src="..."> and <link href="..."> pointing at JS/CSS
_HTML_ASSET_PATTERN = re.compile(r"""(?:src|href)=["']([^"']+\.(?:js|css))["']""")


class TreeSitterCodeParser:
    def __init__(self) -> None:
        self._languages: dict[str, Language] = {
            "python": Language(ts_python.language()),
            "javascript": Language(ts_javascript.language()),
            "typescript": Language(ts_typescript.language_typescript()),
        }

        # Grammars that were wanted and did not load. A log line is not
        # enough: a user ingesting a Dart repo would otherwise get generic
        # chunking, no import graph and no symbols, with nothing in the
        # response to say why the answers got worse.
        self._unavailable: set[str] = set()

        if _DART_AVAILABLE:
            try:
                self._languages["dart"] = get_ts_language("dart")
                logger.info("Dart grammar loaded successfully.")
            except Exception as e:
                self._unavailable.add("dart")
                logger.warning("Dart grammar unavailable, Dart files will use fallback chunking: %s", e)
        else:
            self._unavailable.add("dart")
            logger.warning(
                "tree-sitter-language-pack not installed — Dart files will use "
                "fallback chunking, no import graph or symbol extraction for Dart."
            )

        self._parsers: dict[str, Parser] = {
            lang: Parser(grammar) for lang, grammar in self._languages.items()
        }

    @property
    def unavailable_languages(self) -> set[str]:
        """
        Languages whose grammar failed to load in this environment.

        Known to happen: tree-sitter-language-pack moves the Dart grammar
        across drives while loading it, which throws on a Windows machine
        whose temp directory and install live on different drives.
        """
        return set(self._unavailable)

    # ------------------------------------------------------------------
    # Public interface methods
    # ------------------------------------------------------------------

    def detect_language(self, file_path: str) -> str | None:
        for ext, lang in _EXTENSION_TO_LANGUAGE.items():
            if file_path.lower().endswith(ext):
                # Only claim a language is "supported" if its parser
                # actually loaded — e.g. Dart's grammar can be missing if
                # tree-sitter-language-pack isn't installed. Returning None
                # here routes the file through fallback chunking instead
                # of crashing later when self._parsers[lang] doesn't exist.
                return lang if lang in self._parsers else None
        return None

    def _detect_import_language(self, file_path: str) -> str | None:
        """
        Broader than detect_language: also covers languages whose import
        graph we build by regex alone (see _IMPORT_ONLY_EXTENSION_TO_LANGUAGE).
        Dart is deliberately included even when its grammar failed to load —
        its imports are regex-extracted, so the diagram still works.
        """
        lower = file_path.lower()
        for ext, lang in _EXTENSION_TO_LANGUAGE.items():
            if lower.endswith(ext):
                return lang
        for ext, lang in _IMPORT_ONLY_EXTENSION_TO_LANGUAGE.items():
            if lower.endswith(ext):
                return lang
        return None

    @staticmethod
    def _read_go_module_path(all_files: dict[str, str]) -> str | None:
        """Module path from the shallowest go.mod, e.g. 'github.com/acme/svc'."""
        go_mods = sorted(
            (p for p in all_files if p == "go.mod" or p.endswith("/go.mod")),
            key=lambda p: p.count("/"),
        )
        for path in go_mods:
            match = re.search(r"^module\s+(\S+)", all_files[path], re.MULTILINE)
            if match:
                return match.group(1)
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
        go_module = self._read_go_module_path(all_files)
        for path, content in all_files.items():
            language = self._detect_import_language(path)

            if language is not None:
                imports = self._extract_imports(content=content, language=language)
                for imported in imports:
                    resolved = self._resolve_import_to_path(
                        importing_file=path,
                        import_name=imported,
                        all_files=all_files,
                        language=language,
                        go_module=go_module,
                    )
                    if resolved:
                        edges.append(ModuleEdge(from_module=path, to_module=resolved))

            # UI-file relations — deliberately regex-based, not a real
            # HTML parser (see class docstring). Covers the two
            # connection types that matter for a diagram: Python ->
            # template, and template -> static JS/CSS.
            if language == "python":
                edges.extend(
                    self._extract_template_edges(path=path, content=content, all_files=all_files)
                )
            if path.lower().endswith(".html"):
                edges.extend(
                    self._extract_html_asset_edges(path=path, content=content, all_files=all_files)
                )

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
        imports: list[str] = []
        if language == "python":
            for line in content.splitlines():
                stripped = line.strip()
                from_match = re.match(r"^from\s+([\w.]+)\s+import\s+\(?([\w.,\s]+)", stripped)
                if from_match:
                    base = from_match.group(1)
                    imports.append(base)
                    # `from pkg import a, b` — a/b are often submodules, so
                    # also try pkg.a / pkg.b; resolution simply finds no
                    # file when they turn out to be plain symbols.
                    for name in from_match.group(2).split(","):
                        name = name.strip().split(" as ")[0].strip()
                        if name and name != "*":
                            imports.append(f"{base}.{name}" if not base.endswith(".") else f"{base}{name}")
                    continue
                import_match = re.match(r"^import\s+([\w.]+)", stripped)
                if import_match:
                    imports.append(import_match.group(1))
        elif language == "dart":
            # Covers both: import 'package:craftore/core/x.dart';
            # and relative: import '../domain/entities/x.dart';
            for match in re.finditer(r"""import\s+['"]([^'"]+)['"]""", content):
                imports.append(match.group(1))
        elif language == "go":
            # Single `import "x"` and grouped `import ( "x"\n alias "y" )`
            for block in re.finditer(r"import\s*\(([^)]*)\)", content):
                imports.extend(re.findall(r'"([^"]+)"', block.group(1)))
            for match in re.finditer(r'^\s*import\s+(?:\w+\s+)?"([^"]+)"', content, re.MULTILINE):
                imports.append(match.group(1))
        elif language == "rust":
            # `mod x;` declares a child module file; `use crate::a::b` /
            # `use super::x` reference other modules in this crate. Tagged
            # so resolution knows which rule to apply.
            for match in re.finditer(r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+(\w+)\s*;", content, re.MULTILINE):
                imports.append(f"mod:{match.group(1)}")
            for match in re.finditer(r"^\s*(?:pub(?:\([^)]*\))?\s+)?use\s+((?:crate|super|self)(?:::[\w]+)*)", content, re.MULTILINE):
                imports.append(f"use:{match.group(1)}")
        elif language == "ruby":
            for match in re.finditer(r"""^\s*require_relative\s+['"]([^'"]+)['"]""", content, re.MULTILINE):
                imports.append(f"rel:{match.group(1)}")
            for match in re.finditer(r"""^\s*require\s+['"]([^'"]+)['"]""", content, re.MULTILINE):
                imports.append(f"abs:{match.group(1)}")
        elif language == "java":
            for match in re.finditer(r"^\s*import\s+(?!static\s)([\w.]+)\s*;", content, re.MULTILINE):
                name = match.group(1)
                if not name.startswith(("java.", "javax.")) and not name.endswith(".*"):
                    imports.append(name)
        elif language == "php":
            for match in re.finditer(
                r"""(?:require|include)(?:_once)?\s*\(?\s*(?:__DIR__\s*\.\s*)?['"]([^'"]+\.php)['"]""", content
            ):
                imports.append(f"file:{match.group(1)}")
            for match in re.finditer(r"^\s*use\s+([\w\\]+)\s*;", content, re.MULTILINE):
                imports.append(f"ns:{match.group(1)}")
        else:  # javascript / typescript (also .vue / .svelte / .mjs / .cjs)
            for line in content.splitlines():
                match = re.search(r"""(?:from|require\()\s*['"]([^'"]+)['"]""", line)
                if match:
                    imports.append(match.group(1))
        return imports

    @staticmethod
    def _find_by_suffix(candidate: str, all_files: dict[str, str]) -> str | None:
        for path in all_files:
            if path == candidate or path.endswith("/" + candidate):
                return path
        return None

    @staticmethod
    def _join_relative(importing_file: str, relative: str) -> str:
        importing_dir = importing_file.rsplit("/", 1)[0] if "/" in importing_file else ""
        parts = importing_dir.split("/") if importing_dir else []
        for segment in relative.split("/"):
            if segment == "..":
                if parts:
                    parts.pop()
            elif segment not in (".", ""):
                parts.append(segment)
        return "/".join(parts)

    def _resolve_import_to_path(
        self,
        *,
        importing_file: str,
        import_name: str,
        all_files: dict[str, str],
        language: str | None = None,
        go_module: str | None = None,
    ) -> str | None:
        if language == "python" and import_name.startswith("."):
            # Relative import: one leading dot = the importing file's own
            # package, each extra dot = one package up.
            dots = len(import_name) - len(import_name.lstrip("."))
            remainder = import_name[dots:]
            importing_dir = importing_file.rsplit("/", 1)[0] if "/" in importing_file else ""
            parts = importing_dir.split("/") if importing_dir else []
            for _ in range(dots - 1):
                if parts:
                    parts.pop()
            if remainder:
                parts.extend(remainder.split("."))
            base = "/".join(parts)
            for candidate in (f"{base}.py", f"{base}/__init__.py"):
                if candidate in all_files and candidate != importing_file:
                    return candidate
            return None
        if language == "go":
            return self._resolve_go_import(import_name, all_files, go_module)
        if language == "rust":
            return self._resolve_rust_import(importing_file, import_name, all_files)
        if language == "ruby":
            kind, _, name = import_name.partition(":")
            if kind == "rel":
                base = self._join_relative(importing_file, name)
                for candidate in (base + ".rb", base):
                    if candidate in all_files:
                        return candidate
                return None
            return self._find_by_suffix(f"lib/{name}.rb", all_files) or self._find_by_suffix(f"{name}.rb", all_files)
        if language == "java":
            return self._find_by_suffix(import_name.replace(".", "/") + ".java", all_files)
        if language == "php":
            kind, _, name = import_name.partition(":")
            if kind == "file":
                candidate = self._join_relative(importing_file, name.lstrip("/"))
                return candidate if candidate in all_files else self._find_by_suffix(name.lstrip("/"), all_files)
            # PSR-4: the vendor/root namespace maps to an arbitrary source dir
            # (App\ -> src/, app/, ...), so match on progressively shorter
            # tails of the namespace path.
            segments = name.split("\\")
            for start in range(len(segments)):
                found = self._find_by_suffix("/".join(segments[start:]) + ".php", all_files)
                if found:
                    return found
            return None

        # Dart package imports: fixed convention, package:name/x.dart -> lib/x.dart
        if import_name.startswith("package:"):
            if "/" not in import_name:
                return None
            remainder = import_name.split("/", 1)[1]
            return self._find_by_suffix(f"lib/{remainder}", all_files)

        # Common Vite/webpack alias convention: "@/x" -> "src/x". Pragmatic
        # best-effort assumption, not a guarantee — real alias mappings
        # live in vite.config.js/tsconfig.json and could point elsewhere.
        if import_name.startswith("@/"):
            remainder = import_name[2:]
            for suffix in _JS_RESOLVE_SUFFIXES:
                candidate = f"src/{remainder}{suffix}"
                if candidate in all_files:
                    return candidate
            return None

        # Relative imports — Dart, JS, TS all use "./x" / "../y/z" — resolve
        # against the importing file's OWN directory.
        if import_name.startswith("."):
            base_candidate = self._join_relative(importing_file, import_name)
            for suffix in _JS_RESOLVE_SUFFIXES + (".dart",):
                candidate = base_candidate + suffix
                if candidate in all_files:
                    return candidate
            return None

        # Absolute-style: Python dotted modules, or bare specifiers that
        # happen to match an internal path.
        candidate_suffixes = [
            import_name.replace(".", "/") + ".py",
            import_name.replace(".", "/") + "/__init__.py",
            import_name + ".js",
            import_name + ".ts",
            import_name + "/index.js",
        ]
        for path in all_files:
            for suffix in candidate_suffixes:
                if path == suffix or path.endswith("/" + suffix):
                    return path
        return None

    def _resolve_go_import(
        self, import_name: str, all_files: dict[str, str], go_module: str | None
    ) -> str | None:
        """
        Go imports name a PACKAGE (a directory). Nodes in our graph are
        files, so the edge lands on the package's most representative
        file: <dir>/<pkg>.go by convention, else doc.go, else the first
        non-test .go file. Folder-level aggregation in the diagram
        collapses this back to the directory anyway.
        """
        if not go_module:
            return None
        if import_name == go_module:
            pkg_dir = ""
        elif import_name.startswith(go_module + "/"):
            pkg_dir = import_name[len(go_module) + 1:]
        else:
            return None

        prefix = pkg_dir + "/" if pkg_dir else ""
        members = sorted(
            p for p in all_files
            if p.startswith(prefix) and p.endswith(".go") and "/" not in p[len(prefix):]
            and not p.endswith("_test.go")
        )
        if not members:
            return None
        pkg_name = pkg_dir.rsplit("/", 1)[-1]
        for preferred in (f"{prefix}{pkg_name}.go", f"{prefix}doc.go"):
            if preferred in all_files and not preferred.endswith("_test.go"):
                return preferred
        return members[0]

    def _resolve_rust_import(
        self, importing_file: str, import_name: str, all_files: dict[str, str]
    ) -> str | None:
        kind, _, name = import_name.partition(":")
        importing_dir = importing_file.rsplit("/", 1)[0] if "/" in importing_file else ""
        stem = importing_file.rsplit("/", 1)[-1].removesuffix(".rs")

        # A file's MODULE directory is where its children live: for
        # main.rs/lib.rs/mod.rs that is the file's own directory; for
        # foo.rs it is foo/ (2018-edition layout). `super` is the parent
        # module's directory, which for foo.rs is simply foo's own dir.
        if stem in ("main", "lib", "mod"):
            self_dir = importing_dir
            super_dir = importing_dir.rsplit("/", 1)[0] if "/" in importing_dir else ""
        else:
            self_dir = f"{importing_dir}/{stem}".strip("/")
            super_dir = importing_dir

        if kind == "mod":
            prefix = self_dir + "/" if self_dir else ""
            for candidate in (f"{prefix}{name}.rs", f"{prefix}{name}/mod.rs"):
                if candidate in all_files:
                    return candidate
            return None

        # `use crate::a::b::c` — resolve progressively shorter prefixes so
        # `crate::db::Pool` lands on src/db.rs even though Pool is an item.
        segments = name.split("::")
        root = segments[0]
        rest = segments[1:]
        if root == "crate":
            src_root = self._rust_src_root(importing_file, all_files)
            base_parts: list[str] = [src_root] if src_root else []
        elif root == "super":
            base_parts = [super_dir] if super_dir else []
        else:  # self
            base_parts = [self_dir] if self_dir else []

        for end in range(len(rest), 0, -1):
            base = "/".join(base_parts + rest[:end])
            for candidate in (f"{base}.rs", f"{base}/mod.rs"):
                if candidate in all_files and candidate != importing_file:
                    return candidate
        return None

    @staticmethod
    def _rust_src_root(importing_file: str, all_files: dict[str, str]) -> str:
        """Nearest ancestor 'src' directory holding main.rs or lib.rs, else ''."""
        parts = importing_file.split("/")
        for i in range(len(parts) - 1, -1, -1):
            if parts[i] == "src":
                root = "/".join(parts[: i + 1])
                if f"{root}/main.rs" in all_files or f"{root}/lib.rs" in all_files:
                    return root
        return "src" if ("src/main.rs" in all_files or "src/lib.rs" in all_files) else ""

    # ------------------------------------------------------------------
    # Internal: UI-file relation extraction (templates, static assets)
    # ------------------------------------------------------------------

    def _extract_template_edges(
        self, *, path: str, content: str, all_files: dict[str, str]
    ) -> list[ModuleEdge]:
        edges: list[ModuleEdge] = []
        for match in _RENDER_TEMPLATE_PATTERN.finditer(content):
            resolved = self._resolve_static_path(match.group(1), all_files)
            if resolved:
                edges.append(ModuleEdge(from_module=path, to_module=resolved))
        return edges

    def _extract_html_asset_edges(
        self, *, path: str, content: str, all_files: dict[str, str]
    ) -> list[ModuleEdge]:
        edges: list[ModuleEdge] = []
        for match in _HTML_ASSET_PATTERN.finditer(content):
            resolved = self._resolve_static_path(match.group(1), all_files)
            if resolved:
                edges.append(ModuleEdge(from_module=path, to_module=resolved))
        return edges

    @staticmethod
    def _resolve_static_path(name: str, all_files: dict[str, str]) -> str | None:
        name = name.lstrip("/")
        for candidate_path in all_files:
            if candidate_path == name or candidate_path.endswith("/" + name):
                return candidate_path
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
