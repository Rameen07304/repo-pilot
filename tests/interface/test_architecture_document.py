"""
The exported document itself: geometry, then prose.

The export's whole claim is that it survives away from Repo Pilot — no
network, no scripts required to read it, and no statement in it that the
repository does not support. These tests hold that claim to its word.
"""
import re
import unittest

from app.domain.entities import ArchitectureCoverage, ArchitectureExport, ExportedEdge
from app.interface.architecture_html import render_architecture_html
from app.interface.architecture_svg import build_layout, render_architecture_svg

SHA = "abc123def4567890"


def export(edges=None, **kwargs):
    defaults = dict(
        repo_full_name="acme/widgets",
        commit_sha=SHA,
        repo_url="https://github.com/acme/widgets",
        detected_stack=["Python"],
        edges=edges
        if edges is not None
        else [
            ExportedEdge("app/main.py", "app/routes.py", [3]),
            ExportedEdge("app/routes.py", "app/db.py", [1, 9]),
        ],
        coverage=ArchitectureCoverage(
            files_in_repo=3, nodes_drawn=3, nodes_total=3, edges_drawn=2, edges_total=2
        ),
    )
    defaults.update(kwargs)
    return ArchitectureExport(**defaults)


class LayoutTest(unittest.TestCase):
    def test_a_dependency_is_placed_below_what_it_depends_on(self):
        layout = build_layout(export())
        depth = {n.path: n.layer for n in layout.nodes}
        self.assertLess(depth["app/main.py"], depth["app/routes.py"])
        self.assertLess(depth["app/routes.py"], depth["app/db.py"])

    def test_a_cycle_is_drawn_rather_than_dropped(self):
        # A circular import is a real and interesting property of a
        # repository. Layering needs an acyclic graph, but the edge that
        # made it cyclic still belongs in the picture.
        cyclic = export(
            edges=[
                ExportedEdge("a.py", "b.py", [1]),
                ExportedEdge("b.py", "a.py", [2]),
            ]
        )
        layout = build_layout(cyclic)
        self.assertEqual(len(layout.edges), 2)
        self.assertEqual(len(layout.cycle_edges), 1)

    def test_a_deep_chain_does_not_exhaust_the_stack(self):
        # Cycle detection is iterative on purpose: a long dependency
        # chain in a real repository would blow a recursive one.
        chain = [ExportedEdge(f"m{i}.py", f"m{i + 1}.py", [1]) for i in range(2000)]
        layout = build_layout(export(edges=chain))
        self.assertEqual(len(layout.nodes), 2001)

    def test_a_wide_layer_wraps_instead_of_drawing_one_endless_row(self):
        wide = [ExportedEdge("hub.py", f"leaf{i:02d}.py", [i + 1]) for i in range(40)]
        layout = build_layout(export(edges=wide))
        rows = {n.y for n in layout.nodes}
        self.assertGreater(len(rows), 2, "40 leaves should not sit on one row")
        self.assertLess(layout.width, 1200)

    def test_an_empty_graph_produces_a_document_not_a_crash(self):
        layout = build_layout(export(edges=[]))
        self.assertEqual(layout.nodes, [])
        self.assertIn("No internal dependencies", render_architecture_svg(export(edges=[])))


class SvgTest(unittest.TestCase):
    def test_the_same_export_always_renders_identically(self):
        # Two exports of one commit must be diffable, which only holds if
        # nothing in the layout depends on set or dict iteration luck.
        self.assertEqual(render_architecture_svg(export()), render_architecture_svg(export()))

    def test_every_edge_carries_its_source_lines(self):
        svg = render_architecture_svg(export())
        self.assertIn('data-from="app/routes.py" data-to="app/db.py" data-lines="1,9"', svg)

    def test_a_thin_curve_gets_a_clickable_twin(self):
        svg = render_architecture_svg(export())
        self.assertEqual(svg.count('class="rp-edge-hit"'), 2)

    def test_a_malicious_file_name_cannot_break_out_of_the_markup(self):
        nasty = '<script>alert(1)</script>".py'
        svg = render_architecture_svg(
            export(edges=[ExportedEdge(nasty, "app/db.py", [1])])
        )
        self.assertNotIn("<script>", svg)
        self.assertIn("&lt;script&gt;", svg)


class HtmlDocumentTest(unittest.TestCase):
    def test_the_document_asks_the_network_for_nothing(self):
        html = render_architecture_html(export())
        self.assertNotIn("<script src", html)
        self.assertNotIn("<link rel=\"stylesheet\"", html)
        for host in ("cdnjs", "jsdelivr", "unpkg", "fonts.googleapis", "mermaid"):
            self.assertNotIn(host, html)

    def test_every_edge_is_readable_without_running_a_script(self):
        # The detail panel is an enhancement. Turning scripts off must
        # cost convenience, never evidence.
        html = render_architecture_html(export())
        table = html[html.index("<h2>Evidence</h2>"):]
        self.assertIn("app/main.py", table)
        self.assertIn("app/routes.py", table)
        self.assertIn("app/db.py", table)

    def test_a_line_links_to_that_line_at_that_commit(self):
        html = render_architecture_html(export())
        self.assertIn(f"https://github.com/acme/widgets/blob/{SHA}/app/routes.py#L9", html)

    def test_an_edge_with_no_line_says_so_rather_than_looking_cited(self):
        html = render_architecture_html(
            export(edges=[ExportedEdge("page.html", "app.js", [])])
        )
        self.assertIn("not line-attributable", html)

    def test_gaps_are_stated_before_the_diagram(self):
        html = render_architecture_html(
            export(
                coverage=ArchitectureCoverage(
                    files_in_repo=9,
                    files_skipped=2,
                    unparsed_extensions={".kt": 3},
                    degraded_languages=["dart"],
                    nodes_drawn=3,
                    nodes_total=8,
                    edges_drawn=2,
                    edges_total=7,
                )
            )
        )
        self.assertLess(html.index("does not show"), html.index("<h2>Dependencies</h2>"))
        self.assertIn("2 file(s) were never fetched", html)
        self.assertIn("<code>.kt</code> (3)", html)
        self.assertIn("Grammar unavailable for dart", html)
        self.assertIn("5 of 8 files were left out", html)
        self.assertIn("5 of 7 dependencies are", html)

    def test_completeness_is_claimed_explicitly_not_by_silence(self):
        html = render_architecture_html(export())
        self.assertIn("Nothing was dropped", html)
        self.assertNotIn("does not show", html)

    def test_the_commit_is_named_so_the_document_cannot_drift_quietly(self):
        html = render_architecture_html(export())
        self.assertIn("abc123d", html)
        self.assertIn("Regenerate it rather than editing it", html)

    def test_it_states_that_no_model_chose_the_edges(self):
        self.assertIn("No language model took part", render_architecture_html(export()))

    def test_a_repo_with_no_dependencies_still_produces_a_readable_document(self):
        html = render_architecture_html(
            export(edges=[], coverage=ArchitectureCoverage(files_in_repo=4))
        )
        self.assertIn("No internal dependencies were resolved", html)
        self.assertIn("<h2>Evidence</h2>", html)

    def test_the_markup_is_balanced_enough_to_parse(self):
        html = render_architecture_html(export())
        for tag in ("table", "tbody", "main", "footer"):
            self.assertEqual(
                len(re.findall(rf"<{tag}[ >]", html)),
                html.count(f"</{tag}>"),
                f"unbalanced <{tag}>",
            )


if __name__ == "__main__":
    unittest.main()


class DeltaDocumentTest(unittest.TestCase):
    """
    The delta page. Same rules as the export — no network, readable with
    scripts off — plus one it owns: a dependency that was removed has to
    be visible, which means drawing something that no longer exists and
    labelling it clearly enough that nobody mistakes it for current.
    """

    @staticmethod
    def delta(**kwargs):
        import asyncio

        from app.application.use_cases.compare_architecture import (
            CompareArchitectureUseCase,
        )

        base_edges = kwargs.pop("base_edges", [("app/main.py", "app/db.py", [1])])
        head_edges = kwargs.pop("head_edges", [("app/main.py", "app/cache.py", [2])])

        def side(sha, edges):
            return export(
                commit_sha=sha,
                edges=[ExportedEdge(f, t, list(ls)) for f, t, ls in edges],
            )

        class Fake:
            async def execute(self, *, repo_full_name, commit_sha):
                return {"base1234567": side("base1234567", base_edges),
                        "head1234567": side("head1234567", head_edges)}[commit_sha]

        return asyncio.run(
            CompareArchitectureUseCase(export_use_case=Fake()).execute(
                repo_full_name="acme/widgets", base_sha="base1234567", head_sha="head1234567"
            )
        )

    def render(self, **kwargs):
        from app.interface.architecture_delta_html import render_architecture_delta_html

        return render_architecture_delta_html(self.delta(**kwargs))

    def test_it_asks_the_network_for_nothing(self):
        html = self.render()
        self.assertNotIn("<script src", html)
        for host in ("cdnjs", "jsdelivr", "unpkg", "fonts.googleapis", "mermaid"):
            self.assertNotIn(host, html)

    def test_both_commits_are_named(self):
        html = self.render()
        self.assertIn("base123", html)
        self.assertIn("head123", html)

    def test_a_removed_dependency_is_drawn_and_labelled(self):
        html = self.render()
        self.assertIn('rp-edge-removed', html)
        self.assertIn("do not exist at the head commit", html)

    def test_an_added_line_links_to_head_and_a_removed_line_to_base(self):
        html = self.render()
        self.assertIn("/blob/head1234567/app/main.py#L2", html)
        self.assertIn("/blob/base1234567/app/main.py#L1", html)

    def test_an_unchanged_pair_says_so_plainly(self):
        same = [("app/main.py", "app/db.py", [1])]
        html = self.render(base_edges=same, head_edges=same)
        self.assertIn("Nothing structural changed", html)
        self.assertIn("compares structure, not", html)

    def test_a_rename_is_explained_rather_than_shown_as_churn(self):
        html = self.render(
            base_edges=[("app/main.py", "app/db.py", [1])],
            head_edges=[("app/main.py", "app/core/db.py", [1])],
        )
        self.assertIn("Files that moved", html)
        self.assertIn("app/core/db.py", html)
        self.assertIn("No dependency was added or removed.", html)

    def test_it_states_that_neither_side_came_from_a_model(self):
        self.assertIn("No language model took part in either one", self.render())


class ToolbarTest(unittest.TestCase):
    """
    The filter and the image exports.

    They exist because Archify's own stated weakness is that a large
    repository draws a diagram too dense to read, and because a diagram
    people cannot paste into a review does not get looked at. They are
    also the only part of either document that does nothing without
    JavaScript, so the rule is that they must not be visible until the
    script that drives them has run.
    """

    def both_documents(self):
        from app.interface.architecture_delta_html import render_architecture_delta_html

        yield "export", render_architecture_html(export())
        yield "delta", render_architecture_delta_html(DeltaDocumentTest.delta())

    def test_controls_ship_hidden_so_nothing_inert_is_offered(self):
        for name, html in self.both_documents():
            with self.subTest(document=name):
                for control in ('id="rp-filter"', 'id="rp-png"', 'id="rp-card"'):
                    self.assertIn(control, html)
                # Each control carries both the hidden attribute and the
                # marker the script uses to reveal it.
                self.assertEqual(html.count("data-needs-js hidden"), 3)

    def test_both_documents_carry_the_script(self):
        for name, html in self.both_documents():
            with self.subTest(document=name):
                self.assertIn("<script>", html)
                self.assertIn("rp-edge-hit", html)

    def test_the_delta_detail_panel_is_actually_wired(self):
        # It had a panel and no script, so clicking an arrow did nothing.
        from app.interface.architecture_delta_html import render_architecture_delta_html

        html = render_architecture_delta_html(DeltaDocumentTest.delta())
        self.assertIn('id="detail"', html)
        self.assertLess(html.index('id="detail"'), html.index("<script>"))

    def test_image_export_stays_offline(self):
        # Rasterising must not reach for a font or a library: the whole
        # point is a document that works with no network.
        for name, html in self.both_documents():
            with self.subTest(document=name):
                for host in ("cdnjs", "jsdelivr", "unpkg", "fonts.googleapis"):
                    self.assertNotIn(host, html)
