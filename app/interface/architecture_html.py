"""
Renders an ArchitectureExport as one self-contained HTML file.

Constraints the document is written to satisfy, in order:

  * No network. No CDN, no font request, no script tag pointing anywhere.
    A reviewer opens the file from a zip on a plane and it is complete.
  * Readable with JavaScript disabled. The script only adds highlighting
    and a detail panel; the evidence table below the diagram lists every
    edge and every line it came from as plain HTML, so nothing is behind
    an interaction.
  * Honest about its own limits. The coverage section is not an appendix,
    it is the part that makes the rest usable, so it sits above the fold
    whenever the export is incomplete.

Every line number links to the file at that line on the exact commit the
export describes, which is what turns "trust me" into "check it".
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from app.domain.entities import ArchitectureExport
from app.interface.architecture_svg import render_architecture_svg

_STYLE = """
:root {
  color-scheme: light dark;
  --rp-base: #F6F8FC; --rp-surface: #FFFFFF; --rp-sunken: #EBF1F8;
  --rp-line: #D5DEEA; --rp-accent: #0B5FD0; --rp-ink: #0B1420;
  --rp-ink-body: #35425A; --rp-ink-dim: #515D72; --rp-warn: #8A6200;
  --rp-edge: #8494AC; --rp-node-bg: #FFFFFF; --rp-node-line: #C4D0E0;
}
@media (prefers-color-scheme: dark) {
  :root {
    --rp-base: #0A0D12; --rp-surface: #121722; --rp-sunken: #0C1119;
    --rp-line: #222C3C; --rp-accent: #5AA9FF; --rp-ink: #F1F5FA;
    --rp-ink-body: #C6D1E0; --rp-ink-dim: #8C9BB0; --rp-warn: #E6C25A;
    --rp-edge: #5B6B84; --rp-node-bg: #161D29; --rp-node-line: #2C3849;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0 16px 72px;
  background: var(--rp-base); color: var(--rp-ink-body);
  font: 15px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif;
}
main { max-width: 1100px; margin: 0 auto; }
h1, h2 { color: var(--rp-ink); line-height: 1.25; }
h1 { font-size: clamp(1.5rem, 4vw, 2rem); margin: 40px 0 8px; letter-spacing: -0.02em; }
h2 { font-size: 1.15rem; margin: 40px 0 12px; }
a { color: var(--rp-accent); }
code, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.92em; }
.meta { display: flex; flex-wrap: wrap; gap: 8px 20px; margin: 0 0 4px; padding: 0; list-style: none;
  color: var(--rp-ink-dim); font-size: 0.86rem; }
.panel { background: var(--rp-surface); border: 1px solid var(--rp-line);
  border-radius: 14px; padding: 18px 20px; }
.callout { border-left: 3px solid var(--rp-warn); }
.callout h2 { margin-top: 0; }
.figure { overflow: auto; padding: 12px; -webkit-overflow-scrolling: touch; }
.figure svg { display: block; margin: 0 auto; max-width: none; }
.rp-edge-hit { cursor: pointer; }
.rp-edge-cycle { stroke-dasharray: 5 4; }
.rp-node { cursor: pointer; }
.rp-node:focus-visible rect, .rp-edge-hit:focus-visible { outline: 2px solid var(--rp-accent);
  outline-offset: 2px; }
.dim { opacity: 0.18; }
.lit { stroke: var(--rp-accent) !important; stroke-width: 2.4 !important; opacity: 1 !important; }
.lit-node rect { stroke: var(--rp-accent) !important; stroke-width: 2 !important; }
#detail { margin-top: 12px; font-size: 0.9rem; }
#detail:empty::before { content: "Select any arrow to see the import it was resolved from.";
  color: var(--rp-ink-dim); }
table { width: 100%; border-collapse: collapse; font-size: 0.86rem; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--rp-line);
  vertical-align: top; }
th { color: var(--rp-ink); font-weight: 600; white-space: nowrap; }
tbody tr:hover { background: var(--rp-sunken); }
td.num { text-align: right; white-space: nowrap; }
.stats { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  margin: 0; padding: 0; }
.stats div { background: var(--rp-sunken); border-radius: 10px; padding: 12px 14px; }
.stats dt { color: var(--rp-ink-dim); font-size: 0.76rem; text-transform: uppercase;
  letter-spacing: 0.06em; }
.stats dd { margin: 4px 0 0; color: var(--rp-ink); font-size: 1.3rem; font-weight: 600; }
.note { color: var(--rp-ink-dim); font-size: 0.86rem; }
footer { margin-top: 48px; padding-top: 20px; border-top: 1px solid var(--rp-line);
  color: var(--rp-ink-dim); font-size: 0.84rem; }
@media (prefers-reduced-motion: no-preference) {
  .rp-edge, .rp-node rect { transition: opacity .18s ease, stroke .18s ease; }
}
"""

# Highlighting and the detail panel only. Every fact this script shows is
# already in the table below the diagram, so a reader with scripts off
# loses convenience and no information.
_SCRIPT = """
(function () {
  var svg = document.querySelector('svg.rp-arch');
  var detail = document.getElementById('detail');
  if (!svg || !detail) return;
  var edges = Array.prototype.slice.call(svg.querySelectorAll('.rp-edge'));
  var hits = Array.prototype.slice.call(svg.querySelectorAll('.rp-edge-hit'));
  var nodes = Array.prototype.slice.call(svg.querySelectorAll('.rp-node'));
  var repo = svg.getAttribute('data-repo') || '';
  var sha = svg.getAttribute('data-sha') || '';

  function clear() {
    edges.concat(nodes).forEach(function (el) {
      el.classList.remove('dim', 'lit', 'lit-node');
    });
  }
  function focusEdge(from, to, lines) {
    clear();
    edges.forEach(function (e) {
      var match = e.getAttribute('data-from') === from && e.getAttribute('data-to') === to;
      e.classList.add(match ? 'lit' : 'dim');
    });
    nodes.forEach(function (n) {
      var p = n.getAttribute('data-path');
      if (p === from || p === to) n.classList.add('lit-node'); else n.classList.add('dim');
    });
    var numbers = lines ? lines.split(',').filter(Boolean) : [];
    var html = '<strong class="mono">' + from + '</strong> imports <strong class="mono">'
      + to + '</strong>';
    if (numbers.length && repo && sha) {
      html += ' &middot; ' + numbers.map(function (n) {
        return '<a class="mono" target="_blank" rel="noreferrer noopener" href="https://github.com/'
          + repo + '/blob/' + sha + '/' + from + '#L' + n + '">line ' + n + '</a>';
      }).join(', ');
    } else if (numbers.length) {
      html += ' &middot; <span class="mono">line ' + numbers.join(', ') + '</span>';
    } else {
      html += ' &middot; <span class="note">no single line accounts for this edge</span>';
    }
    detail.innerHTML = html;
  }
  function focusNode(path) {
    clear();
    var related = 0;
    edges.forEach(function (e) {
      var touches = e.getAttribute('data-from') === path || e.getAttribute('data-to') === path;
      if (touches) related++;
      e.classList.add(touches ? 'lit' : 'dim');
    });
    var neighbours = {};
    edges.forEach(function (e) {
      var f = e.getAttribute('data-from'), t = e.getAttribute('data-to');
      if (f === path) neighbours[t] = 1;
      if (t === path) neighbours[f] = 1;
    });
    nodes.forEach(function (n) {
      var p = n.getAttribute('data-path');
      if (p === path) n.classList.add('lit-node');
      else if (!neighbours[p]) n.classList.add('dim');
    });
    detail.innerHTML = '<strong class="mono">' + path + '</strong> &middot; '
      + related + ' dependenc' + (related === 1 ? 'y' : 'ies') + ' shown';
  }

  hits.forEach(function (hit) {
    function activate(ev) {
      ev.stopPropagation();
      focusEdge(hit.getAttribute('data-from'), hit.getAttribute('data-to'),
                hit.getAttribute('data-lines'));
    }
    hit.addEventListener('click', activate);
    hit.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); activate(ev); }
    });
  });
  nodes.forEach(function (node) {
    function activate(ev) {
      ev.stopPropagation();
      focusNode(node.getAttribute('data-path'));
    }
    node.addEventListener('click', activate);
    node.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); activate(ev); }
    });
  });
  svg.addEventListener('click', function () { clear(); detail.innerHTML = ''; });
})();
"""


def _short(sha: str) -> str:
    return sha[:7] if sha else "unknown"


def _blob_url(export: ArchitectureExport, path: str, line: int | None = None) -> str:
    url = f"https://github.com/{export.repo_full_name}/blob/{export.commit_sha}/{path}"
    return f"{url}#L{line}" if line else url


def _coverage_section(export: ArchitectureExport) -> list[str]:
    """
    What the picture does not show. Printed whether or not anything is
    missing: "nothing was skipped" is a claim worth making explicitly, and
    a document that only mentions gaps when it has them teaches readers to
    assume silence means completeness.
    """
    c = export.coverage
    out: list[str] = []
    if c.is_complete:
        out += [
            '<section class="panel">',
            "<h2>Coverage</h2>",
            f"<p>Every one of this commit's {c.files_in_repo} files was fetched, every file "
            "whose imports this parser reads was parsed, and every resolved dependency is "
            "drawn above. Nothing was dropped to make the picture fit.</p>",
            "</section>",
        ]
        return out

    out += ['<section class="panel callout">', "<h2>What this diagram does not show</h2>", "<ul>"]
    if c.files_skipped:
        out.append(
            f"<li><strong>{c.files_skipped} file(s) were never fetched</strong> during "
            "ingestion, so any dependency involving them is missing here. Re-run the ingest "
            "before relying on this document.</li>"
        )
    if c.unparsed_extensions:
        listed = ", ".join(
            f"<code>{escape(ext)}</code> ({count})"
            for ext, count in c.unparsed_extensions.items()
        )
        out.append(
            f"<li><strong>{c.unparsed_file_count} file(s) have no import parser here</strong>: "
            f"{listed}. These files may depend on other files; no edge above says so.</li>"
        )
    if c.degraded_languages:
        listed = ", ".join(escape(lang) for lang in sorted(c.degraded_languages))
        out.append(
            f"<li><strong>Grammar unavailable for {listed}</strong> on the machine that built "
            "this export, so those files contributed no dependencies even though they normally "
            "would.</li>"
        )
    if c.nodes_drawn < c.nodes_total:
        out.append(
            f"<li><strong>{c.nodes_total - c.nodes_drawn} of {c.nodes_total} files were left "
            "out of the drawing</strong> to keep it legible. The least-connected went first, so "
            "the shape is intact and the detail is not.</li>"
        )
    if c.edges_drawn < c.edges_total:
        out.append(
            f"<li><strong>{c.edges_total - c.edges_drawn} of {c.edges_total} dependencies are "
            "not drawn</strong>, for the same reason.</li>"
        )
    out += ["</ul>", "</section>"]
    return out


def render_architecture_html(
    export: ArchitectureExport, *, generated_at: datetime | None = None
) -> str:
    when = generated_at or export.generated_at or datetime.now(timezone.utc)
    c = export.coverage
    repo = escape(export.repo_full_name)
    drawn_nodes = len({p for e in export.edges for p in (e.from_path, e.to_path)})

    head = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{repo} — architecture at {escape(_short(export.commit_sha))}</title>",
        f"<style>{_STYLE}</style>",
        "</head><body><main>",
        f"<h1>{repo}</h1>",
        '<ul class="meta">',
        f'<li>Commit <a class="mono" href="https://github.com/{repo}/tree/'
        f'{escape(export.commit_sha, quote=True)}">{escape(_short(export.commit_sha))}</a></li>',
        f"<li>{when.strftime('%Y-%m-%d %H:%M UTC')}</li>",
        "<li>Repo Pilot</li>",
    ]
    if export.detected_stack:
        head.append(f"<li>{escape(', '.join(export.detected_stack))}</li>")
    head.append("</ul>")

    body: list[str] = []
    # The gaps come before the picture when there are gaps: a reader who
    # scrolls no further should still have seen them.
    if not c.is_complete:
        body += _coverage_section(export)

    svg = render_architecture_svg(export).replace(
        "<svg ",
        f'<svg data-repo="{escape(export.repo_full_name, quote=True)}" '
        f'data-sha="{escape(export.commit_sha, quote=True)}" ',
        1,
    )
    body += [
        "<h2>Dependencies</h2>",
        '<div class="panel figure">',
        svg,
        "</div>",
        '<p id="detail"></p>',
        '<p class="note">Each arrow points from a file to a file it imports. '
        "A dashed arrow closes a circular dependency. Arrows were resolved by parsing the "
        "repository, never suggested by a model.</p>",
    ]

    body += [
        "<h2>By the numbers</h2>",
        '<dl class="stats">',
        f"<div><dt>Files in the repo</dt><dd>{c.files_in_repo}</dd></div>",
        f"<div><dt>Files drawn</dt><dd>{drawn_nodes}</dd></div>",
        f"<div><dt>Dependencies drawn</dt><dd>{len(export.edges)}</dd></div>",
        f"<div><dt>With a source line</dt><dd>{export.evidenced_edge_count}"
        f"/{len(export.edges)}</dd></div>",
        "</dl>",
    ]

    body += [
        "<h2>Evidence</h2>",
        "<p>Every dependency above, and the line it was resolved from. Each line links to "
        f"that file at that line on commit <code>{escape(_short(export.commit_sha))}</code>, "
        "so any arrow here can be checked against the source without this tool.</p>",
        "<table><thead><tr><th>Importing file</th><th>Imports</th>"
        "<th>At line</th></tr></thead><tbody>",
    ]
    if export.edges:
        for edge in sorted(export.edges, key=lambda e: (e.from_path, e.to_path)):
            if edge.lines:
                cited = ", ".join(
                    f'<a class="mono" href="{_blob_url(export, edge.from_path, n)}">{n}</a>'
                    for n in edge.lines
                )
            else:
                cited = '<span class="note">not line-attributable</span>'
            body.append(
                f'<tr><td><a class="mono" href="{_blob_url(export, edge.from_path)}">'
                f"{escape(edge.from_path)}</a></td>"
                f'<td><a class="mono" href="{_blob_url(export, edge.to_path)}">'
                f"{escape(edge.to_path)}</a></td>"
                f'<td class="num">{cited}</td></tr>'
            )
    else:
        body.append(
            '<tr><td colspan="3" class="note">No internal dependencies were resolved between '
            "this repository's files.</td></tr>"
        )
    body.append("</tbody></table>")

    if c.is_complete:
        body += _coverage_section(export)

    body += [
        "<footer>",
        "<p>Built by parsing this repository with tree-sitter and resolving each import to the "
        "file it points at. No language model took part in deciding which files exist or which "
        "depend on which, so the same commit always produces the same diagram and two exports "
        "can be diffed.</p>",
        f"<p>This describes commit <code>{escape(_short(export.commit_sha))}</code> and will "
        "drift as the code changes. Regenerate it rather than editing it.</p>",
        "</footer>",
        "</main>",
        f"<script>{_SCRIPT}</script>",
        "</body></html>",
    ]
    return "\n".join(head + body)
