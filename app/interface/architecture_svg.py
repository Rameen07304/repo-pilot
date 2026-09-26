"""
Compiles an ArchitectureExport into SVG, deterministically.

Nothing here draws a graph library. The exported document has to open
with no network and no JavaScript dependency, years from now, so the
geometry is computed and written out as plain SVG: the same commit always
produces byte-identical output, which also means two exports can be
diffed to see what actually changed in a repository's structure.

The layout is layered (Sugiyama's first three phases, kept small):

  1. Break cycles — a circular import is real and must still be drawn, so
     back edges are found and reversed for layering only, then drawn
     dashed in their true direction.
  2. Assign layers — longest path from the sources, so a file sits below
     everything it depends on.
  3. Order within each layer — a few barycentre sweeps, which is enough
     to stop most crossings without a real crossing-minimisation pass.

Every coordinate is integer, and every iteration is over a sorted
collection, because "deterministic" is a property this module is relied
on for rather than a nice-to-have.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape

# Geometry. Chosen so a 40-node graph fits a laptop screen at 100%.
NODE_HEIGHT = 34
NODE_MIN_WIDTH = 96
NODE_MAX_WIDTH = 240
CHAR_WIDTH = 7  # approximate advance of the label font at 12px
MAX_LABEL_CHARS = 28  # what fits inside NODE_MAX_WIDTH at CHAR_WIDTH
NODE_PAD = 18
COLUMN_GAP = 26
ROW_GAP = 96
SUBROW_GAP = 52  # between wrapped halves of one layer, tighter than between layers
MARGIN = 28
# A layer wider than this wraps onto further rows. Roughly the usable
# width of the document's own figure panel on a laptop, so the common
# case needs no horizontal scrolling at all.
MAX_ROW_WIDTH = 1040


@dataclass
class PlacedNode:
    path: str
    label: str
    layer: int
    x: int
    y: int
    width: int

    @property
    def center_x(self) -> int:
        return self.x + self.width // 2

    @property
    def bottom(self) -> int:
        return self.y + NODE_HEIGHT


@dataclass
class PlacedEdge:
    from_path: str
    to_path: str
    lines: list[int]
    # True when this edge is the one that closes a cycle, so it is drawn
    # dashed and reported. Which edge of a cycle gets the mark depends on
    # the traversal; that a cycle exists does not.
    is_cycle: bool


@dataclass
class Layout:
    nodes: list[PlacedNode] = field(default_factory=list)
    edges: list[PlacedEdge] = field(default_factory=list)
    width: int = 0
    height: int = 0

    @property
    def cycle_edges(self) -> list[PlacedEdge]:
        return [e for e in self.edges if e.is_cycle]


def _label_for(path: str) -> str:
    """
    Always the file name with its parent folder, never the bare name.

    A rule that showed the folder only for short or ambiguous names left
    one box reading 'app/db.py' beside another reading 'routes.py', which
    invites the reader to think the second one sits at the repository
    root. Consistency is worth more than the few pixels; the full path is
    on the node's tooltip and in its data-path either way.
    """
    parts = path.split("/")
    label = "/".join(parts[-2:]) if len(parts) > 1 else parts[0]
    if len(label) > MAX_LABEL_CHARS:
        # Trim from the left: the file name matters more than the folder.
        label = "…" + label[-(MAX_LABEL_CHARS - 1):]
    return label


def _node_width(label: str) -> int:
    return max(NODE_MIN_WIDTH, min(NODE_MAX_WIDTH, len(label) * CHAR_WIDTH + NODE_PAD * 2))


def _find_back_edges(
    nodes: list[str], edges: list[tuple[str, str]]
) -> set[tuple[str, str]]:
    """
    Back edges of a depth-first traversal — the edges that make the graph
    cyclic. Iterative on purpose: a deep dependency chain in a large
    repository will exceed the recursion limit.
    """
    out: dict[str, list[str]] = {n: [] for n in nodes}
    for src, dst in edges:
        out[src].append(dst)
    for src in out:
        out[src].sort()

    WHITE, GREY, BLACK = 0, 1, 2
    colour = {n: WHITE for n in nodes}
    back: set[tuple[str, str]] = set()

    for root in nodes:
        if colour[root] != WHITE:
            continue
        # Each frame is (node, index of the next child to visit).
        stack: list[list] = [[root, 0]]
        colour[root] = GREY
        while stack:
            node, i = stack[-1]
            if i < len(out[node]):
                stack[-1][1] += 1
                child = out[node][i]
                if colour[child] == GREY:
                    back.add((node, child))  # closes a cycle
                elif colour[child] == WHITE:
                    colour[child] = GREY
                    stack.append([child, 0])
            else:
                colour[node] = BLACK
                stack.pop()
    return back


def _assign_layers(
    nodes: list[str], edges: list[tuple[str, str]]
) -> dict[str, int]:
    """
    Longest path from the sources over the acyclic edges: a node sits one
    layer below the deepest thing that imports it.
    """
    incoming: dict[str, list[str]] = {n: [] for n in nodes}
    outgoing: dict[str, list[str]] = {n: [] for n in nodes}
    for src, dst in edges:
        outgoing[src].append(dst)
        incoming[dst].append(src)

    layer = {n: 0 for n in nodes}
    indegree = {n: len(incoming[n]) for n in nodes}
    queue = sorted(n for n in nodes if indegree[n] == 0)
    while queue:
        node = queue.pop(0)
        for child in sorted(outgoing[node]):
            if layer[child] < layer[node] + 1:
                layer[child] = layer[node] + 1
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
        queue.sort()
    return layer


def _order_layers(
    layers: dict[int, list[str]], edges: list[tuple[str, str]], sweeps: int = 4
) -> dict[int, list[str]]:
    """
    Barycentre ordering: repeatedly place each node at the average
    position of its neighbours in the adjacent layer. Cheap, and enough to
    untangle the common case of a few hub modules.
    """
    neighbours_up: dict[str, list[str]] = {}
    neighbours_down: dict[str, list[str]] = {}
    for src, dst in edges:
        neighbours_up.setdefault(dst, []).append(src)
        neighbours_down.setdefault(src, []).append(dst)

    ordered = {depth: list(members) for depth, members in layers.items()}
    depths = sorted(ordered)

    for sweep in range(sweeps):
        downward = sweep % 2 == 0
        sequence = depths if downward else list(reversed(depths))
        for depth in sequence:
            reference = depth - 1 if downward else depth + 1
            if reference not in ordered:
                continue
            index = {name: i for i, name in enumerate(ordered[reference])}
            source = neighbours_up if downward else neighbours_down
            here = ordered[depth]
            # Keys are computed up front rather than inside a sort key: a
            # node with no neighbour in the reference layer keeps its
            # current position instead of drifting to the left edge, and
            # reading that position needs the ordering as it was before
            # this sweep started.
            keys: dict[str, tuple[float, str]] = {}
            for position, node in enumerate(here):
                positions = [index[n] for n in source.get(node, []) if n in index]
                barycentre = (
                    sum(positions) / len(positions) if positions else float(position)
                )
                keys[node] = (barycentre, node)
            ordered[depth] = sorted(here, key=keys.__getitem__)
    return ordered


def build_layout(export) -> Layout:
    """Place every node and edge of an ArchitectureExport on a grid."""
    nodes = sorted(export.nodes)
    if not nodes:
        return Layout(width=MARGIN * 2, height=MARGIN * 2)

    pairs = [(e.from_path, e.to_path) for e in export.edges_to_draw]
    back_edges = _find_back_edges(nodes, pairs)
    acyclic = [p for p in pairs if p not in back_edges]
    layer_of = _assign_layers(nodes, acyclic)

    by_layer: dict[int, list[str]] = {}
    for node in nodes:
        by_layer.setdefault(layer_of[node], []).append(node)
    by_layer = _order_layers(by_layer, acyclic)

    widths = {n: _node_width(_label_for(n)) for n in nodes}

    # A layer with thirty files in it would draw one row several thousand
    # pixels wide and a few hundred tall — unreadable, and unprintable.
    # Wrapping keeps the layering (everything here still sits below what
    # it depends on) while giving the figure a sane aspect ratio.
    rows: list[tuple[int, list[str]]] = []
    for depth in sorted(by_layer):
        current: list[str] = []
        used = 0
        for name in by_layer[depth]:
            addition = widths[name] + (COLUMN_GAP if current else 0)
            if current and used + addition > MAX_ROW_WIDTH:
                rows.append((depth, current))
                current, used = [name], widths[name]
            else:
                current.append(name)
                used += addition
        if current:
            rows.append((depth, current))

    def row_width(members: list[str]) -> int:
        return sum(widths[n] for n in members) + COLUMN_GAP * (len(members) - 1)

    canvas_width = max(row_width(members) for _, members in rows) + MARGIN * 2

    placed: list[PlacedNode] = []
    y = MARGIN
    previous_depth = rows[0][0]
    for depth, members in rows:
        if placed:
            y += SUBROW_GAP if depth == previous_depth else ROW_GAP
        previous_depth = depth
        x = (canvas_width - row_width(members)) // 2
        for name in members:
            placed.append(
                PlacedNode(
                    path=name,
                    label=_label_for(name),
                    layer=depth,
                    x=x,
                    y=y,
                    width=widths[name],
                )
            )
            x += widths[name] + COLUMN_GAP

    return Layout(
        nodes=placed,
        edges=[
            PlacedEdge(
                from_path=e.from_path,
                to_path=e.to_path,
                lines=list(e.lines),
                is_cycle=(e.from_path, e.to_path) in back_edges,
            )
            for e in export.edges_to_draw
        ],
        width=canvas_width,
        height=max(n.bottom for n in placed) + MARGIN,
    )


def _edge_path(src: PlacedNode, dst: PlacedNode) -> str:
    """
    A cubic curve between two boxes, leaving and entering vertically so an
    arrow head reads as a direction rather than a collision.
    """
    x1, y1 = src.center_x, src.bottom
    x2, y2 = dst.center_x, dst.y
    if y2 < y1:  # drawn upward: a cycle, or a same-layer dependency
        x1, y1 = src.center_x, src.y
        x2, y2 = dst.center_x, dst.bottom
    lift = max(24, abs(y2 - y1) // 2)
    direction = 1 if y2 > y1 else -1
    return f"M{x1},{y1} C{x1},{y1 + lift * direction} {x2},{y2 - lift * direction} {x2},{y2}"


def render_architecture_svg(
    export,
    *,
    edge_status: dict[tuple[str, str], str] | None = None,
    node_status: dict[str, str] | None = None,
) -> str:
    """
    The graph as SVG. Colours come from CSS custom properties so the host
    page themes it; the geometry does not depend on the host at all.

    Every edge carries data-from, data-to and data-lines. That is the
    export's whole argument: the picture is checkable, edge by edge,
    without the tool that drew it.

    `edge_status` and `node_status` tag parts of the graph with a word
    such as "added" or "removed". They add a class and a data attribute
    and change nothing else: the delta view is the same drawing with the
    changed parts marked, so the two documents cannot disagree about a
    repository's shape.
    """
    edge_status = edge_status or {}
    node_status = node_status or {}
    layout = build_layout(export)
    if not layout.nodes:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 60" '
            'width="320" height="60" role="img" '
            'aria-label="No internal dependencies were resolved">'
            '<text x="12" y="34" fill="var(--rp-ink-dim, #6b7280)" font-size="13" '
            'font-family="system-ui, sans-serif">'
            "No internal dependencies were resolved.</text></svg>"
        )

    by_path = {n.path: n for n in layout.nodes}
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {layout.width} {layout.height}" '
        f'width="{layout.width}" height="{layout.height}" class="rp-arch" role="img" '
        f'aria-label="Dependency graph of {escape(export.repo_full_name, quote=True)}: '
        f'{len(layout.nodes)} files, {len(layout.edges)} dependencies">',
        "<defs>"
        '<marker id="rp-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0,0 L8,4 L0,8 z" fill="var(--rp-edge, #94a3b8)"/></marker>'
        "</defs>",
        '<g class="rp-edges" fill="none">',
    ]

    for edge in layout.edges:
        src, dst = by_path.get(edge.from_path), by_path.get(edge.to_path)
        if src is None or dst is None:
            continue
        d = _edge_path(src, dst)
        lines = ",".join(str(n) for n in edge.lines)
        status = edge_status.get((edge.from_path, edge.to_path), "")
        classes = "rp-edge" + (" rp-edge-cycle" if edge.is_cycle else "")
        if status:
            classes += f" rp-edge-{status}"
        common = (
            f'data-from="{escape(edge.from_path, quote=True)}" '
            f'data-to="{escape(edge.to_path, quote=True)}" '
            f'data-lines="{lines}"'
            + (f' data-status="{escape(status, quote=True)}"' if status else "")
        )
        parts.append(
            f'<path class="{classes}" d="{d}" stroke="var(--rp-edge, #94a3b8)" '
            f'stroke-width="1.4" marker-end="url(#rp-arrow)" {common}></path>'
        )
        # A 1.4px curve is not a click target. This invisible twin is.
        parts.append(
            f'<path class="rp-edge-hit" d="{d}" stroke="transparent" stroke-width="12" '
            f'tabindex="0" role="button" {common}>'
            f"<title>{escape(edge.from_path)} imports {escape(edge.to_path)}"
            + (f" (line {', '.join(str(n) for n in edge.lines)})" if edge.lines else "")
            + "</title></path>"
        )

    parts.append("</g>")
    parts.append('<g class="rp-nodes">')
    for node in layout.nodes:
        status = node_status.get(node.path, "")
        parts.append(
            f'<g class="rp-node{f" rp-node-{status}" if status else ""}" '
            f'data-path="{escape(node.path, quote=True)}" '
            + (f'data-status="{escape(status, quote=True)}" ' if status else "")
            + f'tabindex="0" role="button">'
            f'<rect x="{node.x}" y="{node.y}" width="{node.width}" height="{NODE_HEIGHT}" '
            f'rx="8" fill="var(--rp-node-bg, #ffffff)" stroke="var(--rp-node-line, #cbd5e1)" '
            f'stroke-width="1"></rect>'
            f'<text x="{node.center_x}" y="{node.y + NODE_HEIGHT // 2 + 4}" '
            f'text-anchor="middle" font-size="12" '
            f'font-family="ui-monospace, SFMono-Regular, Menlo, monospace" '
            f'fill="var(--rp-ink, #0f172a)">{escape(node.label)}</text>'
            f"<title>{escape(node.path)}</title></g>"
        )
    parts.append("</g></svg>")
    return "".join(parts)
