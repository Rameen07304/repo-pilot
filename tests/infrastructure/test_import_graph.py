"""
Import-graph coverage for every stack _detect_stack can report.

The diagram is built solely from build_module_graph, so a stack that
is detected but produces no edges here is a stack whose repos get an
empty diagram. Each test uses a tiny synthetic repo in that language.
"""
import unittest

from app.infrastructure.parsing.tree_sitter_parser import TreeSitterCodeParser


def edge_set(parser, files):
    return {(e.from_module, e.to_module) for e in parser.build_module_graph(files)}


class ImportGraphPerLanguageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parser = TreeSitterCodeParser()

    def test_go_resolves_module_relative_packages(self):
        files = {
            "go.mod": "module github.com/acme/svc\n\ngo 1.22\n",
            "cmd/server/main.go": (
                'package main\n\nimport (\n\t"fmt"\n\t"github.com/acme/svc/internal/auth"\n'
                '\tdb "github.com/acme/svc/internal/store"\n)\n'
            ),
            "internal/auth/auth.go": 'package auth\n\nimport "github.com/acme/svc/internal/store"\n',
            "internal/auth/auth_test.go": "package auth\n",
            "internal/store/store.go": "package store\n",
        }
        edges = edge_set(self.parser, files)
        self.assertIn(("cmd/server/main.go", "internal/auth/auth.go"), edges)
        self.assertIn(("cmd/server/main.go", "internal/store/store.go"), edges)
        self.assertIn(("internal/auth/auth.go", "internal/store/store.go"), edges)
        self.assertFalse(any("_test.go" in t for _, t in edges))

    def test_rust_mod_and_crate_use(self):
        files = {
            "Cargo.toml": "[package]\nname = 'x'\n",
            "src/main.rs": "mod db;\nmod api;\n\nuse crate::api::routes;\n",
            "src/db.rs": "pub struct Pool;\n",
            "src/api/mod.rs": "pub mod routes;\nuse crate::db::Pool;\n",
            "src/api/routes.rs": "use super::handlers;\n",
            "src/api/handlers.rs": "",
        }
        edges = edge_set(self.parser, files)
        self.assertIn(("src/main.rs", "src/db.rs"), edges)
        self.assertIn(("src/main.rs", "src/api/mod.rs"), edges)
        self.assertIn(("src/main.rs", "src/api/routes.rs"), edges)
        self.assertIn(("src/api/mod.rs", "src/api/routes.rs"), edges)
        self.assertIn(("src/api/mod.rs", "src/db.rs"), edges)
        self.assertIn(("src/api/routes.rs", "src/api/handlers.rs"), edges)

    def test_ruby_require_relative_and_lib_require(self):
        files = {
            "Gemfile": "",
            "lib/shop.rb": "require 'shop/cart'\nrequire_relative 'shop/pricing'\nrequire 'json'\n",
            "lib/shop/cart.rb": "require_relative 'pricing'\n",
            "lib/shop/pricing.rb": "",
        }
        edges = edge_set(self.parser, files)
        self.assertIn(("lib/shop.rb", "lib/shop/cart.rb"), edges)
        self.assertIn(("lib/shop.rb", "lib/shop/pricing.rb"), edges)
        self.assertIn(("lib/shop/cart.rb", "lib/shop/pricing.rb"), edges)

    def test_java_imports_resolve_by_package_path(self):
        files = {
            "pom.xml": "",
            "src/main/java/com/acme/App.java": (
                "package com.acme;\n\nimport java.util.List;\nimport com.acme.service.OrderService;\n"
                "import static com.acme.Util.helper;\n"
            ),
            "src/main/java/com/acme/service/OrderService.java": "package com.acme.service;\nimport com.acme.repo.OrderRepo;\n",
            "src/main/java/com/acme/repo/OrderRepo.java": "package com.acme.repo;\n",
        }
        edges = edge_set(self.parser, files)
        self.assertIn(
            ("src/main/java/com/acme/App.java", "src/main/java/com/acme/service/OrderService.java"), edges
        )
        self.assertIn(
            ("src/main/java/com/acme/service/OrderService.java", "src/main/java/com/acme/repo/OrderRepo.java"),
            edges,
        )

    def test_php_require_and_psr4_use(self):
        files = {
            "composer.json": "{}",
            "public/index.php": "<?php\nrequire __DIR__ . '/../src/bootstrap.php';\nuse App\\Http\\Kernel;\n",
            "src/bootstrap.php": "<?php\nrequire_once 'config.php';\n",
            "src/config.php": "<?php\n",
            "src/Http/Kernel.php": "<?php\nnamespace App\\Http;\n",
        }
        edges = edge_set(self.parser, files)
        self.assertIn(("public/index.php", "src/bootstrap.php"), edges)
        self.assertIn(("public/index.php", "src/Http/Kernel.php"), edges)
        self.assertIn(("src/bootstrap.php", "src/config.php"), edges)

    def test_vue_and_svelte_components_are_graphed(self):
        files = {
            "package.json": '{"dependencies": {"vue": "3"}}',
            "src/main.js": "import App from './App.vue'\n",
            "src/App.vue": "<script>\nimport Header from '@/components/Header.vue'\nimport { fmt } from './utils.mjs'\n</script>\n",
            "src/components/Header.vue": "<template></template>",
            "src/utils.mjs": "export const fmt = () => {}\n",
            "src/Widget.svelte": "<script>import { fmt } from './utils.mjs'</script>",
        }
        edges = edge_set(self.parser, files)
        self.assertIn(("src/main.js", "src/App.vue"), edges)
        self.assertIn(("src/App.vue", "src/components/Header.vue"), edges)
        self.assertIn(("src/App.vue", "src/utils.mjs"), edges)
        self.assertIn(("src/Widget.svelte", "src/utils.mjs"), edges)

    def test_python_and_template_edges_still_work(self):
        files = {
            "app/main.py": "from app.routes import ask\nimport json\n",
            "app/routes/ask.py": "def view():\n    return render_template('ask.html')\n",
            "app/templates/ask.html": '<script src="/static/app.js"></script>',
            "app/static/app.js": "",
        }
        edges = edge_set(self.parser, files)
        self.assertIn(("app/main.py", "app/routes/ask.py"), edges)
        self.assertIn(("app/routes/ask.py", "app/templates/ask.html"), edges)
        self.assertIn(("app/templates/ask.html", "app/static/app.js"), edges)

    def test_python_relative_and_submodule_imports(self):
        files = {
            "pkg/__init__.py": "",
            "pkg/api/__init__.py": "",
            "pkg/api/views.py": "from . import helpers\nfrom ..core import models\nfrom .helpers import fmt\n",
            "pkg/api/helpers.py": "",
            "pkg/core/models.py": "",
        }
        edges = edge_set(self.parser, files)
        self.assertIn(("pkg/api/views.py", "pkg/api/helpers.py"), edges)
        self.assertIn(("pkg/api/views.py", "pkg/core/models.py"), edges)
        self.assertIn(("pkg/api/views.py", "pkg/api/__init__.py"), edges)

    def test_go_is_fully_parsed_not_import_only(self):
        # Go used to have no grammar, so it produced module edges and
        # nothing else: no symbols, and the two code-health checks that
        # need symbols silently found nothing. The grammar ships in the
        # language pack this project already depends on.
        self.assertEqual(self.parser.detect_language("cmd/main.go"), "go")
        symbols = self.parser.extract_symbols(
            file_path="cmd/main.go",
            content="package main\ntype Server struct{}\nfunc (s *Server) Run() {}\nfunc main() {}\n",
            language="go",
        )
        self.assertEqual(
            {(s.name, s.kind) for s in symbols},
            {("Server", "type"), ("Run", "method"), ("main", "function")},
        )

    def test_single_file_components_stay_import_only(self):
        # A .vue file is a template wrapping a <script> block, not a
        # JavaScript file, so the JS grammar cannot parse the whole thing.
        # The import regex still reads the script block.
        self.assertIsNone(self.parser.detect_language("src/App.vue"))
        chunks = self.parser.chunk_file(
            file_path="src/App.vue",
            content="<template><div/></template>\n<script>export default {}</script>\n",
        )
        self.assertEqual(len(chunks), 1)


if __name__ == "__main__":
    unittest.main()


class FullySupportedLanguagesTest(unittest.TestCase):
    """
    The five the product advertises used to be import-only: module edges
    and nothing else, so search fell back to window chunks and the two
    code-health checks that need symbols silently found nothing. Each
    node type below was read off the grammar rather than assumed.
    """

    def setUp(self):
        self.parser = TreeSitterCodeParser()

    CASES = {
        "srv.go": (
            "go",
            'package main\ntype Server struct{}\nfunc (s *Server) Run() { helper() }\nfunc helper() {}\n',
            {("Server", "type"), ("Run", "method"), ("helper", "function")},
        ),
        "srv.rs": (
            "rust",
            "struct Server {}\ntrait Runner { fn go(&self); }\nfn helper() {}\n",
            {("Server", "struct"), ("Runner", "trait"), ("go", "function"), ("helper", "function")},
        ),
        "srv.rb": (
            "ruby",
            "class Server\n  def run\n  end\nend\nmodule Util\nend\n",
            {("Server", "class"), ("run", "method"), ("Util", "module")},
        ),
        "Srv.java": (
            "java",
            "public class Server {\n  public void run() {}\n}\ninterface I { void go(); }\n",
            {("Server", "class"), ("run", "method"), ("I", "interface"), ("go", "method")},
        ),
        "srv.php": (
            "php",
            "<?php\nclass Server {\n  public function run() {}\n}\nfunction helper() {}\n",
            {("Server", "class"), ("run", "method"), ("helper", "function")},
        ),
        "srv.mjs": (
            "javascript",
            "export class Server { run() {} }\nexport function helper() {}\n",
            {("Server", "class"), ("run", "method"), ("helper", "function")},
        ),
    }

    def test_each_language_is_detected(self):
        for path, (lang, _src, _want) in self.CASES.items():
            with self.subTest(path=path):
                self.assertEqual(self.parser.detect_language(path), lang)

    def test_each_language_yields_symbols(self):
        for path, (lang, src, want) in self.CASES.items():
            with self.subTest(path=path):
                symbols = self.parser.extract_symbols(file_path=path, content=src, language=lang)
                self.assertEqual({(s.name, s.kind) for s in symbols}, want)

    def test_no_symbol_is_anonymous(self):
        # Ruby's grammar types the literal `class` keyword "class", the
        # same as a class declaration, so every class came back twice
        # until unnamed tokens were excluded.
        for path, (lang, src, _want) in self.CASES.items():
            with self.subTest(path=path):
                symbols = self.parser.extract_symbols(file_path=path, content=src, language=lang)
                self.assertNotIn("<anonymous>", [s.name for s in symbols])

    def test_calls_are_traced_where_the_grammar_allows(self):
        # PHP splits free functions and method calls across two node
        # types; a single-type lookup would have missed one of them.
        symbols = self.parser.extract_symbols(
            file_path="srv.php",
            content="<?php\nclass S { public function run() { helper(); $this->other(); } }\n",
            language="php",
        )
        calls = {c for s in symbols for c in s.calls}
        self.assertIn("helper", calls)
        self.assertIn("other", calls)


class ImportProvenanceTest(unittest.TestCase):
    """
    Every edge records the line its import was written on.

    This is what the architecture export stakes its credibility on: a
    reader who does not believe an arrow can open that file at that line
    and read the import. A line that is merely close is worse than none,
    so each case below is checked against the exact statement.
    """

    @classmethod
    def setUpClass(cls):
        cls.parser = TreeSitterCodeParser()

    def lines_for(self, files, frm, to):
        return sorted(
            e.line
            for e in self.parser.build_module_graph(files)
            if e.from_module == frm and e.to_module == to
        )

    def assert_line_says(self, files, frm, to, expected_line, expected_text):
        lines = self.lines_for(files, frm, to)
        self.assertIn(expected_line, lines, f"{frm} -> {to}: got lines {lines}")
        source = files[frm].splitlines()[expected_line - 1]
        self.assertIn(expected_text, source)

    def test_python_reports_the_import_statement_line(self):
        files = {
            "app/main.py": "import os\n\n\nfrom app.db import Pool\n",
            "app/db.py": "",
        }
        self.assert_line_says(files, "app/main.py", "app/db.py", 4, "from app.db import Pool")

    def test_the_same_import_twice_yields_both_lines(self):
        files = {
            "app/main.py": "from app.db import Pool\nx = 1\nfrom app.db import Session\n",
            "app/db.py": "",
        }
        self.assertEqual(self.lines_for(files, "app/main.py", "app/db.py"), [1, 3])

    def test_a_grouped_go_import_reports_its_own_line_not_the_blocks(self):
        # `import (` opens on line 3; the path that resolves is on line 5.
        # Reporting the block would send a reader to the wrong statement.
        files = {
            "go.mod": "module github.com/acme/svc\n",
            "cmd/main.go": (
                'package main\n\nimport (\n\t"fmt"\n\t"github.com/acme/svc/store"\n)\n'
            ),
            "store/store.go": "package store\n",
        }
        self.assert_line_says(
            files, "cmd/main.go", "store/store.go", 5, "github.com/acme/svc/store"
        )

    def test_every_fully_parsed_language_records_a_line(self):
        cases = {
            "rust": (
                {"Cargo.toml": "", "src/main.rs": "// head\nmod db;\n", "src/db.rs": ""},
                "src/main.rs", "src/db.rs", 2, "mod db;",
            ),
            "ruby": (
                {
                    "Gemfile": "",
                    "lib/shop.rb": "# comment\n\nrequire_relative 'cart'\n",
                    "lib/cart.rb": "",
                },
                "lib/shop.rb", "lib/cart.rb", 3, "require_relative 'cart'",
            ),
            "java": (
                {
                    "pom.xml": "",
                    "src/main/java/com/acme/App.java": (
                        "package com.acme;\n\nimport com.acme.svc.Order;\n"
                    ),
                    "src/main/java/com/acme/svc/Order.java": "package com.acme.svc;\n",
                },
                "src/main/java/com/acme/App.java",
                "src/main/java/com/acme/svc/Order.java",
                3, "import com.acme.svc.Order;",
            ),
            "php": (
                {
                    "composer.json": "{}",
                    "public/index.php": "<?php\n// boot\nrequire 'src/boot.php';\n",
                    "src/boot.php": "<?php\n",
                },
                "public/index.php", "src/boot.php", 3, "require 'src/boot.php';",
            ),
            "javascript": (
                {
                    "package.json": "{}",
                    "src/app.js": "// header\n\nimport {x} from './util.js';\n",
                    "src/util.js": "",
                },
                "src/app.js", "src/util.js", 3, "import {x} from './util.js';",
            ),
            "dart": (
                {
                    "pubspec.yaml": "name: x\n",
                    "lib/main.dart": "// head\nimport 'core/db.dart';\n",
                    "lib/core/db.dart": "",
                },
                "lib/main.dart", "lib/core/db.dart", 2, "import 'core/db.dart';",
            ),
        }
        for language, (files, frm, to, line, text) in cases.items():
            with self.subTest(language=language):
                self.assert_line_says(files, frm, to, line, text)

    def test_template_and_asset_edges_are_line_attributed_too(self):
        files = {
            "app/views.py": "def home():\n    return render_template('home.html')\n",
            "app/templates/home.html": '<html>\n<body>\n<script src="/static/app.js"></script>\n',
            "app/static/app.js": "",
        }
        self.assert_line_says(
            files, "app/views.py", "app/templates/home.html", 2, "render_template"
        )
        self.assert_line_says(
            files, "app/templates/home.html", "app/static/app.js", 3, "app.js"
        )

    def test_the_parser_reports_whether_it_reads_a_file_at_all(self):
        # The export's coverage section is built from this, so a wrong
        # answer here becomes a false claim in the document.
        for path in ("a.py", "a.go", "a.vue", "a.html", "a.HTML"):
            with self.subTest(path=path):
                self.assertTrue(self.parser.reads_dependencies_from(path))
        for path in ("a.kt", "a.css", "a.swift", "README.md"):
            with self.subTest(path=path):
                self.assertFalse(self.parser.reads_dependencies_from(path))


class ControlFlowTest(unittest.TestCase):
    """
    Flowchart shapes, read off the syntax tree.

    Every grammar names things differently and the differences are not
    guessable: Ruby calls its if `if`, Rust calls it `if_expression` and
    wraps it in a statement, Dart exposes no condition field at all, and
    Go hides its statements behind a statement_list. Each of those was a
    silently empty or mislabelled chart before it was checked here.
    """

    CASES = {
        "python": ("def f():\n    if x > 1:\n        a()\n    else:\n        b()\n", "f", 1),
        "javascript": ("function f(){ if (x > 1) { a(); } else { b(); } }", "f", 1),
        "java": ("class C{ int f(){ if(x>1){a();}else{b();} return 1; } }", "f", 1),
        "go": ("package m\nfunc f(){ if x > 1 { a() } else { b() } }", "f", 2),
        "dart": ("void f(){ if (uid == null) { a(); } else { b(); } }", "f", 1),
        "ruby": ("def f\n if x > 1\n  a\n end\nend", "f", 1),
        "rust": ("fn f(){ if x > 1 { a(); } }", "f", 1),
        "php": ("<?php function f(){ if ($x > 1) { a(); } }", "f", 1),
    }

    def test_every_language_finds_the_decision(self):
        from app.infrastructure.parsing.control_flow import Decision, flow_for_symbol

        for language, (source, symbol, line) in self.CASES.items():
            with self.subTest(language=language):
                result = flow_for_symbol(
                    content=source, language=language, symbol=symbol, start_line=line
                )
                self.assertIsNotNone(result, f"{language}: no flow found")
                decisions = [i for i in result[0] if isinstance(i, Decision)]
                self.assertEqual(len(decisions), 1, f"{language}: {result[0]}")

    def test_the_condition_is_the_real_text_not_a_placeholder(self):
        # A diamond reading "condition" tells a reader nothing. Dart has
        # no condition field, so it needs the first-child fallback.
        from app.infrastructure.parsing.control_flow import Decision, flow_for_symbol

        for language, (source, symbol, line) in self.CASES.items():
            with self.subTest(language=language):
                flow = flow_for_symbol(
                    content=source, language=language, symbol=symbol, start_line=line
                )[0]
                label = next(i for i in flow if isinstance(i, Decision)).label
                self.assertIn("x" if language != "dart" else "uid", label)
                self.assertNotEqual(label, "condition")

    def test_both_branches_are_kept(self):
        from app.infrastructure.parsing.control_flow import Decision, flow_for_symbol

        flow = flow_for_symbol(
            content=self.CASES["python"][0], language="python", symbol="f", start_line=1
        )[0]
        decision = next(i for i in flow if isinstance(i, Decision))
        self.assertTrue(decision.yes)
        self.assertTrue(decision.no)

    def test_the_file_root_is_not_mistaken_for_the_function(self):
        # The root node starts on line 1 too, and its first child is the
        # definition, so it answered to the name and won on size.
        from app.infrastructure.parsing.control_flow import flow_for_symbol

        result = flow_for_symbol(
            content="void f(){ if (a) { b(); } }", language="dart", symbol="f", start_line=1
        )
        self.assertIsNotNone(result)
        self.assertTrue(result[0])

    def test_an_unknown_symbol_returns_nothing_rather_than_guessing(self):
        from app.infrastructure.parsing.control_flow import flow_for_symbol

        self.assertIsNone(
            flow_for_symbol(content="def f():\n    pass\n", language="python",
                            symbol="nope", start_line=1)
        )

    def test_a_long_function_is_truncated_not_dumped(self):
        from app.infrastructure.parsing.control_flow import MAX_STEPS, flow_for_symbol

        body = "\n".join(f"    step{i}()" for i in range(MAX_STEPS + 20))
        items, truncated = flow_for_symbol(
            content=f"def f():\n{body}\n", language="python", symbol="f", start_line=1
        )
        self.assertTrue(truncated)
        self.assertLessEqual(len(items), MAX_STEPS)

    def test_the_branch_count_equals_what_the_chart_would_draw(self):
        # The ranking signal and the drawing must not drift. A first
        # version counted every conditional expression anywhere in the
        # subtree, so a Flutter build() advertised ten branches and then
        # charted none — a ternary inside a widget argument is not a
        # statement. Worse than no ranking.
        from tree_sitter_language_pack import get_parser

        from app.infrastructure.parsing.control_flow import (
            Decision,
            Loop,
            build_flow,
            count_branches,
        )

        def drawn(items):
            total = 0
            for item in items:
                if isinstance(item, Decision):
                    total += 1 + drawn(item.yes) + drawn(item.no)
                elif isinstance(item, Loop):
                    total += 1 + drawn(item.body)
            return total

        samples = {
            # A ternary in a widget tree: counted as zero, because that is
            # what gets drawn.
            "dart": "class W { Widget build() { return Row(children:[ ok ? A() : B() ]); } }",
            "python": "def f(x):\n    if x:\n        a()\n    for i in y:\n        b()\n",
            "javascript": "function f(x){ if(x){a();} while(y){b();} }",
        }
        for language, source in samples.items():
            with self.subTest(language=language):
                tree = get_parser(language).parse(source.encode())
                body = max(
                    (n for n in self._walk(tree.root_node)
                     if n.type in ("block", "function_body", "statement_block")),
                    key=lambda n: n.end_byte - n.start_byte,
                )
                flow, _ = build_flow(body)
                self.assertEqual(count_branches(body), drawn(flow))

    @staticmethod
    def _walk(node):
        stack = [node]
        while stack:
            current = stack.pop()
            yield current
            stack.extend(current.children)


class StarImportTest(unittest.TestCase):
    """
    `from pkg.mod import *` is how most Python packages re-export their
    own insides from __init__.py. The name group in the import pattern
    had no `*` in it, so the line matched nothing at all and even the
    module being imported was lost -- a package whose __init__ pulls in
    its core had no edge to that core.

    Found by running megadose/nqntnqnqmb through the real app: six
    files, and an import graph with zero edges in it.
    """

    def test_a_star_import_still_produces_an_edge(self):
        parser = TreeSitterCodeParser()
        edges = parser.build_module_graph({
            "nqntnqnqmb/__init__.py": "from nqntnqnqmb.core import *\n",
            "nqntnqnqmb/core.py": "def getCompanyFromName(): pass\n",
        })

        self.assertEqual(
            [(e.from_module, e.to_module) for e in edges],
            [("nqntnqnqmb/__init__.py", "nqntnqnqmb/core.py")],
        )
        self.assertEqual(edges[0].line, 1, "and it still carries the line it came from")

    def test_a_relative_star_import_resolves_too(self):
        parser = TreeSitterCodeParser()
        edges = parser.build_module_graph({
            "pkg/__init__.py": "from .core import *\n",
            "pkg/core.py": "def f(): pass\n",
        })
        self.assertEqual(
            [(e.from_module, e.to_module) for e in edges],
            [("pkg/__init__.py", "pkg/core.py")],
        )

    def test_the_star_itself_is_not_treated_as_a_submodule(self):
        # The base module is what resolves; "pkg.core.*" is not a file
        # and must not be looked up as one.
        parser = TreeSitterCodeParser()
        found = parser._extract_imports(content="from pkg.core import *\n", language="python")
        self.assertEqual(found, [("pkg.core", 1)])

    def test_named_imports_still_work_unchanged(self):
        parser = TreeSitterCodeParser()
        found = parser._extract_imports(
            content="from pkg.core import alpha, beta\n", language="python"
        )
        self.assertIn(("pkg.core", 1), found)
        self.assertIn(("pkg.core.alpha", 1), found)
        self.assertIn(("pkg.core.beta", 1), found)
