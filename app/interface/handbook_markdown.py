"""
Renders a Handbook as a Markdown document a team can commit.

Presentation, so it lives in the interface layer alongside the code
health renderer, and it borrows that module's vocabulary rather than
inventing a second one: a grade means the same thing in the handbook as
it does on the screen and in the standalone report.

The document is written to be read by someone who has never opened the
repository, and to survive being committed: no colour, no collapsible
sections, nothing that only renders on one host.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.domain.entities import Handbook
from app.interface.report_markdown import (
    CHECK_LABEL,
    GRADE_MEANING,
    SEVERITY_LABEL,
    SEVERITY_ORDER,
)

# How many reading steps get the full treatment. Past this the list stops
# being a reading order and starts being a file listing.
MAX_DETAILED_STEPS = 12


def _short(sha: str) -> str:
    return sha[:7] if sha else "unknown"


def render_handbook_markdown(handbook: Handbook, *, generated_at: datetime | None = None) -> str:
    when = (generated_at or handbook.generated_at or datetime.now(timezone.utc))
    guide = handbook.guide
    lines: list[str] = [
        f"# {handbook.repo_full_name} — a working handbook",
        "",
        f"- **Repository:** `{handbook.repo_full_name}`",
        f"- **Commit:** `{handbook.commit_sha}`",
        f"- **Generated:** {when.strftime('%Y-%m-%d %H:%M UTC')} by Repo Pilot",
    ]
    if handbook.detected_stack:
        lines.append("- **Stack:** " + ", ".join(handbook.detected_stack))
    lines.append(f"- **Files read:** {handbook.file_count}")
    lines.append("")

    if handbook.skipped_file_count:
        lines += [
            f"> **This handbook is incomplete.** {handbook.skipped_file_count} file(s) could not "
            "be fetched during ingestion, so anything depending on them may be missing. "
            "Re-run the ingest before relying on this document.",
            "",
        ]

    if guide.overview:
        lines += ["## What this codebase is", "", guide.overview, ""]

    if not guide.graph_available:
        lines += [
            "> No internal imports were found between this repository's files, so the reading "
            "order below is not ranked by what depends on what. Treat it as a list, not a path.",
            "",
        ]

    if guide.entry_points:
        lines += [
            "## Where execution starts",
            "",
            "These are the files the rest of the repository hangs off. Open one of them first.",
            "",
        ]
        lines += [f"- `{path}`" for path in guide.entry_points]
        lines.append("")

    if guide.reading_order:
        shown = guide.reading_order[:MAX_DETAILED_STEPS]
        rest = guide.reading_order[MAX_DETAILED_STEPS:]
        lines += [
            "## Reading order",
            "",
            "Ranked by how much of the repository leans on each file, entry points first. "
            "Read them in this order and each file will already make sense by the time you "
            "reach it.",
            "",
        ]
        for i, step in enumerate(shown, start=1):
            marker = " *(entry point)*" if step.is_entry_point else ""
            lines += [f"### {i}. `{step.file_path}`{marker}", ""]
            if step.why_it_matters:
                lines += [f"**Why it matters.** {step.why_it_matters}", ""]
            if step.what_to_understand:
                lines += [f"**What to take away.** {step.what_to_understand}", ""]
            if step.key_symbols:
                lines += [
                    "Key definitions: " + ", ".join(f"`{s}`" for s in step.key_symbols[:8]) + ".",
                    "",
                ]
            if step.depended_on_by:
                lines += [
                    f"Imported by {len(step.depended_on_by)} module(s), including "
                    + ", ".join(f"`{m}`" for m in step.depended_on_by[:4])
                    + ".",
                    "",
                ]
            if step.depends_on:
                lines += [
                    "Imports " + ", ".join(f"`{m}`" for m in step.depends_on[:4]) + ".",
                    "",
                ]

        if rest:
            lines += [
                f"### Also worth reading ({len(rest)})",
                "",
                "Ranked below the files above, still above everything else in the repository.",
                "",
            ]
            lines += [f"- `{step.file_path}`" for step in rest]
            lines.append("")

    if guide.suggested_questions:
        lines += [
            "## Questions worth asking next",
            "",
            "Each of these can be put to Repo Pilot against this commit, and the answer will "
            "cite the files it came from.",
            "",
        ]
        lines += [f"- {q}" for q in guide.suggested_questions]
        lines.append("")

    health = handbook.health
    if health is not None and not health.was_analysable:
        lines += [
            "## Maintainability",
            "",
            "**Nothing to grade.** No source files were found to check, so no score is "
            "given. This repository may be documentation, or its only code may sit in "
            "directories that are deliberately excluded from grading, such as `docs/` "
            "and `scripts/`.",
            "",
        ]
    elif health is not None:
        lines += [
            "## Maintainability",
            "",
            f"**Grade {health.grade} — {health.score}/100.** {GRADE_MEANING.get(health.grade, '')}",
            "",
            f"{health.source_file_count} source file(s), {health.test_file_count} of them tests, "
            f"{health.symbol_count} symbol(s), {len(health.findings)} finding(s).",
            "",
        ]
        if health.findings:
            ordered = sorted(
                health.findings,
                key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), -(f.metric or 0), f.title),
            )
            current = None
            for finding in ordered:
                if finding.severity != current:
                    current = finding.severity
                    count = sum(1 for f in ordered if f.severity == current)
                    lines += [f"### {SEVERITY_LABEL.get(current, current)} ({count})", ""]
                where = ", ".join(f"`{p}`" for p in finding.files) or "—"
                if finding.line and len(finding.files) == 1:
                    where = f"`{finding.files[0]}:{finding.line}`"
                lines += [
                    f"- **{finding.title}**  ",
                    f"  {finding.detail}  ",
                    f"  {CHECK_LABEL.get(finding.check, finding.check)} · {where}",
                ]
            lines.append("")
        else:
            lines += ["Nothing to act on.", ""]
    else:
        lines += [
            "## Maintainability",
            "",
            "Not available: this repository has no parsed index at this commit, so the "
            "structural checks could not run.",
            "",
        ]

    lines += [
        "## How this document was produced",
        "",
        "Repo Pilot read every file in the repository with tree-sitter and resolved each "
        "import to the file it actually points at. The reading order and the maintainability "
        "findings are computed from that structure, not inferred by a language model: a model "
        "wrote the explanations above, but it was only ever shown files the parser had already "
        "found, and it cannot add a file or an edge that is not in the repository.",
        "",
        f"This handbook describes commit `{_short(handbook.commit_sha)}`. It will drift as the "
        "code changes. Regenerate it rather than editing it by hand, and it will keep telling "
        "the truth about the repository it sits in.",
        "",
    ]
    return "\n".join(lines)
