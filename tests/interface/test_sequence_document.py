"""
The exported call-sequence page.

Same contract as the architecture export — no network, readable with
scripts off, hostile repo content cannot inject — plus the one that
matters most for this document: the two kinds of call it did not draw
have to be on the page. A sequence diagram that silently drops the calls
it could not place looks far more complete than it is.
"""
import unittest

from app.domain.entities import CallSequence, CallStep
from app.interface.sequence_html import render_sequence_html
from app.interface.sequence_svg import render_sequence_svg

REPO, SHA = "acme/widgets", "abc123def456"


def step(**kwargs):
    defaults = dict(
        depth=0, from_file="api/views.py", from_symbol="handler",
        to_file="core/db.py", to_symbol="save", line=12,
    )
    defaults.update(kwargs)
    return CallStep(**defaults)


def sequence(**kwargs):
    defaults = dict(
        repo_full_name=REPO, commit_sha=SHA,
        repo_url=f"https://github.com/{REPO}",
        entry_file="api/views.py", entry_symbol="handler",
        steps=[step(), step(depth=1, from_file="core/db.py", from_symbol="save",
                            to_file="core/db.py", to_symbol="connect", line=6)],
        max_depth=6,
    )
    defaults.update(kwargs)
    return CallSequence(**defaults)


class SequenceSvgTest(unittest.TestCase):
    def test_the_same_sequence_always_renders_identically(self):
        self.assertEqual(render_sequence_svg(sequence()), render_sequence_svg(sequence()))

    def test_each_participant_gets_one_lifeline(self):
        svg = render_sequence_svg(sequence())
        self.assertEqual(svg.count('stroke-dasharray="3 4"'), 2)  # views.py, db.py

    def test_every_message_carries_its_call_site(self):
        svg = render_sequence_svg(sequence())
        self.assertIn('data-caller="handler"', svg)
        self.assertIn('data-symbol="save"', svg)
        self.assertIn('data-line="12"', svg)

    def test_a_same_file_call_still_draws_a_message(self):
        # connect() is called from within core/db.py, so its arrow has
        # nowhere to go; it loops instead of collapsing to a dot.
        svg = render_sequence_svg(sequence())
        self.assertIn('data-symbol="connect"', svg)

    def test_a_self_call_label_is_not_clipped_off_the_canvas(self):
        # The loop and its label are drawn to the RIGHT of the lifeline,
        # so one on the last participant used to run past the edge.
        import re

        seq = sequence(steps=[step(
            from_file="core/db.py", from_symbol="a",
            to_file="core/db.py", to_symbol="an_extremely_long_function_name", line=3,
        )])
        svg = render_sequence_svg(seq)
        width = int(re.search(r'width="(\d+)"', svg).group(1))
        self.assertGreater(width, 400)

    def test_an_empty_sequence_says_so_rather_than_drawing_nothing(self):
        svg = render_sequence_svg(sequence(steps=[]))
        self.assertIn("Nothing this function calls", svg)

    def test_a_hostile_symbol_name_is_escaped(self):
        svg = render_sequence_svg(sequence(steps=[step(to_symbol='<script>x</script>')]))
        self.assertNotIn("<script>x", svg)
        self.assertIn("&lt;script&gt;", svg)


class SequenceDocumentTest(unittest.TestCase):
    def test_it_asks_the_network_for_nothing(self):
        html = render_sequence_html(sequence())
        self.assertNotIn("<script src", html)
        for host in ("cdnjs", "jsdelivr", "unpkg", "fonts.googleapis", "mermaid"):
            self.assertNotIn(host, html)

    def test_every_step_is_readable_without_a_script(self):
        html = render_sequence_html(sequence())
        table = html[html.index("<h2>Evidence</h2>"):]
        self.assertIn("handler()", table)
        self.assertIn("save()", table)
        self.assertIn("api/views.py:12", table)

    def test_a_call_site_links_to_that_line_at_that_commit(self):
        html = render_sequence_html(sequence())
        self.assertIn(f"/blob/{SHA}/api/views.py#L12", html)

    def test_calls_that_left_the_repo_are_named_and_counted(self):
        html = render_sequence_html(sequence(unresolved_calls={"print": 3, "len": 1}))
        self.assertIn("4 call(s) left this repository", html)
        self.assertIn("<code>print()</code> (3)", html)

    def test_calls_that_could_not_be_placed_are_a_separate_admission(self):
        # Different from external: this is a call we could not resolve,
        # not one that simply is not ours.
        html = render_sequence_html(sequence(ambiguous_calls={"save": 2}))
        self.assertIn("2 call(s) could not be placed", html)
        self.assertIn("no arrow was drawn rather than a possibly wrong one", html)

    def test_a_truncated_trace_says_where_it_stopped(self):
        html = render_sequence_html(sequence(truncated=True, max_depth=4))
        self.assertIn("stopped early", html)
        self.assertIn("4 levels", html)

    def test_limits_are_stated_before_the_diagram(self):
        html = render_sequence_html(sequence(unresolved_calls={"print": 1}))
        self.assertLess(html.index("does not show"), html.index("<h2>Sequence</h2>"))

    def test_a_clean_trace_makes_no_such_claim(self):
        self.assertNotIn("does not show", render_sequence_html(sequence()))

    def test_an_entry_calling_nothing_internal_is_explained_not_blank(self):
        html = render_sequence_html(
            sequence(steps=[], unresolved_calls={"render": 4})
        )
        self.assertIn("calls nothing that lives in this repository", html)
        self.assertIn("4 call(s) left this repository", html)

    def test_recursion_is_explained_when_present(self):
        html = render_sequence_html(sequence(steps=[step(is_recursive=True)]))
        self.assertIn("already on the stack", html)

    def test_it_states_that_no_model_chose_the_steps(self):
        html = render_sequence_html(sequence())
        self.assertIn("No language model chose the steps", html)
        self.assertIn("matched by name", html)

    def test_it_admits_a_branch_that_never_runs_is_still_a_call(self):
        # The honest limit of static analysis, and the thing a reader
        # would otherwise assume the opposite of.
        self.assertIn("a branch that never executes is still a call",
                      render_sequence_html(sequence()))

    def test_a_hostile_path_cannot_inject(self):
        html = render_sequence_html(
            sequence(steps=[step(from_file='<img src=x onerror="alert(1)">.py')])
        )
        self.assertNotIn("<img src=x", html)
        self.assertNotIn('onerror="alert(1)"', html)

    def test_the_inlined_script_builds_no_markup_from_strings(self):
        self.assertNotIn("innerHTML", render_sequence_html(sequence()))


if __name__ == "__main__":
    unittest.main()
