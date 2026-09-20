"""
AnalyzeImpactUseCase — reverse dependency traversal.

Stub cache only: the whole point of the feature is that the blast
radius comes from parsed edges, so no LLM is involved in any
structural assertion here.
"""
import asyncio
import unittest

from app.application.use_cases.analyze_impact import (
    AnalyzeImpactUseCase,
    RepoNotIndexedError,
    TargetNotFoundError,
)
from app.domain.entities import ParsedRepoIndex, ParsedSymbol, RepoManifest
from app.domain.value_objects import RepoUrl


class StubCache:
    def __init__(self, edges=None, symbols=None, *, index=True, manifest=None):
        self._index = (
            ParsedRepoIndex(
                repo_full_name="acme/app",
                commit_sha="sha1",
                symbols=symbols or [],
                module_edges=edges or [],
            )
            if index
            else None
        )
        self._manifest = manifest

    async def get_parsed_index(self, repo_full_name, commit_sha):
        return self._index

    async def get(self, repo_full_name, commit_sha):
        return self._manifest


def run(use_case, **kwargs):
    return asyncio.run(
        use_case.execute(repo_full_name="acme/app", commit_sha="sha1", **kwargs)
    )


def make(edges=None, symbols=None, **kwargs):
    return AnalyzeImpactUseCase(
        cache=StubCache(edges, symbols, **kwargs), llm_client=None, use_llm_summary=False
    )


class ReverseImportTraversalTest(unittest.TestCase):
    def test_direct_and_indirect_are_separated_by_hop_count(self):
        edges = [
            ("api/routes.py", "core/service.py"),   # routes imports service
            ("core/service.py", "core/db.py"),      # service imports db
            ("cli/main.py", "api/routes.py"),       # main imports routes
        ]
        report = run(make(edges), file_path="core/db.py")

        self.assertEqual([n.path for n in report.direct], ["core/service.py"])
        self.assertEqual(
            sorted(n.path for n in report.indirect), ["api/routes.py", "cli/main.py"]
        )
        self.assertEqual(report.max_depth, 3)
        self.assertTrue(report.direct[0].is_direct)
        self.assertFalse(report.indirect[0].is_direct)

    def test_via_records_the_hop_a_node_was_reached_through(self):
        edges = [("api/routes.py", "core/service.py"), ("core/service.py", "core/db.py")]
        report = run(make(edges), file_path="core/db.py")
        routes = next(n for n in report.indirect if n.path == "api/routes.py")
        self.assertEqual(routes.via, ["core/service.py"])

    def test_leaf_with_no_dependents_reports_an_empty_radius(self):
        edges = [("api/routes.py", "core/service.py")]
        report = run(make(edges), file_path="api/routes.py")
        self.assertEqual(report.direct, [])
        self.assertEqual(report.indirect, [])
        self.assertIn("Nothing in the parsed dependency graph", report.summary)

    def test_cycles_terminate_and_never_revisit_a_node(self):
        edges = [("a.py", "b.py"), ("b.py", "a.py"), ("c.py", "a.py")]
        report = run(make(edges), file_path="a.py")
        paths = [n.path for n in report.direct + report.indirect]
        self.assertEqual(sorted(paths), ["b.py", "c.py"])
        self.assertEqual(len(paths), len(set(paths)))

    def test_depth_limit_is_respected(self):
        edges = [("l3.py", "l2.py"), ("l2.py", "l1.py"), ("l1.py", "l0.py")]
        use_case = AnalyzeImpactUseCase(
            cache=StubCache(edges), llm_client=None, use_llm_summary=False, max_depth=2
        )
        report = run(use_case, file_path="l0.py")
        self.assertEqual(sorted(n.path for n in report.direct + report.indirect), ["l1.py", "l2.py"])
        self.assertEqual(report.max_depth, 2)

    def test_node_cap_sets_the_truncated_flag(self):
        edges = [(f"dep{i}.py", "hub.py") for i in range(10)]
        use_case = AnalyzeImpactUseCase(
            cache=StubCache(edges), llm_client=None, use_llm_summary=False, max_nodes=4
        )
        report = run(use_case, file_path="hub.py")
        self.assertTrue(report.truncated)
        self.assertEqual(len(report.direct), 4)
        self.assertIn("node cap", report.summary)

    def test_self_edges_do_not_make_a_file_its_own_dependent(self):
        report = run(make([("a.py", "a.py"), ("b.py", "a.py")]), file_path="a.py")
        self.assertEqual([n.path for n in report.direct], ["b.py"])


class ReverseCallGraphTest(unittest.TestCase):
    def _symbols(self):
        return [
            ParsedSymbol(name="check_spam", kind="function", file_path="core/spam.py",
                         start_line=1, end_line=5),
            ParsedSymbol(name="moderate", kind="function", file_path="api/moderation.py",
                         start_line=1, end_line=9, calls=["check_spam"]),
            ParsedSymbol(name="unrelated", kind="function", file_path="vendor/other.py",
                         start_line=1, end_line=4, calls=["check_spam"]),
        ]

    def test_importers_rank_above_name_only_matches(self):
        edges = [("api/moderation.py", "core/spam.py")]
        report = run(make(edges, self._symbols()), file_path="core/spam.py", symbol="check_spam")

        self.assertEqual([n.path for n in report.callers], ["api/moderation.py", "vendor/other.py"])
        # The importer is strong evidence; the bare name match is not.
        self.assertEqual(report.callers[0].depth, 1)
        self.assertEqual(report.callers[1].depth, 2)
        self.assertEqual(report.callers[0].symbols, ["moderate"])

    def test_callers_are_only_computed_when_a_symbol_is_given(self):
        edges = [("api/moderation.py", "core/spam.py")]
        report = run(make(edges, self._symbols()), file_path="core/spam.py")
        self.assertEqual(report.callers, [])

    def test_unknown_symbol_is_rejected_rather_than_guessed(self):
        edges = [("api/moderation.py", "core/spam.py")]
        with self.assertRaises(TargetNotFoundError):
            run(make(edges, self._symbols()), file_path="core/spam.py", symbol="nope")


class EntryPointsAndErrorsTest(unittest.TestCase):
    def test_entry_points_in_the_radius_are_reported(self):
        manifest = RepoManifest(
            repo_url=RepoUrl("https://github.com/acme/app"),
            commit_sha="sha1",
            entry_points=["cli/main.py"],
        )
        edges = [("cli/main.py", "core/service.py")]
        use_case = AnalyzeImpactUseCase(
            cache=StubCache(edges, manifest=manifest), llm_client=None, use_llm_summary=False
        )
        report = run(use_case, file_path="core/service.py")
        self.assertEqual(report.entry_points_reached, ["cli/main.py"])
        self.assertIn("cli/main.py", report.summary)

    def test_missing_index_raises_rather_than_returning_an_empty_report(self):
        use_case = AnalyzeImpactUseCase(
            cache=StubCache(index=False), llm_client=None, use_llm_summary=False
        )
        with self.assertRaises(RepoNotIndexedError):
            run(use_case, file_path="a.py")

    def test_file_outside_the_graph_is_reported_as_unknown(self):
        with self.assertRaises(TargetNotFoundError):
            run(make([("a.py", "b.py")]), file_path="ghost.py")

    def test_empty_graph_is_not_a_crash(self):
        with self.assertRaises(TargetNotFoundError):
            run(make([]), file_path="a.py")


class SummaryNarrationTest(unittest.TestCase):
    def test_llm_failure_falls_back_to_the_computed_sentence(self):
        from app.application.interfaces.llm_client import LLMProviderError

        class FailingLLM:
            async def generate(self, prompt, **kwargs):
                raise LLMProviderError("stub", "down")

            async def embed(self, texts):
                return [[0.0]]

        edges = [("api/routes.py", "core/db.py")]
        use_case = AnalyzeImpactUseCase(
            cache=StubCache(edges), llm_client=FailingLLM(), use_llm_summary=True
        )
        report = run(use_case, file_path="core/db.py")
        self.assertIn("directly affects 1 file(s)", report.summary)

    def test_llm_cannot_change_the_computed_structure(self):
        class LyingLLM:
            async def generate(self, prompt, **kwargs):
                return "Everything in the repo depends on this."

            async def embed(self, texts):
                return [[0.0]]

        edges = [("api/routes.py", "core/db.py")]
        use_case = AnalyzeImpactUseCase(
            cache=StubCache(edges), llm_client=LyingLLM(), use_llm_summary=True
        )
        report = run(use_case, file_path="core/db.py")
        # The prose is the model's; the graph is not.
        self.assertEqual([n.path for n in report.direct], ["api/routes.py"])
        self.assertEqual(report.indirect, [])


if __name__ == "__main__":
    unittest.main()
