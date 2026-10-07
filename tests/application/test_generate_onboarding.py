"""
GenerateOnboardingUseCase — deterministic ranking, LLM only for prose.

The assertions that matter here are the ones about WHICH files get
picked and in what order: that part must hold with no LLM at all, so
most tests run with llm_client=None.
"""
import asyncio
import unittest

from app.application.interfaces.llm_client import LLMProviderError
from app.application.use_cases.generate_onboarding import (
    GenerateOnboardingUseCase,
    RepoNotIngestedError,
)
from app.domain.entities import FileNode, ParsedRepoIndex, ParsedSymbol, RepoManifest
from app.domain.value_objects import RepoUrl


class StubCache:
    def __init__(self, manifest, index):
        self._manifest = manifest
        self._index = index

    async def get(self, repo_full_name, commit_sha):
        return self._manifest

    async def get_parsed_index(self, repo_full_name, commit_sha):
        return self._index


def manifest_for(paths, *, entry_points=(), summary="A test project.", stack=("python",)):
    return RepoManifest(
        repo_url=RepoUrl("https://github.com/acme/app"),
        commit_sha="sha1",
        files=[FileNode(path=p, size_bytes=100, content=f"# {p}\n") for p in paths],
        detected_stack=list(stack),
        entry_points=list(entry_points),
        summary=summary,
    )


def index_for(edges, symbols=()):
    return ParsedRepoIndex(
        repo_full_name="acme/app", commit_sha="sha1", symbols=list(symbols), module_edges=list(edges)
    )


def run(use_case):
    return asyncio.run(use_case.execute(repo_full_name="acme/app", commit_sha="sha1"))


def build(manifest, index, **kwargs):
    kwargs.setdefault("llm_client", None)
    kwargs.setdefault("use_llm_narration", False)
    return GenerateOnboardingUseCase(cache=StubCache(manifest, index), **kwargs)


class EntryPointDetectionTest(unittest.TestCase):
    def test_manifest_entry_points_are_used(self):
        m = manifest_for(["app/main.py", "app/core.py"], entry_points=["app/main.py"])
        guide = run(build(m, index_for([("app/main.py", "app/core.py")])))
        self.assertIn("app/main.py", guide.entry_points)

    def test_bootstrap_basenames_are_detected_without_a_graph(self):
        m = manifest_for(["src/index.ts", "src/util.ts"])
        guide = run(build(m, index_for([])))
        self.assertIn("src/index.ts", guide.entry_points)
        self.assertFalse(guide.graph_available)

    def test_graph_roots_count_as_entry_points(self):
        # cli/run.py imports things and nothing imports it -> a root.
        edges = [("cli/run.py", "core/a.py"), ("cli/run.py", "core/b.py")]
        m = manifest_for(["cli/run.py", "core/a.py", "core/b.py"])
        guide = run(build(m, index_for(edges)))
        self.assertIn("cli/run.py", guide.entry_points)

    def test_documentation_is_never_an_entry_point(self):
        m = manifest_for(["README.md", "docs/guide.md", "app/main.py"])
        guide = run(build(m, index_for([])))
        self.assertEqual(guide.entry_points, ["app/main.py"])


class RankingTest(unittest.TestCase):
    def test_most_imported_file_outranks_a_leaf(self):
        edges = [
            ("a.py", "core/shared.py"),
            ("b.py", "core/shared.py"),
            ("c.py", "core/shared.py"),
            ("a.py", "core/lonely.py"),
        ]
        m = manifest_for(["a.py", "b.py", "c.py", "core/shared.py", "core/lonely.py"])
        guide = run(build(m, index_for(edges)))
        order = [s.file_path for s in guide.reading_order]
        self.assertIn("core/shared.py", order)
        self.assertLess(order.index("core/shared.py"), order.index("core/lonely.py"))

    def test_entry_points_lead_the_reading_order(self):
        edges = [("app/main.py", "core/shared.py"), ("b.py", "core/shared.py")]
        m = manifest_for(["app/main.py", "b.py", "core/shared.py"], entry_points=["app/main.py"])
        guide = run(build(m, index_for(edges)))
        self.assertEqual(guide.reading_order[0].file_path, "app/main.py")
        self.assertTrue(guide.reading_order[0].is_entry_point)

    def test_size_alone_does_not_win(self):
        # huge.py is the biggest file but nothing imports it.
        edges = [("a.py", "small_hub.py"), ("b.py", "small_hub.py")]
        m = manifest_for(["a.py", "b.py", "small_hub.py", "huge.py"])
        for f in m.files:
            if f.path == "huge.py":
                f.size_bytes = 900_000
                f.content = "x\n" * 40_000
        guide = run(build(m, index_for(edges)))
        order = [s.file_path for s in guide.reading_order]
        self.assertIn("small_hub.py", order)
        self.assertLess(order.index("small_hub.py"), order.index("huge.py") if "huge.py" in order else 99)

    def test_reading_order_respects_the_max_files_cap(self):
        paths = [f"mod{i}.py" for i in range(30)]
        edges = [(p, "core.py") for p in paths]
        m = manifest_for(paths + ["core.py"])
        guide = run(build(m, index_for(edges), max_files=6))
        self.assertLessEqual(len(guide.reading_order), 6)

    def test_each_step_carries_its_real_graph_relationships(self):
        edges = [("api/routes.py", "core/db.py"), ("cli/main.py", "api/routes.py")]
        symbols = [
            ParsedSymbol(name="Router", kind="class", file_path="api/routes.py",
                         start_line=1, end_line=20),
            ParsedSymbol(name="handle", kind="function", file_path="api/routes.py",
                         start_line=5, end_line=9),
        ]
        m = manifest_for(["api/routes.py", "core/db.py", "cli/main.py"])
        guide = run(build(m, index_for(edges, symbols)))
        step = next(s for s in guide.reading_order if s.file_path == "api/routes.py")
        self.assertEqual(step.depends_on, ["core/db.py"])
        self.assertEqual(step.depended_on_by, ["cli/main.py"])
        self.assertEqual(step.key_symbols[0], "Router")  # classes first

    def test_no_file_outside_the_manifest_is_ever_suggested(self):
        edges = [("ghost.py", "core.py")]  # ghost.py was never ingested
        m = manifest_for(["core.py"])
        guide = run(build(m, index_for(edges)))
        for step in guide.reading_order:
            self.assertIn(step.file_path, {"core.py"})


class EmptyAndDegradedRepoTest(unittest.TestCase):
    def test_repo_with_no_edges_still_produces_a_guide(self):
        m = manifest_for(["app/main.py", "helper.py"])
        guide = run(build(m, index_for([])))
        self.assertFalse(guide.graph_available)
        self.assertTrue(guide.reading_order)
        self.assertTrue(guide.suggested_questions)

    def test_completely_empty_repo_does_not_crash(self):
        guide = run(build(manifest_for([]), index_for([])))
        self.assertEqual(guide.reading_order, [])
        self.assertTrue(guide.suggested_questions)  # generic fallbacks only

    def test_missing_cache_entry_raises(self):
        use_case = GenerateOnboardingUseCase(cache=StubCache(None, None), llm_client=None)
        with self.assertRaises(RepoNotIngestedError):
            run(use_case)

    def test_every_step_has_narration_even_with_no_llm(self):
        edges = [("a.py", "core.py")]
        guide = run(build(manifest_for(["a.py", "core.py"]), index_for(edges)))
        for step in guide.reading_order:
            self.assertTrue(step.why_it_matters)
            self.assertTrue(step.what_to_understand)


class NarrationTest(unittest.TestCase):
    def test_llm_prose_is_applied_to_matching_paths_only(self):
        class NarratingLLM:
            async def generate(self, prompt, **kwargs):
                return (
                    '{"files": {"core.py": {"why": "It is the hub.", "understand": "The API."},'
                    ' "invented.py": {"why": "nope", "understand": "nope"}},'
                    ' "questions": ["How does core.py work?"]}'
                )

            async def embed(self, texts):
                return [[0.0]]

        m = manifest_for(["a.py", "core.py"])
        use_case = build(m, index_for([("a.py", "core.py")]),
                         llm_client=NarratingLLM(), use_llm_narration=True)
        guide = run(use_case)

        core = next(s for s in guide.reading_order if s.file_path == "core.py")
        self.assertEqual(core.why_it_matters, "It is the hub.")
        self.assertEqual(guide.suggested_questions, ["How does core.py work?"])
        # The model cannot add a file to the reading order.
        self.assertNotIn("invented.py", [s.file_path for s in guide.reading_order])

    def test_llm_failure_leaves_the_graph_derived_text_in_place(self):
        class FailingLLM:
            async def generate(self, prompt, **kwargs):
                raise LLMProviderError("stub", "down")

            async def embed(self, texts):
                return [[0.0]]

        m = manifest_for(["a.py", "core.py"])
        guide = run(build(m, index_for([("a.py", "core.py")]),
                          llm_client=FailingLLM(), use_llm_narration=True))
        core = next(s for s in guide.reading_order if s.file_path == "core.py")
        self.assertIn("import it", core.why_it_matters)
        self.assertTrue(guide.suggested_questions)

    def test_non_json_narration_is_ignored(self):
        class ChattyLLM:
            async def generate(self, prompt, **kwargs):
                return "Sure! Here is my analysis of the repository."

            async def embed(self, texts):
                return [[0.0]]

        m = manifest_for(["a.py", "core.py"])
        guide = run(build(m, index_for([("a.py", "core.py")]),
                          llm_client=ChattyLLM(), use_llm_narration=True))
        self.assertTrue(all(s.why_it_matters for s in guide.reading_order))


class SupportingCodeRankingTest(unittest.TestCase):
    """
    Regression cover for what real repos exposed: build manifests and
    test files both look like entry points to a naive graph reading,
    and both crowd out the modules a newcomer actually needs.
    """

    def test_build_manifests_are_not_entry_points(self):
        # A monorepo with a pyproject.toml per package.
        paths = [
            "pyproject.toml",
            "examples/a/pyproject.toml",
            "examples/b/pyproject.toml",
            "src/pkg/__init__.py",
            "src/pkg/core.py",
        ]
        edges = [("src/pkg/core.py", "src/pkg/__init__.py")]
        guide = run(build(manifest_for(paths), index_for(edges)))

        self.assertNotIn("pyproject.toml", guide.entry_points)
        self.assertNotIn("pyproject.toml", [s.file_path for s in guide.reading_order])

    def test_test_files_do_not_lead_the_reading_order(self):
        # tests/test_core.py is a textbook graph root: it imports the
        # module under test, and nothing imports it.
        edges = [
            ("tests/test_core.py", "src/pkg/core.py"),
            ("tests/test_util.py", "src/pkg/core.py"),
            ("src/pkg/api.py", "src/pkg/core.py"),
        ]
        paths = ["tests/test_core.py", "tests/test_util.py", "src/pkg/core.py", "src/pkg/api.py"]
        guide = run(build(manifest_for(paths), index_for(edges)))

        order = [s.file_path for s in guide.reading_order]
        # api.py is the real-source entry point and core.py is the hub;
        # both come before either test file, which is the point.
        self.assertEqual(order[:2], ["src/pkg/api.py", "src/pkg/core.py"])
        self.assertNotIn("tests/test_core.py", guide.entry_points)
        self.assertNotIn("tests/test_util.py", guide.entry_points)
        self.assertGreater(order.index("tests/test_core.py"), order.index("src/pkg/core.py"))

    def test_example_directories_rank_below_real_source(self):
        edges = [
            ("examples/demo/cli.py", "src/pkg/core.py"),
            ("src/pkg/api.py", "src/pkg/core.py"),
        ]
        paths = ["examples/demo/cli.py", "src/pkg/core.py", "src/pkg/api.py"]
        guide = run(build(manifest_for(paths), index_for(edges)))

        order = [s.file_path for s in guide.reading_order]
        self.assertLess(order.index("src/pkg/core.py"), order.index("examples/demo/cli.py"))

    def test_spec_and_dot_test_naming_conventions_are_recognised(self):
        edges = [
            ("src/Button.test.tsx", "src/Button.tsx"),
            ("src/Panel.spec.ts", "src/Button.tsx"),
            ("src/App.tsx", "src/Button.tsx"),
        ]
        paths = ["src/Button.test.tsx", "src/Panel.spec.ts", "src/Button.tsx", "src/App.tsx"]
        guide = run(build(manifest_for(paths), index_for(edges)))

        order = [s.file_path for s in guide.reading_order]
        # App.tsx is the real-source root; Button.tsx is the hub. The
        # .test.tsx / .spec.ts files must rank behind both.
        self.assertEqual(order[:2], ["src/App.tsx", "src/Button.tsx"])
        self.assertNotIn("src/Button.test.tsx", guide.entry_points)
        self.assertNotIn("src/Panel.spec.ts", guide.entry_points)
        for supporting in ("src/Button.test.tsx", "src/Panel.spec.ts"):
            self.assertGreater(order.index(supporting), order.index("src/Button.tsx"))

    def test_a_repo_that_is_only_examples_still_gets_a_starting_point(self):
        # The demotion must not leave an all-examples repo with nothing.
        edges = [("examples/a/main.py", "examples/a/helper.py")]
        paths = ["examples/a/main.py", "examples/a/helper.py"]
        guide = run(build(manifest_for(paths), index_for(edges)))

        self.assertTrue(guide.entry_points)
        self.assertTrue(guide.reading_order)

    def test_a_high_centrality_module_is_never_pushed_out_by_entry_points(self):
        # Many plausible entry points, but core.py is what everything imports.
        entry_paths = [f"scripts/run{i}.py" for i in range(6)]
        edges = [(p, "src/core.py") for p in entry_paths]
        guide = run(build(manifest_for(entry_paths + ["src/core.py"]), index_for(edges)))

        order = [s.file_path for s in guide.reading_order]
        self.assertIn("src/core.py", order)
        self.assertLessEqual(order.index("src/core.py"), 2)


if __name__ == "__main__":
    unittest.main()


class SingleScriptEntryPointTest(unittest.TestCase):
    """
    All three entry-point signals can miss at once. A one-file tool
    whose script is not called main.py has no recognised basename, and
    with no internal imports it has no graph root either -- so "where do
    I start" came back empty for a repository whose answer was the only
    source file in it.

    Found by running martinvigo/email2phonenumber through the real app:
    one 1076-line script, and no entry point.
    """

    def test_a_lone_script_is_the_entry_point(self):
        guide = run(build(manifest_for(["email2phonenumber.py"]), index_for([])))
        self.assertEqual(guide.entry_points, ["email2phonenumber.py"])

    def test_the_readme_is_not_offered_as_a_place_to_start_reading_code(self):
        m = manifest_for(["email2phonenumber.py", "README.md", "LICENSE"])
        guide = run(build(m, index_for([])))
        self.assertEqual(guide.entry_points, ["email2phonenumber.py"])

    def test_the_fallback_names_one_file_not_a_list(self):
        # Without a graph there is nothing to rank a list by, so five
        # equally-unsupported guesses would be worse than one.
        m = manifest_for(["alpha.py", "beta.py", "gamma.py"])
        guide = run(build(m, index_for([])))
        self.assertEqual(len(guide.entry_points), 1)

    def test_a_real_entry_point_still_wins_over_the_fallback(self):
        m = manifest_for(["app/main.py", "app/core.py"], entry_points=["app/main.py"])
        guide = run(build(m, index_for([("app/main.py", "app/core.py")])))
        self.assertEqual(guide.entry_points, ["app/main.py"])

    def test_a_graph_root_still_wins_over_the_fallback(self):
        m = manifest_for(["runner.py", "lib.py"])
        guide = run(build(m, index_for([("runner.py", "lib.py")])))
        self.assertEqual(guide.entry_points, ["runner.py"])
