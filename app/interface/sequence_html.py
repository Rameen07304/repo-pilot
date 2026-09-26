"""
Renders a CallSequence as one self-contained HTML file.

Same contract as the architecture export — no network, readable with
scripts off, honest about its limits — and the limits matter more here
than anywhere else in the product.

A call is matched by name. That is enough to be certain when the name is
unique in the repository, or when it is defined in the caller's own file,
or when exactly one candidate sits in a file the caller imports. Past
that the honest answer is that we do not know which `save` was meant, and
the page says so instead of drawing an arrow.

So this document carries two counts it would be easy to leave out: calls
that went somewhere outside the repository, and calls that were too
ambiguous to place. A sequence diagram that silently drops both looks far
more complete than it is.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path

from app.domain.entities import CallSequence
from app.interface.architecture_html import _STYLE, blob_url
from app.interface.sequence_svg import render_sequence_svg

_SEQUENCE_STYLE = """
.rp-msg-hit { cursor: pointer; }
.rp-msg-recursive { stroke-dasharray: 5 3; }
.rp-msg-hit:focus-visible { outline: 2px solid var(--rp-accent); outline-offset: -2px; }
.lit-msg { stroke: var(--rp-accent) !important; stroke-width: 2.4 !important; }
.depth { display: inline-block; min-width: 2.2em; color: var(--rp-ink-dim); }
/* The shared placeholder talks about imports, which is the other
   document's subject. Arrows here are calls. */
#detail:empty::before { content: "Select any arrow to see the call it was resolved from."; }
"""

_STATIC = Path(__file__).resolve().parent / "static"


def _script() -> str:
    return (_STATIC / "sequence_page.js").read_text(encoding="utf-8")


def _short(sha: str) -> str:
    return sha[:7] if sha else "unknown"


def _blob(repo: str, sha: str, path: str, line: int | None = None) -> str:
    return blob_url(repo, sha, path, line)


def render_sequence_html(
    sequence: CallSequence, *, generated_at: datetime | None = None
) -> str:
    when = generated_at or sequence.generated_at or datetime.now(timezone.utc)
    repo = escape(sequence.repo_full_name)
    sha = sequence.commit_sha

    svg = render_sequence_svg(sequence).replace(
        "<svg ",
        f'<svg data-repo="{escape(sequence.repo_full_name, quote=True)}" '
        f'data-sha="{escape(sha, quote=True)}" ',
        1,
    )

    out: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{repo} — {escape(sequence.entry_symbol)}() call sequence</title>",
        f"<style>{_STYLE}{_SEQUENCE_STYLE}</style>",
        "</head><body><main>",
        f"<h1>{escape(sequence.entry_symbol)}()</h1>",
        '<ul class="meta">',
        f'<li><a class="mono" href="{_blob(sequence.repo_full_name, sha, sequence.entry_file)}">'
        f"{escape(sequence.entry_file)}</a></li>",
        f'<li>{repo}</li>',
        f'<li>Commit <a class="mono" href="https://github.com/{repo}/tree/'
        f'{escape(sha, quote=True)}">{escape(_short(sha))}</a></li>',
        f"<li>{when.strftime('%Y-%m-%d %H:%M UTC')}</li>",
        "</ul>",
    ]

    # The limits come first whenever there are any, for the same reason
    # they do on the architecture export: a reader who scrolls no further
    # should still have seen them.
    caveats: list[str] = []
    if sequence.ambiguous_calls:
        listed = ", ".join(
            f"<code>{escape(name)}()</code> ({count})"
            for name, count in list(sequence.ambiguous_calls.items())[:8]
        )
        caveats.append(
            f"<li><strong>{sum(sequence.ambiguous_calls.values())} call(s) could not be "
            f"placed</strong>: {listed}. Each name matches several definitions in this "
            "repository and nothing chose between them, so no arrow was drawn rather than "
            "a possibly wrong one.</li>"
        )
    if sequence.unresolved_calls:
        listed = ", ".join(
            f"<code>{escape(name)}()</code> ({count})"
            for name, count in list(sequence.unresolved_calls.items())[:10]
        )
        caveats.append(
            f"<li><strong>{sequence.unresolved_total} call(s) left this repository</strong>: "
            f"{listed}. These are frameworks, the standard library or builtins. They are "
            "counted rather than drawn, because nothing here can open them to check.</li>"
        )
    if sequence.truncated:
        caveats.append(
            f"<li><strong>The trace stopped early</strong> at {sequence.max_depth} levels "
            "deep. What runs below that is not shown.</li>"
        )

    if caveats:
        out += [
            '<section class="panel callout">',
            "<h2>What this sequence does not show</h2>",
            "<ul>",
            *caveats,
            "</ul>",
            "</section>",
        ]

    out += [
        "<h2>Sequence</h2>",
        '<div class="panel figure">',
        svg,
        "</div>",
        '<p id="detail"></p>',
        '<p class="note">Read top to bottom. Each arrow is a call found inside the function '
        "above it, and the small text under it is the file and line the call is written on. "
        "A bar on a lifeline is how long that file is on the call stack, so a call drawn "
        "inside a bar happens because of the call that opened it.</p>",
    ]

    if sequence.is_empty:
        out += [
            '<section class="panel">',
            f"<p><strong>{escape(sequence.entry_symbol)}() calls nothing that lives in this "
            "repository.</strong> It may still do a great deal — every call it makes goes to "
            "a framework or the standard library, and those are listed above rather than "
            "drawn.</p>",
            "</section>",
        ]
    else:
        out += [
            "<h2>Evidence</h2>",
            "<p>Every call above, in order, with the line it was found on. Each links to that "
            f"line at commit <code>{escape(_short(sha))}</code>.</p>",
            "<table><thead><tr><th>#</th><th>Depth</th><th>Caller</th><th>Calls</th>"
            "<th>At</th></tr></thead><tbody>",
        ]
        for i, step in enumerate(sequence.steps, start=1):
            marker = " ↺" if step.is_recursive else ""
            out.append(
                f'<tr><td class="num">{i}</td>'
                f'<td><span class="depth">{"·" * step.depth or "—"}</span></td>'
                f'<td><a class="mono" href="'
                f'{_blob(sequence.repo_full_name, sha, step.from_file)}">'
                f"{escape(step.from_symbol)}()</a></td>"
                f'<td><a class="mono" href="'
                f'{_blob(sequence.repo_full_name, sha, step.to_file)}">'
                f"{escape(step.to_symbol)}(){marker}</a></td>"
                f'<td class="num"><a class="mono" href="'
                f'{_blob(sequence.repo_full_name, sha, step.from_file, step.line)}">'
                f"{escape(step.from_file)}:{step.line}</a></td></tr>"
            )
        out.append("</tbody></table>")
        if any(s.is_recursive for s in sequence.steps):
            out.append(
                '<p class="note">↺ marks a call to something already on the stack. It is '
                "drawn once and not followed, because recursion is worth seeing and not "
                "worth expanding forever.</p>"
            )

    out += [
        "<footer>",
        "<p>Every step here is a call tree-sitter found inside a function definition and "
        "resolved to a symbol in this repository. No language model chose the steps or their "
        "order: the order is the order the calls appear in the source, so the same commit "
        "always produces the same sequence.</p>",
        "<p>Calls are matched by name, which is certain when the name is unique in the "
        "repository, defined in the caller's own file, or found in exactly one file the "
        "caller imports. Anything less certain is counted above, not drawn. This is a map of "
        "what the code can do, not a recording of one run: a branch that never executes is "
        "still a call.</p>",
        "</footer>",
        "</main>",
        f"<script>{_script()}</script>",
        "</body></html>",
    ]
    return "\n".join(out)
