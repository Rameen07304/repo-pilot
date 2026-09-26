"""
The architecture delta between two commits.

Both sides are parses, so the interesting questions are not "did it
notice a change" but "did it avoid inventing one". A rename that reads as
a rewrite, or a guessed pairing between two same-named files, would put
a wrong claim in front of a reviewer — which is worse than a diff that
says less.
"""
import unittest

from app.application.use_cases.compare_architecture import (
    CompareArchitectureUseCase,
    SameCommitError,
)
from app.application.use_cases.export_architecture import RepoNotExportableError
from app.domain.entities import ArchitectureExport, ExportedEdge

REPO = "acme/widgets"


def side(sha, edges):
    return ArchitectureExport(
        repo_full_name=REPO,
        commit_sha=sha,
        repo_url=f"https://github.com/{REPO}",
        edges=[ExportedEdge(f, t, list(lines)) for f, t, lines in edges],
    )


class FakeExport:
    def __init__(self, sides):
        self._sides = sides

    async def execute(self, *, repo_full_name, commit_sha):
        if commit_sha not in self._sides:
            raise RepoNotExportableError(f"{commit_sha} is not ingested")
        return self._sides[commit_sha]


def compare(base_edges, head_edges):
    base, head = side("base", base_edges), side("head", head_edges)
    uc = CompareArchitectureUseCase(
        export_use_case=FakeExport({"base": base, "head": head})
    )
    import asyncio

    return asyncio.run(uc.execute(repo_full_name=REPO, base_sha="base", head_sha="head"))


class DeltaTest(unittest.TestCase):
    def test_identical_commits_report_no_structural_change(self):
        edges = [("a.py", "b.py", [1])]
        delta = compare(edges, edges)
        self.assertTrue(delta.is_unchanged)
        self.assertEqual(delta.change_count, 0)
        self.assertEqual(delta.unchanged_edge_count, 1)

    def test_an_added_dependency_is_reported_with_its_line(self):
        delta = compare([("a.py", "b.py", [1])], [("a.py", "b.py", [1]), ("a.py", "c.py", [7])])
        self.assertEqual(len(delta.added_edges), 1)
        self.assertEqual(delta.added_edges[0].to_path, "c.py")
        self.assertEqual(delta.added_edges[0].lines, [7])
        self.assertEqual(delta.removed_edges, [])

    def test_a_removed_dependency_keeps_the_line_it_used_to_be_on(self):
        # The line belongs to the base commit, where the import still
        # exists; that is what makes the removal checkable.
        delta = compare([("a.py", "b.py", [4])], [("a.py", "c.py", [1])])
        self.assertEqual([(e.to_path, e.lines) for e in delta.removed_edges], [("b.py", [4])])

    def test_a_pure_rename_is_one_move_and_no_edge_churn(self):
        # The naive answer is two node changes and two edge changes, which
        # on a refactor drowns out whatever really changed.
        delta = compare(
            [("app/main.py", "app/db.py", [1]), ("app/api.py", "app/db.py", [2])],
            [("app/main.py", "app/core/db.py", [1]), ("app/api.py", "app/core/db.py", [2])],
        )
        self.assertEqual(
            [(m.from_path, m.to_path) for m in delta.moved_nodes],
            [("app/db.py", "app/core/db.py")],
        )
        self.assertEqual(delta.added_edges, [])
        self.assertEqual(delta.removed_edges, [])
        self.assertEqual(delta.added_nodes, [])
        self.assertEqual(delta.removed_nodes, [])
        self.assertEqual(delta.unchanged_edge_count, 2)

    def test_a_real_change_survives_a_rename_in_the_same_commit(self):
        delta = compare(
            [("app/main.py", "app/db.py", [1])],
            [("app/main.py", "app/core/db.py", [1]), ("app/main.py", "app/cache.py", [2])],
        )
        self.assertEqual(len(delta.moved_nodes), 1)
        self.assertEqual([e.to_path for e in delta.added_edges], ["app/cache.py"])
        self.assertEqual(delta.removed_edges, [])

    def test_two_files_of_the_same_name_moving_at_once_are_not_guessed(self):
        # Pairing them would be a coin flip, and a wrong arrow in a review
        # is worse than an honest "one left, one arrived".
        delta = compare(
            [("app/a.py", "x/util.py", [1]), ("app/b.py", "y/util.py", [1])],
            [("app/a.py", "p/util.py", [1]), ("app/b.py", "q/util.py", [1])],
        )
        self.assertEqual(delta.moved_nodes, [])
        self.assertEqual(delta.added_nodes, ["p/util.py", "q/util.py"])
        self.assertEqual(delta.removed_nodes, ["x/util.py", "y/util.py"])

    def test_a_new_file_is_an_added_node_not_a_move(self):
        delta = compare([("a.py", "b.py", [1])], [("a.py", "b.py", [1]), ("a.py", "new.py", [2])])
        self.assertEqual(delta.added_nodes, ["new.py"])
        self.assertEqual(delta.moved_nodes, [])

    def test_rerouted_lists_files_that_still_exist(self):
        # A deleted file's dependencies changed trivially. Listing it
        # among "files to read first" wastes the reviewer's attention.
        delta = compare(
            [("gone.py", "b.py", [1]), ("kept.py", "b.py", [1])],
            [("kept.py", "c.py", [1])],
        )
        self.assertIn("kept.py", delta.rerouted_nodes)
        self.assertNotIn("gone.py", delta.rerouted_nodes)
        self.assertIn("gone.py", delta.removed_nodes)

    def test_comparing_a_commit_with_itself_is_refused(self):
        uc = CompareArchitectureUseCase(export_use_case=FakeExport({}))
        import asyncio

        with self.assertRaises(SameCommitError):
            asyncio.run(uc.execute(repo_full_name=REPO, base_sha="x", head_sha="x"))

    def test_a_missing_side_says_which_side_is_missing(self):
        import asyncio

        uc = CompareArchitectureUseCase(
            export_use_case=FakeExport({"head": side("head", [])})
        )
        with self.assertRaises(RepoNotExportableError) as caught:
            asyncio.run(uc.execute(repo_full_name=REPO, base_sha="base", head_sha="head"))
        self.assertIn("base", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
