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
