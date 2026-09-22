"""
Renders a CodeHealthReport as a Markdown document people can keep.

Presentation, so it lives in the interface layer: the use case decides
what is true about the repo, this decides how to say it on a page. The
same wording as the UI, so a downloaded report and the screen it came
from never disagree.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.domain.entities import CodeHealthReport

GRADE_MEANING = {
    "A": "Healthy. Nothing here needs attention right now.",
    "B": "In good shape. A few things worth tidying when you are nearby.",
    "C": "Workable, with some real friction worth planning in.",
    "D": "Rough edges that will slow down anyone new to this code.",
    "F": "Hard to work in safely. Worth addressing before adding features.",
}

SEVERITY_LABEL = {"high": "Worth fixing", "medium": "Worth a look", "low": "Minor"}
SEVERITY_BLURB = {
    "high": "These slow down or mislead anyone working in the code. Start here.",
    "medium": "Not urgent, but they make the code harder to change than it needs to be.",
    "low": "Small cleanups. Nothing here is broken — fix them when you are already in the file.",
}
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

CHECK_LABEL = {
    "circular_import": "Circular imports",
    "hub_module": "Hub modules",
    "untested_module": "Untested modules",
    "long_function": "Long functions",
    "large_file": "Large files",
    "unreferenced_symbol": "Unreferenced symbols",
}
CHECK_EXPLAINER = {
    "circular_import": "modules that import each other in a loop",
    "hub_module": "modules that many others depend on",
    "untested_module": "code no test file imports",
    "long_function": "functions long enough to hide several jobs",
    "large_file": "files large enough to be hard to navigate",
    "unreferenced_symbol": "functions nothing else appears to call",
}


def render_code_health_markdown(report: CodeHealthReport, *, generated_at: datetime | None = None) -> str:
    when = (generated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        f"# Code health report — {report.repo_full_name}",
        "",
        f"- **Repository:** `{report.repo_full_name}`",
        f"- **Commit:** `{report.commit_sha}`",
        f"- **Generated:** {when} by Repo Pilot",
        "",
        "## Result",
        "",
        f"**Grade {report.grade} — {report.score}/100.** {GRADE_MEANING.get(report.grade, '')}",
        "",
        f"Analysed {report.source_file_count} source file(s) and {report.symbol_count} symbol(s); "
        f"{report.test_file_count} of those files are tests. {len(report.findings)} finding(s).",
        "",
    ]

    if report.deductions:
        lines += ["### Where the points went", "", "| Check | Points deducted | What it means |", "|---|---:|---|"]
        for check, points in sorted(report.deductions.items(), key=lambda kv: -kv[1]):
            lines.append(f"| {CHECK_LABEL.get(check, check)} | −{points} | {CHECK_EXPLAINER.get(check, '')} |")
        lines.append("")

    if not report.findings:
        lines += ["## Findings", "", "None. Nothing to act on.", ""]
    else:
        lines += ["## Findings", ""]
        ordered = sorted(
            report.findings,
            key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), -(f.metric or 0), f.title),
        )
        current = None
        for finding in ordered:
            if finding.severity != current:
                current = finding.severity
                count = sum(1 for f in ordered if f.severity == current)
                lines += [
                    f"### {SEVERITY_LABEL.get(current, current)} ({count})",
                    "",
                    f"_{SEVERITY_BLURB.get(current, '')}_",
                    "",
                ]
            where = ", ".join(f"`{path}`" for path in finding.files) or "—"
            if finding.line and len(finding.files) == 1:
                where = f"`{finding.files[0]}:{finding.line}`"
            lines += [
                f"- **{finding.title}**  ",
                f"  {finding.detail}  ",
                f"  {CHECK_LABEL.get(finding.check, finding.check)} · {where}",
            ]
        lines.append("")

    lines += [
        "## How this was measured",
        "",
        "Every finding is computed from the repository's own parsed structure — "
        "the import graph and the symbols tree-sitter extracted — with no AI involved, "
        "so the same commit always produces the same report.",
        "",
        "The score is a rule of thumb for prioritising, not a verdict on whether the "
        "project works: it looks at structure only and never runs the code or judges "
        "what it does. Each check can deduct up to a fixed maximum, so one noisy "
        "category cannot dominate the result.",
        "",
        "Checks run: " + ", ".join(f"{label.lower()} ({CHECK_EXPLAINER[key]})" for key, label in CHECK_LABEL.items()) + ".",
        "",
    ]
    return "\n".join(lines)
