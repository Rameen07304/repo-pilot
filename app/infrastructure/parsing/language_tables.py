"""
Which node types each grammar uses, and which extension is which language.

Every one of these is a fact about somebody else's grammar, not about
this codebase, and none of them is guessable: Go keeps a type's name on
a nested type_spec, Ruby types the literal `class` keyword "class", PHP
splits calls across two node types, and Dart has no call node at all.

They live together because that is where the bugs live. A wrong entry
here does not raise — it silently produces an empty symbol list or a
diagram with nothing in it, and the only way to be sure is to parse a
real sample of the language and look, which is how each of these was
checked.
"""
import re

_EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    # .mjs and .cjs are plain JavaScript with a module flag, so the JS
    # grammar parses them. They were import-only, which meant a repo of
    # ES modules produced module edges and no symbols at all.
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".dart": "dart",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".java": "java",
    ".php": "php",
}

# Languages we can build an import graph for WITHOUT a tree-sitter
# grammar — import extraction is regex-based for every language here,
# so these get module edges (and therefore show up in the diagram) but
# fall back to window chunking for search. Vue/Svelte single-file
# components carry plain ES imports in their <script> block, so the
# JS import regex works on them as-is.
# Not a language: these contribute their asset links to the graph by
# regex, with no grammar and no import statements.
_LINK_ONLY_EXTENSIONS = (".html", ".htm")

_IMPORT_ONLY_EXTENSION_TO_LANGUAGE = {
    # Single-file components: the file is a template with a <script>
    # block inside, not a JavaScript file, so the JS grammar cannot parse
    # the whole thing. The import regex still reads the script block.
    ".vue": "javascript",
    ".svelte": "javascript",
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
    # Verified against a real parse tree. Dart is the odd one here: a
    # function's signature and its body are SIBLINGS, not parent and
    # child, so a definition node contains no code at all. See
    # _dart_body_of, without which every Dart symbol had zero calls and a
    # line range covering only its signature.
    "dart": {
        "class_definition": "class",
        "mixin_declaration": "class",
        "method_signature": "method",
        "function_signature": "function",
    },
}

# Node types verified by parsing a sample of each language rather than
# assumed: Go keeps a type's name on the nested type_spec, not on the
# type_declaration above it, and Ruby names its class node "class".
_GRAMMAR_NODE_TYPES = {
    "go": {
        "function_declaration": "function",
        "method_declaration": "method",
        "type_spec": "type",
        "type_alias": "type",
    },
    "rust": {
        "function_item": "function",
        # A trait method signature is part of the API surface, the same
        # as an interface method in Java or PHP, which are both captured.
        "function_signature_item": "function",
        "struct_item": "struct",
        "trait_item": "trait",
        "enum_item": "enum",
    },
    "ruby": {
        "class": "class",
        "module": "module",
        "method": "method",
        "singleton_method": "method",
    },
    "java": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "enum_declaration": "enum",
        "method_declaration": "method",
    },
    "php": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "trait_declaration": "trait",
        "function_definition": "function",
        "method_declaration": "method",
    },
}

_CALL_NODE_TYPE = {
    "python": ("call",),
    "javascript": ("call_expression",),
    "typescript": ("call_expression",),
    # Dart has no call node at all — see _extract_dart_calls. This entry
    # exists so the table stays exhaustive; it is never read.
    "dart": (),
    "go": ("call_expression",),
    "rust": ("call_expression",),
    # Ruby only produces a call node when the call has parentheses;
    # bare `do_thing` parses as an identifier. Best-effort, like Dart.
    "ruby": ("call",),
    "java": ("method_invocation",),
    # PHP splits free functions from method calls across two node types.
    "php": ("function_call_expression", "member_call_expression"),
}

_DEFINITION_NODE_TYPES.update(_GRAMMAR_NODE_TYPES)

# Bump when parsing changes in a way that makes an already-cached index
# wrong or incomplete: a new grammar, different node types, changed
# import resolution. Caches carrying an older value are re-parsed.
#   1 - python, javascript, typescript, dart
#   2 - go, rust, ruby, java, php fully parsed; .mjs/.cjs on the JS grammar
#   7 - `from x import *` resolves. It matched nothing before, so any
#       cached graph is missing those edges entirely -- including the
#       __init__.py -> core edge that wires most Python packages.
# Lives here because this is what it versions: a cached index is stale
# exactly when one of the tables above changes.
PARSER_VERSION = "7"

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


