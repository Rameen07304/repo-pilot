"""
The architecture export: a diagram that can be checked without the tool.

Two things make it worth more than a picture, and both are tested here
rather than assumed: every edge carries the line it was resolved from,
and the document knows what it could not see. A gap the export fails to
report is worse than a gap it has, because a reader has no way to notice
it.
"""
import unittest

from app.application.use_cases.export_architecture import (
    ExportArchitectureUseCase,
    RepoNotExportableError,
)
from app.domain.entities import FileNode, ParsedRepoIndex, RepoManifest
from app.domain.value_objects import RepoUrl

REPO = "acme/widgets"
SHA = "abc123def456"


class FakeCache:
    def __init__(self, manifest=None, index=None):
        self._manifest = manifest
        self._index = index

    async def get(self, repo_full_name, commit_sha):
        return self._manifest

    async def get_parsed_index(self, repo_full_name, commit_sha):
        return self._index


class FakeParser:
    """Reads dependencies out of .py and .html, like the real one."""

    def reads_dependencies_from(self, file_path):
        return file_path.endswith((".py", ".html"))


def manifest(files=("app/main.py", "app/db.py"), **kwargs):
    defaults = dict(
        repo_url=RepoUrl(f"https://github.com/{REPO}"),
        commit_sha=SHA,
        files=[FileNode(path=p, size_bytes=10) for p in files],
        detected_stack=["Python"],
    )
    defaults.update(kwargs)
    return RepoManifest(**defaults)


def index(edges=(("app/main.py", "app/db.py"),), provenance=(("app/main.py", "app/db.py", 3),)):
    return ParsedRepoIndex(
        repo_full_name=REPO,
        commit_sha=SHA,
        module_edges=list(edges),
        edge_provenance=list(provenance),
    )


def use_case(manifest_=None, index_=None, **kwargs):
    return ExportArchitectureUseCase(
        cache=FakeCache(manifest_, index_), parser=FakeParser(), **kwargs
    )


class ExportArchitectureTest(unittest.IsolatedAsyncioTestCase):
    async def test_every_edge_carries_the_line_it_came_from(self):
        export = await use_case(manifest(), index()).execute(
            repo_full_name=REPO, commit_sha=SHA
        )
        self.assertEqual(len(export.edges), 1)
        self.assertEqual(export.edges[0].lines, [3])
        self.assertTrue(export.edges[0].is_evidenced)
        self.assertEqual(export.evidenced_edge_count, 1)

    async def test_one_file_importing_another_twice_is_one_edge_with_two_lines(self):
        # A second import of the same module is more evidence for one
        # dependency, not a second dependency.
        export = await use_case(
            manifest(),
            index(
                edges=[("app/main.py", "app/db.py"), ("app/main.py", "app/db.py")],
                provenance=[("app/main.py", "app/db.py", 9), ("app/main.py", "app/db.py", 2)],
            ),
        ).execute(repo_full_name=REPO, commit_sha=SHA)
        self.assertEqual(len(export.edges), 1)
        self.assertEqual(export.edges[0].lines, [2, 9])  # ascending

    async def test_an_edge_with_no_recorded_line_is_kept_and_marked_unevidenced(self):
        # Dropping it would make the diagram disagree with the graph the
        # rest of the product draws. Saying nothing about it would imply
        # a line exists.
        export = await use_case(manifest(), index(provenance=[])).execute(
            repo_full_name=REPO, commit_sha=SHA
        )
        self.assertEqual(len(export.edges), 1)
        self.assertEqual(export.edges[0].lines, [])
        self.assertFalse(export.edges[0].is_evidenced)
        self.assertEqual(export.evidenced_edge_count, 0)

    async def test_self_imports_are_not_drawn(self):
        export = await use_case(
            manifest(), index(edges=[("app/main.py", "app/main.py")], provenance=[])
        ).execute(repo_full_name=REPO, commit_sha=SHA)
        self.assertEqual(export.edges, [])

    async def test_missing_manifest_and_missing_index_both_refuse(self):
        with self.assertRaises(RepoNotExportableError):
            await use_case(None, index()).execute(repo_full_name=REPO, commit_sha=SHA)
        with self.assertRaises(RepoNotExportableError):
            await use_case(manifest(), None).execute(repo_full_name=REPO, commit_sha=SHA)


class CoverageHonestyTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_complete_export_says_so(self):
        export = await use_case(manifest(), index()).execute(
            repo_full_name=REPO, commit_sha=SHA
        )
        self.assertTrue(export.coverage.is_complete)

    async def test_files_with_no_import_parser_are_counted_by_extension(self):
        files = ["app/main.py", "app/db.py", "a.kt", "b.kt", "c.swift", "README.md", "logo.png"]
        export = await use_case(manifest(files), index()).execute(
            repo_full_name=REPO, commit_sha=SHA
        )
        # Ranked by count, and documentation and images are not blind spots.
        self.assertEqual(export.coverage.unparsed_extensions, {".kt": 2, ".swift": 1})
        self.assertEqual(export.coverage.unparsed_file_count, 3)
        self.assertFalse(export.coverage.is_complete)

    async def test_html_is_not_reported_as_a_blind_spot(self):
        # The graph reads asset links out of HTML, so claiming it cannot
        # would be a false statement in the one section that exists to be
        # trusted.
        export = await use_case(manifest(["app/main.py", "page.html"]), index()).execute(
            repo_full_name=REPO, commit_sha=SHA
        )
        self.assertEqual(export.coverage.unparsed_extensions, {})

    async def test_unfetched_files_are_reported(self):
        export = await use_case(manifest(skipped_file_count=4), index()).execute(
            repo_full_name=REPO, commit_sha=SHA
        )
        self.assertEqual(export.coverage.files_skipped, 4)
        self.assertFalse(export.coverage.is_complete)

    async def test_a_grammar_that_did_not_load_is_reported(self):
        export = await use_case(manifest(degraded_languages=["dart"]), index()).execute(
            repo_full_name=REPO, commit_sha=SHA
        )
        self.assertEqual(export.coverage.degraded_languages, ["dart"])
        self.assertFalse(export.coverage.is_complete)

    async def test_capping_keeps_the_most_connected_and_admits_what_it_dropped(self):
        # A hub plus three leaves, capped to two nodes: the hub must
        # survive, and the document must not claim to be whole.
        edges = [("hub.py", f"leaf{i}.py") for i in range(3)]
        provenance = [("hub.py", f"leaf{i}.py", i + 1) for i in range(3)]
        export = await use_case(
            manifest(), index(edges=edges, provenance=provenance), max_nodes=2
        ).execute(repo_full_name=REPO, commit_sha=SHA)

        drawn = {p for e in export.edges for p in (e.from_path, e.to_path)}
        self.assertIn("hub.py", drawn)
        self.assertEqual(export.coverage.nodes_total, 4)
        self.assertEqual(export.coverage.nodes_drawn, 2)
        self.assertEqual(export.coverage.edges_total, 3)
        self.assertEqual(export.coverage.edges_drawn, 1)
        self.assertFalse(export.coverage.is_complete)

    async def test_the_edge_cap_is_also_admitted(self):
        edges = [(f"a{i}.py", f"b{i}.py") for i in range(5)]
        export = await use_case(
            manifest(), index(edges=edges, provenance=[]), max_edges=2
        ).execute(repo_full_name=REPO, commit_sha=SHA)
        self.assertEqual(export.coverage.edges_drawn, 2)
        self.assertEqual(export.coverage.edges_total, 5)
        self.assertFalse(export.coverage.is_complete)


if __name__ == "__main__":
    unittest.main()
