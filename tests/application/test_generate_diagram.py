"""
GenerateDiagramUseCase — capping and folder aggregation for big repos.

Uses a stub cache so no SQLite/LLM is involved; the use case's inputs
are just (from, to) module edges.
"""
import asyncio
import re
import unittest

from app.application.use_cases.generate_diagram import GenerateDiagramUseCase
from app.domain.entities import ParsedRepoIndex


class StubCache:
    def __init__(self, edges):
        self._index = ParsedRepoIndex(
            repo_full_name="acme/big", commit_sha="abc", symbols=[], module_edges=edges
        )

    async def get_parsed_index(self, repo_full_name, commit_sha):
        return self._index


def run(use_case, **kwargs):
    return asyncio.run(use_case.execute(repo_full_name="acme/big", commit_sha="abc", **kwargs))


def node_labels(mermaid):
    return re.findall(r'N\d+\["([^"]+)"\]', mermaid)


class SmallRepoStaysFileLevelTest(unittest.TestCase):
    def test_file_level_edges_unchanged(self):
        edges = [
            ("app/main.py", "app/routes/ask.py"),
            ("app/routes/ask.py", "app/core/llm.py"),
            ("app/main.py", "app/routes/ask.py"),  # duplicate
            ("app/core/llm.py", "app/core/llm.py"),  # self-loop
        ]
        spec = run(GenerateDiagramUseCase(cache=StubCache(edges), max_nodes=40, max_edges=80))
        self.assertEqual(spec.granularity, "file")
        self.assertEqual(spec.total_files, 3)
        self.assertEqual(spec.mermaid_syntax.count("-->"), 2)
        self.assertNotIn("|", spec.mermaid_syntax)  # no weights at file level

    def test_grouped_nodes_show_basename_inside_subgraph(self):
        edges = [("app/routes/ask.py", "app/routes/diagram.py"), ("app/routes/ask.py", "app/main.py")]
        spec = run(GenerateDiagramUseCase(cache=StubCache(edges)), group_by_folder=True)
        self.assertIn('subgraph cluster_app_routes["app/routes"]', spec.mermaid_syntax)
        self.assertIn('"ask.py"', spec.mermaid_syntax)
        self.assertIn('"app/main.py"', spec.mermaid_syntax)  # ungrouped single file keeps its full path


class BigRepoAggregationTest(unittest.TestCase):
    def _big_edges(self):
        # 3 top-level areas x 4 sub-packages x 5 files = 60 files, well over max_nodes=20
        edges = []
        areas = ["api", "core", "infra"]
        for area in areas:
            for pkg in range(4):
                for f in range(5):
                    src = f"src/{area}/pkg{pkg}/file{f}.py"
                    edges.append((src, f"src/{area}/pkg{pkg}/file{(f + 1) % 5}.py"))  # intra-folder
                    edges.append((src, f"src/core/pkg{pkg}/file0.py"))  # everything depends on core
            edges.append((f"src/{area}/pkg0/file0.py", f"src/{areas[(areas.index(area) + 1) % 3]}/pkg1/file1.py"))
        return edges

    def test_aggregates_to_deepest_folder_depth_that_fits(self):
        spec = run(GenerateDiagramUseCase(cache=StubCache(self._big_edges()), max_nodes=20, max_edges=80))
        self.assertEqual(spec.granularity, "folder")
        self.assertEqual(spec.total_files, 60)
        # 12 sub-packages fit in 20 nodes, so depth 3 (src/area/pkgN) is chosen
        self.assertTrue(all(m.count("/") == 2 for m in spec.modules), spec.modules)
        self.assertLessEqual(len(spec.modules), 20)
        # weighted edges, and directory labels are marked with a trailing slash
        self.assertRegex(spec.mermaid_syntax, r'N\d+ -->\|"\d+"\| N\d+')
        self.assertTrue(all(label.endswith("/") for label in node_labels(spec.mermaid_syntax)))

    def test_intra_folder_imports_vanish_and_parallel_ones_merge(self):
        spec = run(GenerateDiagramUseCase(cache=StubCache(self._big_edges()), max_nodes=20, max_edges=80))
        # each of the 5 files in api/pkg0 imports core/pkg0/file0 -> one edge weighted 5
        self.assertIn('-->|"5"|', spec.mermaid_syntax)

    def test_falls_back_to_shallower_depth_when_needed(self):
        spec = run(GenerateDiagramUseCase(cache=StubCache(self._big_edges()), max_nodes=5, max_edges=80))
        self.assertEqual(spec.granularity, "folder")
        self.assertEqual(sorted(spec.modules), ["src/api", "src/core", "src/infra"])

    def test_node_cap_keeps_most_connected_not_alphabetical(self):
        # 'zzz_hub.py' is imported by everyone and sorts last alphabetically;
        # the old first-N-in-order cap would have dropped it.
        edges = [(f"src/m{i}.py", "src/zzz_hub.py") for i in range(10)]
        edges.append(("src/m0.py", "src/aaa_leaf.py"))
        spec = run(GenerateDiagramUseCase(cache=StubCache(edges), max_nodes=4, max_edges=80))
        self.assertEqual(spec.granularity, "file")  # single folder -> aggregation would be empty, so ranked files
        self.assertEqual(len(spec.modules), 4)
        self.assertIn("src/zzz_hub.py", spec.modules)  # degree 10
        self.assertIn("src/m0.py", spec.modules)  # degree 2, the only file importing two things

    def test_flat_repo_over_cap_ranks_instead_of_aggregating(self):
        edges = [(f"m{i}.py", "hub.py") for i in range(50)]
        spec = run(GenerateDiagramUseCase(cache=StubCache(edges), max_nodes=10, max_edges=80))
        self.assertEqual(spec.granularity, "file")
        self.assertIn("hub.py", spec.modules)
        self.assertLessEqual(len(spec.modules), 10)


if __name__ == "__main__":
    unittest.main()
