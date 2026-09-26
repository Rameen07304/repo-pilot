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
.toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
  margin: 0 0 12px; }
.toolbar input[type="search"] { flex: 1 1 220px; min-width: 180px; padding: 9px 13px;
  border: 1px solid var(--rp-line); border-radius: 9px; background: var(--rp-surface);
  color: var(--rp-ink); font: inherit; font-size: 0.9rem; }
.toolbar input[type="search"]:focus-visible { outline: 2px solid var(--rp-accent);
  outline-offset: 1px; }
.toolbar button { padding: 9px 15px; border: 1px solid var(--rp-line); border-radius: 9px;
  background: var(--rp-surface); color: var(--rp-ink); font: inherit; font-size: 0.85rem;
  cursor: pointer; }
.toolbar button:hover { border-color: var(--rp-accent); color: var(--rp-accent); }
.toolbar button:active { transform: translateY(1px); }
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

# Everything below is an enhancement. Every fact it surfaces is already
# in the evidence table as plain HTML, so a reader with scripts off loses
# convenience and no information — which is why the controls it drives
# are hidden until it runs.
_SCRIPT = r"""
(function () {
  var svg = document.querySelector('svg.rp-arch');
  var detail = document.getElementById('detail');
  if (!svg || !detail) return;
  var edges = Array.prototype.slice.call(svg.querySelectorAll('.rp-edge'));
  var hits = Array.prototype.slice.call(svg.querySelectorAll('.rp-edge-hit'));
  var nodes = Array.prototype.slice.call(svg.querySelectorAll('.rp-node'));
  var repo = svg.getAttribute('data-repo') || '';
  var sha = svg.getAttribute('data-sha') || '';

  // Adjacency, built once. Reach is transitive: the question a reader
  // actually has is "what does changing this touch", and the answer
  // rarely stops at the first hop.
  var out = {}, into = {};
  edges.forEach(function (e) {
    var f = e.getAttribute('data-from'), t = e.getAttribute('data-to');
    (out[f] = out[f] || []).push(t);
    (into[t] = into[t] || []).push(f);
  });
  function reach(start, graph) {
    var seen = {}, stack = [start];
    while (stack.length) {
      var n = stack.pop();
      (graph[n] || []).forEach(function (m) {
        if (!seen[m] && m !== start) { seen[m] = 1; stack.push(m); }
      });
    }
    return seen;
  }

  function clear() {
    edges.concat(nodes).forEach(function (el) {
      el.classList.remove('dim', 'lit', 'lit-node');
    });
  }
  function linkFor(path, line) {
    return repo && sha
      ? 'https://github.com/' + repo + '/blob/' + sha + '/' + path + '#L' + line
      : '';
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
        return '<a class="mono" target="_blank" rel="noreferrer noopener" href="'
          + linkFor(from, n) + '">line ' + n + '</a>';
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
    var downstream = reach(path, out);   // what this file leans on
    var upstream = reach(path, into);    // what leans on this file
    var involved = {};
    involved[path] = 1;
    Object.keys(downstream).forEach(function (k) { involved[k] = 1; });
    Object.keys(upstream).forEach(function (k) { involved[k] = 1; });

    edges.forEach(function (e) {
      var f = e.getAttribute('data-from'), t = e.getAttribute('data-to');
      e.classList.add(involved[f] && involved[t] ? 'lit' : 'dim');
    });
    nodes.forEach(function (n) {
      var p = n.getAttribute('data-path');
      if (p === path) n.classList.add('lit-node');
      else if (!involved[p]) n.classList.add('dim');
    });
    var up = Object.keys(upstream).length, down = Object.keys(downstream).length;
    detail.innerHTML = '<strong class="mono">' + path + '</strong> &middot; '
      + up + ' file(s) reach it, it reaches ' + down + ' &middot; '
      + '<span class="note">transitive, not just direct neighbours</span>';
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

  // ---- filter -------------------------------------------------------
  // Archify's own stated weakness is that a large repository draws a
  // diagram too dense to read. Filtering is the cheapest answer to that:
  // the whole graph stays on the page, and everything not matching
  // recedes rather than disappearing, so context is never lost.
  var box = document.getElementById('rp-filter');
  if (box) {
    box.addEventListener('input', function () {
      var q = box.value.trim().toLowerCase();
      clear();
      if (!q) { detail.innerHTML = ''; return; }
      var matched = {};
      nodes.forEach(function (n) {
        var p = n.getAttribute('data-path');
        if (p.toLowerCase().indexOf(q) !== -1) matched[p] = 1;
        else n.classList.add('dim');
      });
      edges.forEach(function (e) {
        var f = e.getAttribute('data-from'), t = e.getAttribute('data-to');
        e.classList.add(matched[f] || matched[t] ? 'lit' : 'dim');
      });
      var count = Object.keys(matched).length;
      detail.innerHTML = count
        ? count + ' file(s) match <strong class="mono">' + q + '</strong>'
        : '<span class="note">nothing matches ' + q + '</span>';
    });
  }

  // ---- image export -------------------------------------------------
  // The SVG on the page paints from CSS custom properties defined on the
  // document. Once it is serialised into an <img> it has no document to
  // read them from, so the resolved values are baked into a <style> on
  // the clone. Without that step the PNG comes out unstyled.
  var NS = 'http://www.w3.org/2000/svg';
  var VARS = ['--rp-edge', '--rp-node-bg', '--rp-node-line', '--rp-ink', '--rp-ink-dim',
              '--rp-accent', '--rp-added', '--rp-removed'];
  function serialised() {
    var clone = svg.cloneNode(true);
    var cs = getComputedStyle(document.documentElement);
    var decls = VARS.map(function (v) {
      var value = cs.getPropertyValue(v).trim();
      return value ? v + ':' + value : '';
    }).filter(Boolean).join(';');
    var style = document.createElementNS(NS, 'style');
    style.textContent = 'svg{' + decls + '}'
      + '.rp-edge-cycle{stroke-dasharray:5 4}'
      + '.rp-edge-added{stroke:var(--rp-added);stroke-width:2.2}'
      + '.rp-edge-removed{stroke:var(--rp-removed);stroke-width:2.2;stroke-dasharray:6 4}'
      + '.rp-node-added rect{stroke:var(--rp-added);stroke-width:2}'
      + '.rp-node-removed rect{stroke:var(--rp-removed);stroke-width:2;stroke-dasharray:5 3}'
      + '.rp-node-moved rect{stroke:var(--rp-accent);stroke-width:2}';
    clone.insertBefore(style, clone.firstChild);
    // Highlight state is a viewer's temporary choice, not part of the
    // diagram, so an exported image never carries it.
    Array.prototype.forEach.call(clone.querySelectorAll('.dim,.lit,.lit-node'),
      function (el) { el.classList.remove('dim', 'lit', 'lit-node'); });
    return new XMLSerializer().serializeToString(clone);
  }
  function rasterise(draw, name) {
    var img = new Image();
    img.onload = function () { draw(img, name); };
    img.onerror = function () {
      detail.innerHTML = '<span class="note">this browser refused to rasterise the diagram; '
        + 'the page itself is still complete</span>';
    };
    img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(serialised());
  }
  function save(canvas, name) {
    canvas.toBlob(function (blob) {
      if (!blob) return;
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url; a.download = name; a.click();
      URL.revokeObjectURL(url);
    }, 'image/png');
  }
  function pageBackground() {
    var bg = getComputedStyle(document.body).backgroundColor;
    return bg && bg !== 'rgba(0, 0, 0, 0)' ? bg : '#ffffff';
  }
  var base = (repo || 'architecture').replace(/[^\w-]/g, '-') + '-' + sha.slice(0, 7);

  var pngBtn = document.getElementById('rp-png');
  if (pngBtn) {
    pngBtn.addEventListener('click', function () {
      rasterise(function (img, name) {
        var scale = 2;  // legible when dropped into a doc or a ticket
        var canvas = document.createElement('canvas');
        canvas.width = svg.width.baseVal.value * scale;
        canvas.height = svg.height.baseVal.value * scale;
        var ctx = canvas.getContext('2d');
        ctx.fillStyle = pageBackground();
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        save(canvas, name);
      }, base + '.png');
    });
  }

  var cardBtn = document.getElementById('rp-card');
  if (cardBtn) {
    cardBtn.addEventListener('click', function () {
      rasterise(function (img, name) {
        var W = 1200, H = 630, PAD = 56, HEADER = 104;
        var canvas = document.createElement('canvas');
        canvas.width = W; canvas.height = H;
        var ctx = canvas.getContext('2d');
        var cs = getComputedStyle(document.documentElement);
        ctx.fillStyle = pageBackground();
        ctx.fillRect(0, 0, W, H);

        ctx.fillStyle = cs.getPropertyValue('--rp-ink').trim() || '#0B1420';
        ctx.font = '600 34px system-ui, sans-serif';
        ctx.fillText(repo || 'architecture', PAD, PAD + 26);
        ctx.fillStyle = cs.getPropertyValue('--rp-ink-dim').trim() || '#515D72';
        ctx.font = '19px ui-monospace, Menlo, monospace';
        ctx.fillText(sha.slice(0, 7) + '  ·  ' + edges.length + ' dependencies  ·  '
                     + nodes.length + ' files', PAD, PAD + 60);

        // Fit the whole diagram rather than cropping it: a share card
        // that shows a corner of a graph tells the reader nothing.
        var availW = W - PAD * 2, availH = H - HEADER - PAD;
        var w = svg.width.baseVal.value, h = svg.height.baseVal.value;
        var k = Math.min(availW / w, availH / h);
        var dw = w * k, dh = h * k;
        ctx.drawImage(img, PAD + (availW - dw) / 2, HEADER + (availH - dh) / 2, dw, dh);
        save(canvas, name);
      }, base + '-card.png');
    });
  }

  // The controls are inert without this script, so they ship hidden and
  // are revealed only once it has wired them up.
  Array.prototype.forEach.call(document.querySelectorAll('[data-needs-js]'),
    function (el) { el.hidden = false; });
})();
"""


# Hidden until the script reveals them: with scripts off these controls
# would do nothing, and an inert button is worse than no button.
_TOOLBAR = (
    '<div class="toolbar">'
    '<input id="rp-filter" type="search" placeholder="filter files by path…" '
    'aria-label="Filter files by path" data-needs-js hidden>'
    '<button id="rp-png" type="button" data-needs-js hidden '
    'title="The diagram as a PNG at twice its size, without any highlighting">PNG</button>'
    '<button id="rp-card" type="button" data-needs-js hidden '
    'title="A 1200x630 image with the repository, the commit and the whole diagram, '
    'sized for a pull request or a chat message">share card</button>'
    "</div>"
)


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
        _TOOLBAR,
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
