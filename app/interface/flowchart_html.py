"""
Renders a ControlFlowChart as one self-contained HTML file.

Same contract as the other exports: no network, readable with scripts
off, honest about what it left out. Shorter than the other two because
the flowchart carries its own legend in its shapes — a diamond is a
decision whether or not anything says so.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from app.domain.entities import ControlFlowChart
from app.infrastructure.parsing.control_flow import MAX_STEPS, Decision, Loop, Return, Step
from app.interface.architecture_html import _STYLE, blob_url
from app.interface.flowchart_svg import render_flowchart_svg

_FLOW_STYLE = """
.rp-flow-shape { cursor: default; }
.rp-flow-shape:focus-visible { outline: 2px solid var(--rp-accent); outline-offset: 2px; }
.legend { display: flex; flex-wrap: wrap; gap: 8px 20px; margin: 12px 0 0; padding: 0;
  list-style: none; font-size: 0.84rem; color: var(--rp-ink-dim); }
.legend b { color: var(--rp-ink); font-weight: 600; }
ol.steps { font-size: 0.88rem; }
ol.steps ol { margin: 4px 0 4px 0; }
ol.steps li { margin-bottom: 3px; }
"""


def _short(sha: str) -> str:
    return sha[:7] if sha else "unknown"


def _outline(items: list, chart: ControlFlowChart) -> list[str]:
    """
    The same flow as a nested list, so the document still says everything
    it knows with scripts off and in a screen reader.
    """
    out = ['<ol class="steps">']
    for item in items:
        line = getattr(item, "line", 0)
        cite = (
            f'<a class="mono" href="'
            f"{blob_url(chart.repo_full_name, chart.commit_sha, chart.file_path, line)}\">"
            f"line {line}</a>"
        )
        if isinstance(item, Decision):
            out.append(f"<li><strong>if</strong> <code>{escape(item.label)}</code> · {cite}")
            if item.yes:
                out.append("<div>then:</div>")
                out.extend(_outline(item.yes, chart))
            if item.no:
                out.append("<div>otherwise:</div>")
                out.extend(_outline(item.no, chart))
            out.append("</li>")
        elif isinstance(item, Loop):
            out.append(
                f"<li><strong>for each</strong> <code>{escape(item.label)}</code> · {cite}"
            )
            if item.body:
                out.extend(_outline(item.body, chart))
            out.append("</li>")
        elif isinstance(item, Return):
            out.append(f"<li><strong>return</strong> <code>{escape(item.label)}</code> · {cite}</li>")
        elif isinstance(item, Step):
            out.append(f"<li><code>{escape(item.label)}</code> · {cite}</li>")
    out.append("</ol>")
    return out


def render_flowchart_html(
    chart: ControlFlowChart, *, generated_at: datetime | None = None
) -> str:
    when = generated_at or chart.generated_at or datetime.now(timezone.utc)
    repo = escape(chart.repo_full_name)
    out: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{repo} — {escape(chart.symbol)}() flowchart</title>",
        f"<style>{_STYLE}{_FLOW_STYLE}</style>",
        "</head><body><main>",
        f"<h1>{escape(chart.symbol)}()</h1>",
        '<ul class="meta">',
        f'<li><a class="mono" href="'
        f'{blob_url(chart.repo_full_name, chart.commit_sha, chart.file_path, chart.start_line)}">'
        f"{escape(chart.file_path)}:{chart.start_line}</a></li>",
        f"<li>{repo}</li>",
        f"<li>Commit <code>{escape(_short(chart.commit_sha))}</code></li>",
        f"<li>{when.strftime('%Y-%m-%d %H:%M UTC')}</li>",
        "</ul>",
    ]

    if not chart.charted:
        out += [
            '<section class="panel callout">',
            "<h2>No flowchart for this one</h2>",
            "<p>Control flow is read from the syntax tree, and this build does not read it "
            "for this file's language — or this definition has no body to chart. The call "
            "sequence for the same function still works, because it is built from a "
            "different part of the parse.</p>",
            "</section>",
        ]
    elif chart.truncated:
        out += [
            '<section class="panel callout">',
            "<h2>This chart is cut short</h2>",
            f"<p>The function has more than {MAX_STEPS} steps, so the chart stops there. "
            "A flowchart longer than that stops being one.</p>",
            "</section>",
        ]

    out += [
        "<h2>Flow</h2>",
        '<div class="panel figure">',
        render_flowchart_svg(chart.items, symbol=chart.symbol),
        "</div>",
        '<ul class="legend">',
        "<li><b>Rounded</b> start and end</li>",
        "<li><b>Diamond</b> a decision or a loop test</li>",
        "<li><b>Box</b> a step</li>",
        "<li><b>Green box</b> a return, which ends that path</li>",
        "</ul>",
    ]

    if chart.items:
        out += [
            "<h2>The same flow, as text</h2>",
            "<p>Every shape above, in order, linked to the line it came from.</p>",
            *_outline(chart.items, chart),
        ]

    out += [
        "<footer>",
        "<p>Every shape is a statement tree-sitter found in this function: a diamond is an "
        "<code>if</code> or a loop in the file, not a guess at what the code means. No "
        "language model took part, so the same commit always produces the same chart.</p>",
        "<p>This charts what the code <em>can</em> do, not what one run did. A branch that "
        "never executes is still drawn.</p>",
        "</footer>",
        "</main></body></html>",
    ]
    return "\n".join(out)
