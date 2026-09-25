"""
The handbook: one document a team commits next to the code. It must say
the same things the screens say, stay honest about what it does not know,
and never claim a reading order it was not given.
"""
import unittest
from datetime import datetime, timezone

from app.domain.entities import (
    CodeHealthFinding,
    CodeHealthReport,
    Handbook,
    OnboardingGuide,
    ReadingStep,
)
from app.interface.handbook_markdown import MAX_DETAILED_STEPS, render_handbook_markdown

WHEN = datetime(2026, 3, 1, 9, 30, tzinfo=timezone.utc)


def step(path="app/main.py", **kwargs):
    defaults = dict(
        file_path=path,
        why_it_matters="Everything else is wired together here.",
        what_to_understand="How the routes reach the use cases.",
        depends_on=["app.config"],
        depended_on_by=["app.cli"],
        key_symbols=["create_app"],
        is_entry_point=False,
    )
    defaults.update(kwargs)
    return ReadingStep(**defaults)


def guide(**kwargs):
    defaults = dict(
        repo_full_name="acme/widgets",
        commit_sha="abc123def456",
        overview="A small service that scores widgets.",
        entry_points=["app/main.py"],
        reading_order=[step()],
        suggested_questions=["Where is the session token checked?"],
        graph_available=True,
    )
    defaults.update(kwargs)
    return OnboardingGuide(**defaults)


def health(**kwargs):
    defaults = dict(
        repo_full_name="acme/widgets", commit_sha="abc123def456", score=88, grade="B",
        source_file_count=18, test_file_count=2, symbol_count=109, findings=[], deductions={},
    )
    defaults.update(kwargs)
    return CodeHealthReport(**defaults)


def handbook(**kwargs):
    defaults = dict(
        repo_full_name="acme/widgets",
        commit_sha="abc123def456",
        guide=guide(),
        detected_stack=["Python", "FastAPI"],
        file_count=18,
        skipped_file_count=0,
        health=health(),
    )
    defaults.update(kwargs)
    return Handbook(**defaults)


class HandbookMarkdownTest(unittest.TestCase):
    def test_header_carries_provenance(self):
        md = render_handbook_markdown(handbook(), generated_at=WHEN)
        self.assertIn("# acme/widgets — a working handbook", md)
        self.assertIn("`abc123def456`", md)
        self.assertIn("2026-03-01 09:30 UTC by Repo Pilot", md)
        self.assertIn("Python, FastAPI", md)

    def test_reading_order_is_numbered_and_explained(self):
        md = render_handbook_markdown(handbook(), generated_at=WHEN)
        self.assertIn("## Reading order", md)
        self.assertIn("1. `app/main.py`", md)
        self.assertIn("Everything else is wired together here.", md)
        self.assertIn("How the routes reach the use cases.", md)

    def test_entry_points_are_marked_in_the_order(self):
        h = handbook(guide=guide(reading_order=[step(is_entry_point=True)]))
        self.assertIn("*(entry point)*", render_handbook_markdown(h, generated_at=WHEN))

    def test_long_reading_orders_are_truncated_not_dumped(self):
        steps = [step(path=f"app/mod{i}.py") for i in range(MAX_DETAILED_STEPS + 5)]
        md = render_handbook_markdown(handbook(guide=guide(reading_order=steps)), generated_at=WHEN)
        # The last one past the cap is listed, but without its prose.
        self.assertIn("### Also worth reading (5)", md)
        self.assertIn(f"- `app/mod{MAX_DETAILED_STEPS + 4}.py`", md)
        self.assertNotIn(f"### {MAX_DETAILED_STEPS + 1}.", md)

    def test_incomplete_ingest_is_stated_up_front(self):
        md = render_handbook_markdown(handbook(skipped_file_count=3), generated_at=WHEN)
        self.assertIn("This handbook is incomplete", md)
        self.assertIn("3 file(s) could not", md)

    def test_complete_ingest_makes_no_such_claim(self):
        self.assertNotIn("incomplete", render_handbook_markdown(handbook(), generated_at=WHEN))

    def test_missing_graph_warns_the_order_is_not_ranked(self):
        md = render_handbook_markdown(handbook(guide=guide(graph_available=False)), generated_at=WHEN)
        self.assertIn("not ranked", md)

    def test_health_section_matches_the_report_vocabulary(self):
        h = handbook(health=health(grade="D", score=59, findings=[
            CodeHealthFinding(
                check="circular_import", severity="high", title="Two modules import each other",
                detail="app.a and app.b form a cycle.", files=["app/a.py", "app/b.py"], metric=2,
            )
        ]))
        md = render_handbook_markdown(h, generated_at=WHEN)
        self.assertIn("**Grade D — 59/100.**", md)
        self.assertIn("Worth fixing (1)", md)
        self.assertIn("Circular imports", md)

    def test_missing_health_says_so_rather_than_implying_a_clean_bill(self):
        md = render_handbook_markdown(handbook(health=None), generated_at=WHEN)
        self.assertIn("## Maintainability", md)
        self.assertIn("Not available", md)
        self.assertNotIn("Nothing to act on", md)

    def test_clean_health_is_distinct_from_absent_health(self):
        md = render_handbook_markdown(handbook(health=health(findings=[])), generated_at=WHEN)
        self.assertIn("Nothing to act on.", md)
        self.assertNotIn("Not available", md)

    def test_provenance_explains_what_the_model_was_allowed_to_do(self):
        md = render_handbook_markdown(handbook(), generated_at=WHEN)
        self.assertIn("tree-sitter", md)
        self.assertIn("cannot add a file or an edge", md)
        self.assertIn("Regenerate it rather than editing it by hand", md)


class HandbookNothingToGradeTest(unittest.TestCase):
    """
    Three states the handbook must keep apart: graded with findings,
    graded with none, and never graded at all.
    """

    def test_an_ungradable_repo_is_not_reported_as_healthy(self):
        h = handbook(health=health(source_file_count=0, symbol_count=0, findings=[]))
        md = render_handbook_markdown(h, generated_at=WHEN)
        self.assertIn("Nothing to grade", md)
        self.assertNotIn("Grade B", md)
        self.assertNotIn("Nothing to act on", md)

    def test_a_clean_repo_still_says_so(self):
        h = handbook(health=health(source_file_count=9, findings=[]))
        md = render_handbook_markdown(h, generated_at=WHEN)
        self.assertIn("Nothing to act on.", md)
        self.assertNotIn("Nothing to grade", md)

    def test_missing_health_is_a_third_thing_again(self):
        md = render_handbook_markdown(handbook(health=None), generated_at=WHEN)
        self.assertIn("Not available", md)
        self.assertNotIn("Nothing to grade", md)
        self.assertNotIn("Nothing to act on", md)
