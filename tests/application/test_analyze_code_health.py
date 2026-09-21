"""
AnalyzeCodeHealthUseCase — every check is exercised against a small
synthetic repo where the expected findings are known exactly. No LLM
is involved in this use case, so there is nothing to stub but the cache.
"""
import asyncio
import unittest

from app.application.use_cases.analyze_code_health import AnalyzeCodeHealthUseCase, RepoNotIndexedError
from app.domain.entities import FileNode, ParsedRepoIndex, ParsedSymbol, RepoManifest
from app.domain.value_objects import RepoUrl

REPO = "acme/widgets"
SHA = "abc123"


class StaticCache:
    def __init__(self, files: dict[str, str], symbols, edges):
        self.manifest = RepoManifest(
            repo_url=RepoUrl(f"https://github.com/{REPO}"), commit_sha=SHA,
            files=[FileNode(path=p, size_bytes=len(c), content=c) for p, c in files.items()],
        )
        self.index = ParsedRepoIndex(repo_full_name=REPO, commit_sha=SHA, symbols=symbols, module_edges=edges)

    async def get(self, *_):
        return self.manifest

    async def get_parsed_index(self, *_):
        return self.index


class EmptyCache:
    async def get(self, *_):
        return None

    async def get_parsed_index(self, *_):
        return None


def sym(name, path, start, end, calls=(), kind="function"):
    return ParsedSymbol(name=name, kind=kind, file_path=path, start_line=start, end_line=end, calls=list(calls))


def run(files, symbols=(), edges=(), **kwargs):
    uc = AnalyzeCodeHealthUseCase(cache=StaticCache(files, list(symbols), list(edges)), **kwargs)
    return asyncio.run(uc.execute(repo_full_name=REPO, commit_sha=SHA))


def by_check(report, check):
    return [f for f in report.findings if f.check == check]


class CodeHealthTest(unittest.TestCase):
    def test_not_ingested(self):
        uc = AnalyzeCodeHealthUseCase(cache=EmptyCache())
        with self.assertRaises(RepoNotIndexedError):
            asyncio.run(uc.execute(repo_full_name=REPO, commit_sha=SHA))

    def test_clean_repo_scores_100(self):
        files = {"app/a.py": "def a():\n    pass\n", "tests/test_a.py": "from app import a\n"}
        report = run(files, symbols=[sym("a", "app/a.py", 1, 2)], edges=[("tests/test_a.py", "app/a.py")])
        self.assertEqual(report.score, 100)
        self.assertEqual(report.grade, "A")
        self.assertEqual(report.findings, [])
        self.assertEqual(report.source_file_count, 2)
        self.assertEqual(report.test_file_count, 1)

    def test_circular_imports_found_via_scc(self):
        files = {p: "" for p in ("a.py", "b.py", "c.py", "d.py", "tests/test_x.py")}
        edges = [("a.py", "b.py"), ("b.py", "c.py"), ("c.py", "a.py"), ("d.py", "a.py"), ("tests/test_x.py", "a.py"),
                 ("tests/test_x.py", "b.py"), ("tests/test_x.py", "c.py")]
        report = run(files, edges=edges)
        cycles = by_check(report, "circular_import")
        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0].files, ["a.py", "b.py", "c.py"])
        self.assertEqual(cycles[0].severity, "high")  # 3-module cycle
        self.assertNotIn("d.py", cycles[0].files)

    def test_two_module_cycle_is_medium(self):
        files = {"a.py": "", "b.py": "", "tests/t.py": ""}
        report = run(files, edges=[("a.py", "b.py"), ("b.py", "a.py"), ("tests/t.py", "a.py"), ("tests/t.py", "b.py")])
        self.assertEqual(by_check(report, "circular_import")[0].severity, "medium")

    def test_hub_module_threshold_and_severity(self):
        files = {f"p{i}.py": "" for i in range(13)} | {"hub.py": "", "tests/test_hub.py": ""}
        edges = [(f"p{i}.py", "hub.py") for i in range(12)] + [("tests/test_hub.py", "hub.py")]
        report = run(files, edges=edges, hub_fan_in=6)
        hubs = by_check(report, "hub_module")
        self.assertEqual(len(hubs), 1)
        self.assertEqual(hubs[0].files, ["hub.py"])
        self.assertEqual(hubs[0].metric, 13)
        self.assertEqual(hubs[0].severity, "medium")  # >= 2x threshold

    def test_untested_modules_ranked_by_dependents_and_leaves_skipped(self):
        files = {"core.py": "", "util.py": "", "leaf.py": "", "a.py": "", "b.py": "", "tests/test_util.py": ""}
        edges = [("a.py", "core.py"), ("b.py", "core.py"), ("a.py", "util.py"), ("tests/test_util.py", "util.py"), ("leaf.py", "a.py")]
        report = run(files, edges=edges)
        untested = by_check(report, "untested_module")
        paths = [f.files[0] for f in untested]
        self.assertIn("core.py", paths)  # 2 dependents, no test
        self.assertNotIn("util.py", paths)  # a test imports it
        self.assertNotIn("leaf.py", paths)  # nothing depends on it
        self.assertEqual(paths[0], "core.py")  # most dependents first

    def test_no_tests_at_all_is_one_high_finding(self):
        files = {"a.py": "", "b.py": ""}
        report = run(files, edges=[("a.py", "b.py")])
        untested = by_check(report, "untested_module")
        self.assertEqual(len(untested), 1)
        self.assertEqual(untested[0].severity, "high")
        self.assertEqual(untested[0].files, [])

    def test_long_functions_thresholds(self):
        files = {"a.py": "\n" * 200, "tests/t.py": ""}
        symbols = [
            sym("short", "a.py", 1, 10),
            sym("longish", "a.py", 20, 90),  # 71 lines -> low
            sym("huge", "a.py", 100, 230),  # 131 lines -> medium
            sym("Big", "a.py", 1, 200, kind="class"),  # classes are exempt
        ]
        report = run(files, symbols=symbols, edges=[("tests/t.py", "a.py")], long_function_lines=60, very_long_function_lines=120)
        longs = by_check(report, "long_function")
        self.assertEqual([(f.symbol, f.severity) for f in longs], [("huge", "medium"), ("longish", "low")])
        self.assertEqual(longs[0].line, 100)

    def test_large_files(self):
        files = {"big.py": "x\n" * 1200, "mid.py": "x\n" * 600, "small.py": "x\n" * 50, "tests/t.py": ""}
        report = run(files, edges=[("tests/t.py", "big.py"), ("tests/t.py", "mid.py"), ("tests/t.py", "small.py")])
        large = by_check(report, "large_file")
        self.assertEqual([(f.files[0], f.severity) for f in large], [("big.py", "medium"), ("mid.py", "low")])

    def test_unreferenced_symbols_respect_calls_text_refs_and_hooks(self):
        files = {
            "app.py": "def dead():\n    pass\n\ndef used():\n    pass\n\ndef main():\n    used()\n\ndef Widget():\n    pass\n\nroutes = [Widget]\n",
            "other.py": "from app import helper\n",
            "lib.py": "def helper():\n    pass\n",
            "tests/t.py": "",
        }
        symbols = [
            sym("dead", "app.py", 1, 2),
            sym("used", "app.py", 4, 5),
            sym("main", "app.py", 7, 8, calls=["used"]),
            sym("Widget", "app.py", 10, 11),
            sym("helper", "lib.py", 1, 2),
        ]
        report = run(files, symbols=symbols, edges=[("tests/t.py", "app.py"), ("tests/t.py", "lib.py")])
        names = {f.symbol for f in by_check(report, "unreferenced_symbol")}
        self.assertEqual(names, {"dead"})  # used: called; main: hook; Widget: same-file text ref; helper: imported elsewhere

    def test_shared_names_are_never_reported_as_dead(self):
        files = {"a.py": "def process():\n    pass\n", "b.py": "def process():\n    pass\n", "tests/t.py": ""}
        symbols = [sym("process", "a.py", 1, 2), sym("process", "b.py", 1, 2)]
        report = run(files, symbols=symbols, edges=[("tests/t.py", "a.py"), ("tests/t.py", "b.py")])
        self.assertEqual(by_check(report, "unreferenced_symbol"), [])

    def test_generated_and_non_source_files_are_ignored(self):
        files = {"a.py": "", "README.md": "x\n" * 2000, "gen.g.dart": "x\n" * 2000, "tests/t.py": ""}
        report = run(files, edges=[("tests/t.py", "a.py")])
        self.assertEqual(by_check(report, "large_file"), [])
        self.assertEqual(report.source_file_count, 2)

    def test_score_caps_per_check(self):
        # 30 long functions would be 30 * 3 = 90 points uncapped; cap is 16.
        files = {"a.py": "\n" * 5000, "tests/t.py": ""}
        symbols = [sym(f"f{i}", "a.py", i * 150 + 1, i * 150 + 130) for i in range(30)]
        report = run(files, symbols=symbols, edges=[("tests/t.py", "a.py")], max_findings_per_check=30)
        self.assertEqual(len(by_check(report, "long_function")), 30)
        self.assertEqual(report.deductions["long_function"], 16)
        self.assertEqual(report.score, 100 - sum(report.deductions.values()))

    def test_findings_per_check_are_capped(self):
        files = {"a.py": "\n" * 5000, "tests/t.py": ""}
        symbols = [sym(f"f{i}", "a.py", i * 150 + 1, i * 150 + 130) for i in range(30)]
        report = run(files, symbols=symbols, edges=[("tests/t.py", "a.py")], max_findings_per_check=5)
        self.assertEqual(len(by_check(report, "long_function")), 5)


if __name__ == "__main__":
    unittest.main()
