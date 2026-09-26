"""
Renders an ArchitectureDelta as one self-contained HTML file.

Same constraints as the single-commit export — no network, readable
without JavaScript, honest about its limits — plus one of its own: the
drawing is the head commit's graph with removed dependencies added back
as ghosts. A reviewer needs to see what left, and a diagram that only
shows what survived cannot show that.

The reading order is deliberate. The counts come first, because the most
common answer is "nothing structural changed" and that should take one
glance. Then moves, because an unrecognised rename makes every other
number look wrong. Then the dependencies that appeared and disappeared,
each with the line it was written on.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from app.domain.entities import ArchitectureDelta, ArchitectureExport, ExportedEdge
from app.interface.architecture_html import _STYLE
from app.interface.architecture_svg import render_architecture_svg

_DELTA_STYLE = """
:root { --rp-added: #0B7A43; --rp-removed: #C0392B; }
@media (prefers-color-scheme: dark) {
  :root { --rp-added: #5CD69A; --rp-removed: #FF6B6B; }
}
.rp-edge-added { stroke: var(--rp-added) !important; stroke-width: 2.2 !important; }
.rp-edge-removed { stroke: var(--rp-removed) !important; stroke-width: 2.2 !important;
  stroke-dasharray: 6 4; }
.rp-node-added rect { stroke: var(--rp-added) !important; stroke-width: 2 !important; }
.rp-node-removed rect { stroke: var(--rp-removed) !important; stroke-width: 2 !important;
  stroke-dasharray: 5 3; }
.rp-node-moved rect { stroke: var(--rp-accent) !important; stroke-width: 2 !important; }
.key { display: flex; flex-wrap: wrap; gap: 8px 20px; margin: 10px 0 0; padding: 0;
  list-style: none; font-size: 0.84rem; color: var(--rp-ink-dim); }
.key span { display: inline-block; width: 22px; height: 0; vertical-align: middle;
  margin-right: 7px; border-top-width: 2px; border-top-style: solid; }
.key .k-add { border-color: var(--rp-added); }
.key .k-del { border-color: var(--rp-removed); border-top-style: dashed; }
.key .k-same { border-color: var(--rp-edge); }
.tag { display: inline-block; padding: 1px 7px; border-radius: 999px; font-size: 0.74rem;
  font-weight: 600; letter-spacing: 0.03em; text-transform: uppercase; }
.tag-add { color: var(--rp-added); background: color-mix(in srgb, var(--rp-added) 14%, transparent); }
.tag-del { color: var(--rp-removed); background: color-mix(in srgb, var(--rp-removed) 14%, transparent); }
.verdict { font-size: 1.05rem; color: var(--rp-ink); }
"""


def _short(sha: str) -> str:
    return sha[:7] if sha else "unknown"


def _blob(repo: str, sha: str, path: str, line: int | None = None) -> str:
    url = f"https://github.com/{repo}/blob/{sha}/{path}"
    return f"{url}#L{line}" if line else url


def _cited(repo: str, sha: str, path: str, lines: list[int]) -> str:
    if not lines:
        return '<span class="note">not line-attributable</span>'
    return ", ".join(
        f'<a class="mono" href="{_blob(repo, sha, path, n)}">{n}</a>' for n in lines
    )


def _change_table(delta: ArchitectureDelta) -> list[str]:
    repo = delta.repo_full_name
    rows: list[str] = []
    for edge in delta.added_edges:
        rows.append(
            f'<tr><td><span class="tag tag-add">added</span></td>'
            f'<td><a class="mono" href="{_blob(repo, delta.head_sha, edge.from_path)}">'
            f"{escape(edge.from_path)}</a></td>"
            f'<td><a class="mono" href="{_blob(repo, delta.head_sha, edge.to_path)}">'
            f"{escape(edge.to_path)}</a></td>"
            f'<td class="num">{_cited(repo, delta.head_sha, edge.from_path, edge.lines)}</td></tr>'
        )
    for edge in delta.removed_edges:
        rows.append(
            f'<tr><td><span class="tag tag-del">removed</span></td>'
            f'<td><a class="mono" href="{_blob(repo, delta.base_sha, edge.from_path)}">'
            f"{escape(edge.from_path)}</a></td>"
            f'<td><a class="mono" href="{_blob(repo, delta.base_sha, edge.to_path)}">'
            f"{escape(edge.to_path)}</a></td>"
            f'<td class="num">{_cited(repo, delta.base_sha, edge.from_path, edge.lines)}</td></tr>'
        )
    if not rows:
        rows.append(
            '<tr><td colspan="4" class="note">No dependency was added or removed.</td></tr>'
        )
    return [
        "<h2>Dependencies that changed</h2>",
        "<p>An added line links to the head commit; a removed one links to the base commit, "
        "where the import still exists. Either way the statement itself is one click away.</p>",
        '<table><thead><tr><th></th><th>Importing file</th><th>Imports</th>'
        "<th>At line</th></tr></thead><tbody>",
        *rows,
        "</tbody></table>",
    ]


def render_architecture_delta_html(
    delta: ArchitectureDelta, *, generated_at: datetime | None = None
) -> str:
    when = generated_at or delta.generated_at or datetime.now(timezone.utc)
    repo = escape(delta.repo_full_name)
    base_sha, head_sha = delta.base_sha, delta.head_sha

    # The drawing is the head graph plus the dependencies that left, so a
    # reviewer sees the removal rather than inferring it from an absence.
    # Ghost endpoints are mapped through the renames first: a removed edge
    # that pointed at a file which merely moved must land on that file's
    # box, not draw a second box under its old name.
    renamed = {m.from_path: m.to_path for m in delta.moved_nodes}
    ghosts = [
        ExportedEdge(
            renamed.get(e.from_path, e.from_path),
            renamed.get(e.to_path, e.to_path),
            list(e.lines),
        )
        for e in delta.removed_edges
    ]
    combined = ArchitectureExport(
        repo_full_name=delta.repo_full_name,
        commit_sha=head_sha,
        repo_url=delta.head.repo_url,
        edges=list(delta.head.edges) + ghosts,
        detected_stack=list(delta.head.detected_stack),
        coverage=delta.head.coverage,
    )
    edge_status = {(e.from_path, e.to_path): "added" for e in delta.added_edges}
    edge_status.update({(g.from_path, g.to_path): "removed" for g in ghosts})
    node_status = {p: "added" for p in delta.added_nodes}
    node_status.update({p: "removed" for p in delta.removed_nodes})
    node_status.update({m.to_path: "moved" for m in delta.moved_nodes})

    svg = render_architecture_svg(
        combined, edge_status=edge_status, node_status=node_status
    ).replace(
        "<svg ",
        f'<svg data-repo="{escape(delta.repo_full_name, quote=True)}" '
        f'data-sha="{escape(head_sha, quote=True)}" ',
        1,
    )

    out: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{repo} — architecture delta {escape(_short(base_sha))}..."
        f"{escape(_short(head_sha))}</title>",
        f"<style>{_STYLE}{_DELTA_STYLE}</style>",
        "</head><body><main>",
        f"<h1>{repo}</h1>",
        '<ul class="meta">',
        f'<li>Base <a class="mono" href="https://github.com/{repo}/tree/'
        f'{escape(base_sha, quote=True)}">{escape(_short(base_sha))}</a></li>',
        f'<li>Head <a class="mono" href="https://github.com/{repo}/tree/'
        f'{escape(head_sha, quote=True)}">{escape(_short(head_sha))}</a></li>',
        f"<li>{when.strftime('%Y-%m-%d %H:%M UTC')}</li>",
        "<li>Repo Pilot</li>",
        "</ul>",
    ]

    if delta.is_unchanged:
        out += [
            '<section class="panel">',
            "<h2>Nothing structural changed</h2>",
            f"<p class=\"verdict\">Between <code>{escape(_short(base_sha))}</code> and "
            f"<code>{escape(_short(head_sha))}</code> the files and the dependencies between "
            f"them are identical: {delta.unchanged_edge_count} dependency(s), all unchanged. "
            "Code inside those files may well have changed — this compares structure, not "
            "content.</p>",
            "</section>",
        ]
    else:
        out += [
            "<h2>What changed</h2>",
            '<dl class="stats">',
            f"<div><dt>Dependencies added</dt><dd>{len(delta.added_edges)}</dd></div>",
            f"<div><dt>Dependencies removed</dt><dd>{len(delta.removed_edges)}</dd></div>",
            f"<div><dt>Files added</dt><dd>{len(delta.added_nodes)}</dd></div>",
            f"<div><dt>Files removed</dt><dd>{len(delta.removed_nodes)}</dd></div>",
            f"<div><dt>Files moved</dt><dd>{len(delta.moved_nodes)}</dd></div>",
            f"<div><dt>Unchanged</dt><dd>{delta.unchanged_edge_count}</dd></div>",
            "</dl>",
        ]

    if delta.moved_nodes:
        out += [
            "<h2>Files that moved</h2>",
            "<p>Matched by file name. A move is resolved before dependencies are compared, so "
            "a file that only changed path does not appear as a removal and an addition, and "
            "its dependencies are not counted as churn.</p>",
            "<table><thead><tr><th>Was</th><th>Is now</th></tr></thead><tbody>",
        ]
        for moved in delta.moved_nodes:
            out.append(
                f'<tr><td><a class="mono" href="{_blob(delta.repo_full_name, base_sha, moved.from_path)}">'
                f"{escape(moved.from_path)}</a></td>"
                f'<td><a class="mono" href="{_blob(delta.repo_full_name, head_sha, moved.to_path)}">'
                f"{escape(moved.to_path)}</a></td></tr>"
            )
        out.append("</tbody></table>")

    if delta.added_nodes or delta.removed_nodes:
        out += ["<h2>Files that entered or left the graph</h2>", "<ul>"]
        for path in delta.added_nodes:
            out.append(
                f'<li><span class="tag tag-add">added</span> <a class="mono" '
                f'href="{_blob(delta.repo_full_name, head_sha, path)}">{escape(path)}</a></li>'
            )
        for path in delta.removed_nodes:
            out.append(
                f'<li><span class="tag tag-del">removed</span> <a class="mono" '
                f'href="{_blob(delta.repo_full_name, base_sha, path)}">{escape(path)}</a></li>'
            )
        out += [
            "</ul>",
            '<p class="note">A file enters or leaves this list when it starts or stops taking '
            "part in the import graph, which is not the same as being created or deleted: a "
            "file whose last import was removed leaves the graph while staying in the "
            "repository.</p>",
        ]

    if delta.rerouted_nodes:
        out += [
            "<h2>Files whose dependencies changed</h2>",
            "<p>These are the files to read first. Each one depends on something different "
            "than it did, which is where behaviour tends to move.</p>",
            "<ul>",
            *(
                f'<li><a class="mono" href="{_blob(delta.repo_full_name, head_sha, p)}">'
                f"{escape(p)}</a></li>"
                for p in delta.rerouted_nodes
            ),
            "</ul>",
        ]

    out += [
        "<h2>The graph at head</h2>",
        '<div class="panel figure">',
        svg,
        "</div>",
        '<p id="detail"></p>',
        '<ul class="key">',
        '<li><span class="k-add"></span>added at head</li>',
        '<li><span class="k-del"></span>removed since base</li>',
        '<li><span class="k-same"></span>unchanged</li>',
        "</ul>",
        '<p class="note">Dependencies that were removed are drawn back in so the change is '
        "visible; they do not exist at the head commit.</p>",
    ]

    out += _change_table(delta)

    out += [
        "<footer>",
        "<p>Both sides of this comparison were produced by parsing the repository at that "
        "commit and resolving each import to the file it points at. No language model took "
        "part in either one, so a difference reported here is a difference in the code, not a "
        "difference in how something was described.</p>",
        f"<p>Base <code>{escape(_short(base_sha))}</code>, head "
        f"<code>{escape(_short(head_sha))}</code>. Structure only: this says nothing about "
        "what changed inside a file that kept the same dependencies.</p>",
        "</footer>",
        "</main></body></html>",
    ]
    return "\n".join(out)
