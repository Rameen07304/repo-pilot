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
