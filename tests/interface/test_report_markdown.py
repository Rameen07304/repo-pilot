"""
The downloadable Markdown report: it must say the same things the
screen says, and never invent a finding of its own.
"""
import unittest
from datetime import datetime, timezone

from app.domain.entities import CodeHealthFinding, CodeHealthReport
from app.interface.report_markdown import render_code_health_markdown

WHEN = datetime(2026, 3, 1, 9, 30, tzinfo=timezone.utc)


def report(**kwargs):
    defaults = dict(
        repo_full_name="acme/widgets", commit_sha="abc123def456", score=88, grade="B",
        source_file_count=18, test_file_count=2, symbol_count=109, findings=[], deductions={},
    )
    defaults.update(kwargs)
    return CodeHealthReport(**defaults)


def finding(**kwargs):
    defaults = dict(
        check="long_function", severity="low", title="calculate() is 109 lines",
        detail="Long functions hide multiple responsibilities.", files=["app/scoring.py"],
        symbol="calculate", line=6, metric=109,
    )
    defaults.update(kwargs)
    return CodeHealthFinding(**defaults)


class ReportMarkdownTest(unittest.TestCase):
    def test_header_carries_provenance(self):
        md = render_code_health_markdown(report(), generated_at=WHEN)
        self.assertIn("# Code health report — acme/widgets", md)
        self.assertIn("`abc123def456`", md)
        self.assertIn("2026-03-01 09:30 UTC by Repo Pilot", md)

    def test_grade_is_explained_not_just_stated(self):
        md = render_code_health_markdown(report(), generated_at=WHEN)
        self.assertIn("**Grade B — 88/100.**", md)
        self.assertIn("In good shape.", md)  # the same sentence the UI shows

    def test_every_grade_has_a_meaning(self):
        for grade in ("A", "B", "C", "D", "F"):
            md = render_code_health_markdown(report(grade=grade), generated_at=WHEN)
            body = md.split("## Result")[1].split("##")[0]
            self.assertGreater(len(body.strip().splitlines()[2]), 20, f"grade {grade} has no explanation")

    def test_no_findings_says_so(self):
        md = render_code_health_markdown(report(grade="A", score=100), generated_at=WHEN)
        self.assertIn("None. Nothing to act on.", md)

    def test_findings_grouped_by_severity_with_the_band_explained(self):
        md = render_code_health_markdown(
            report(findings=[
                finding(severity="low", title="small thing"),
                finding(severity="high", title="big thing", check="untested_module"),
                finding(severity="medium", title="middling thing", check="hub_module"),
            ]),
            generated_at=WHEN,
        )
        self.assertIn("### Worth fixing (1)", md)
        self.assertIn("### Worth a look (1)", md)
        self.assertIn("### Minor (1)", md)
        self.assertIn("Start here.", md)
        # severity order: high before medium before low
        self.assertLess(md.index("Worth fixing"), md.index("Worth a look"))
        self.assertLess(md.index("Worth a look"), md.index("Minor ("))

    def test_finding_includes_detail_check_and_location(self):
        md = render_code_health_markdown(report(findings=[finding()]), generated_at=WHEN)
        self.assertIn("**calculate() is 109 lines**", md)
        self.assertIn("Long functions hide multiple responsibilities.", md)
        self.assertIn("Long functions · `app/scoring.py:6`", md)

    def test_multi_file_finding_lists_every_file_without_a_line(self):
        md = render_code_health_markdown(
            report(findings=[finding(check="circular_import", files=["a.py", "b.py"], line=None, symbol=None)]),
            generated_at=WHEN,
        )
        self.assertIn("`a.py`, `b.py`", md)

    def test_deductions_table_explains_each_check(self):
        md = render_code_health_markdown(
            report(deductions={"long_function": 6, "large_file": 1}), generated_at=WHEN
        )
        self.assertIn("| Long functions | −6 | functions long enough to hide several jobs |", md)
        self.assertLess(md.index("Long functions |"), md.index("Large files |"))  # biggest deduction first

    def test_methodology_is_stated(self):
        md = render_code_health_markdown(report(), generated_at=WHEN)
        self.assertIn("no AI involved", md)
        self.assertIn("never runs the code", md)


if __name__ == "__main__":
    unittest.main()


class NothingToGradeTest(unittest.TestCase):
    """
    No findings scores 100. On a repository with no gradable source that
    is not a clean bill of health, it is an unexamined one, and the two
    must not read the same.
    """

    def test_a_repo_with_no_source_is_not_given_a_grade(self):
        md = render_code_health_markdown(report(source_file_count=0, symbol_count=0), generated_at=WHEN)
        self.assertIn("Nothing to grade", md)
        self.assertNotIn("Nothing here needs attention", md)

    def test_it_says_why_rather_than_just_refusing(self):
        md = render_code_health_markdown(report(source_file_count=0), generated_at=WHEN)
        self.assertIn("docs/", md)
        self.assertIn("there was nothing to score", md)

    def test_the_checks_are_still_listed_so_the_reader_knows_what_was_skipped(self):
        md = render_code_health_markdown(report(source_file_count=0), generated_at=WHEN)
        self.assertIn("Checks run:", md)

    def test_a_repo_with_source_still_gets_its_grade(self):
        md = render_code_health_markdown(
            report(source_file_count=12, symbol_count=40, grade="B", score=88), generated_at=WHEN
        )
        self.assertIn("**Grade B — 88/100.**", md)
        self.assertNotIn("Nothing to grade", md)

    def test_a_genuinely_clean_repo_is_distinct_from_an_unexamined_one(self):
        clean = render_code_health_markdown(report(source_file_count=9, findings=[]), generated_at=WHEN)
        self.assertIn("None. Nothing to act on.", clean)
        self.assertNotIn("Nothing to grade", clean)
