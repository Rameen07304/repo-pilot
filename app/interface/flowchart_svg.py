"""
Draws a control flow as a flowchart: the shape people mean by the word.

A vertical spine with branches to the right, which is how flowcharts are
drawn by hand and the only layout that stays readable without a graph
engine. The main path goes straight down; a decision's No branch steps
out one column, runs, and rejoins below.

Deterministic like the other two renderers: integer coordinates, no
layout library, same drawing for the same commit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape

from app.infrastructure.parsing.control_flow import Decision, Loop, Return, Step

BOX_W = 190
BOX_H = 46
DIAMOND_W = 210
DIAMOND_H = 76
GAP_Y = 34
COLUMN = 250
MARGIN = 34
TERMINAL_W = 120
TERMINAL_H = 40


@dataclass
class Shape:
    kind: str  # start | end | step | decision | loop | return
    label: str
    line: int
    x: int  # centre
    y: int  # top
    w: int
    h: int

    @property
    def cx(self) -> int:
        return self.x

    @property
    def bottom(self) -> int:
        return self.y + self.h


@dataclass
class Link:
    x1: int
    y1: int
    x2: int
    y2: int
    label: str = ""
    elbow: bool = False  # route around instead of straight down


@dataclass
class Chart:
    shapes: list[Shape] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    width: int = 0
    height: int = 0


def _size(kind: str) -> tuple[int, int]:
    if kind in ("decision", "loop"):
        return DIAMOND_W, DIAMOND_H
    if kind in ("start", "end"):
        return TERMINAL_W, TERMINAL_H
    return BOX_W, BOX_H


class _Layout:
    """
    Places shapes on a grid. `column` is how far right a branch has
    stepped; the main path stays at column 0 so the eye can follow it
    straight down.
    """

    def __init__(self) -> None:
        self.chart = Chart()
        self.max_x = 0

    def place(self, kind: str, label: str, line: int, column: int, y: int) -> Shape:
        w, h = _size(kind)
        x = MARGIN + column * COLUMN + BOX_W // 2
        shape = Shape(kind=kind, label=label, line=line, x=x, y=y, w=w, h=h)
        self.chart.shapes.append(shape)
        self.max_x = max(self.max_x, x + w // 2)
        return shape

    def link(self, top: Shape, bottom: Shape, label: str = "") -> None:
        self.chart.links.append(
            Link(
                x1=top.cx, y1=top.bottom, x2=bottom.cx, y2=bottom.y,
                label=label, elbow=top.cx != bottom.cx,
            )
        )

    def run(self, items: list, column: int, y: int, entries: list[Shape]) -> tuple[int, list[Shape]]:
        """
        Lays out a list of items, returning the y below them and the
        shapes that flow onward. `entries` are the shapes whose exit
        should connect into the first item here.
        """
        for item in items:
            if isinstance(item, Decision):
                node = self.place("decision", item.label, item.line, column, y)
                for entry in entries:
                    self.link(entry, node)
                y = node.bottom + GAP_Y

                yes_y, yes_exits = self.run(item.yes, column, y, [])
                no_y, no_exits = self.run(item.no, column + 1, y, [])

                # The branch shapes connect to the diamond, labelled.
                for exits, label, taken in ((item.yes, "yes", yes_exits), (item.no, "no", no_exits)):
                    if exits:
                        first = self._first_at(exits, taken)
                        if first is not None:
                            self.chart.links.append(
                                Link(node.cx, node.bottom, first.cx, first.y, label, node.cx != first.cx)
                            )
                entries = []
                if item.yes:
                    entries.extend(yes_exits)
                else:
                    entries.append(node)  # no then-branch: falls straight through
                if item.no:
                    entries.extend(no_exits)
                else:
                    entries.append(node)
                y = max(yes_y, no_y)

            elif isinstance(item, Loop):
                node = self.place("loop", item.label, item.line, column, y)
                for entry in entries:
                    self.link(entry, node)
                y = node.bottom + GAP_Y
                body_y, body_exits = self.run(item.body, column + 1, y, [])
                if item.body:
                    first = self._first_at(item.body, body_exits)
                    if first is not None:
                        self.chart.links.append(
                            Link(node.cx, node.bottom, first.cx, first.y, "each", True)
                        )
                    # back edge: the body returns to the test
                    for exit_shape in body_exits:
                        self.chart.links.append(
                            Link(exit_shape.cx, exit_shape.bottom, node.cx, node.y, "repeat", True)
                        )
                y = max(y, body_y)
                entries = [node]

            elif isinstance(item, Return):
                node = self.place("return", item.label, item.line, column, y)
                for entry in entries:
                    self.link(entry, node)
                y = node.bottom + GAP_Y
                entries = []  # a return ends this path

            elif isinstance(item, Step):
                node = self.place("step", item.label, item.line, column, y)
                for entry in entries:
                    self.link(entry, node)
                y = node.bottom + GAP_Y
                entries = [node]

        return y, entries

    def _first_at(self, items: list, exits: list[Shape]) -> Shape | None:
        """The first shape drawn for a branch, which is what the diamond points at."""
        if not items:
            return None
        # Shapes are appended in order, so the first one placed after the
        # diamond for this branch is the branch's entry.
        for shape in self.chart.shapes:
            if shape.line == getattr(items[0], "line", None):
                return shape
        return exits[0] if exits else None


def build_chart(flow: list) -> Chart:
    layout = _Layout()
    start = layout.place("start", "start", 0, 0, MARGIN)
    y, exits = layout.run(flow, 0, start.bottom + GAP_Y, [start])
    end = layout.place("end", "end", 0, 0, y)
    for exit_shape in exits:
        layout.link(exit_shape, end)
    chart = layout.chart
    chart.width = layout.max_x + MARGIN
    chart.height = end.bottom + MARGIN
    return chart


def _shape_svg(shape: Shape) -> str:
    x, y, w, h = shape.x - shape.w // 2, shape.y, shape.w, shape.h
    label = escape(shape.label)
    common = (
        f'data-line="{shape.line}" data-kind="{escape(shape.kind, quote=True)}" '
        f'tabindex="0" role="button"'
    )
    if shape.kind in ("decision", "loop"):
        cx, cy = shape.x, y + h // 2
        points = f"{cx},{y} {x + w},{cy} {cx},{y + h} {x},{cy}"
        body = (
            f'<polygon points="{points}" fill="var(--rp-node-bg, #fff)" '
            f'stroke="var(--rp-accent, #0B5FD0)" stroke-width="1.5"></polygon>'
        )
    elif shape.kind in ("start", "end"):
        body = (
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{h // 2}" '
            f'fill="var(--rp-accent, #0B5FD0)" stroke="none"></rect>'
        )
    elif shape.kind == "return":
        body = (
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" '
            f'fill="var(--rp-node-bg, #fff)" stroke="var(--rp-added, #0B7A43)" '
            f'stroke-width="1.6"></rect>'
        )
    else:
        body = (
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" '
            f'fill="var(--rp-node-bg, #fff)" stroke="var(--rp-node-line, #cbd5e1)" '
            f'stroke-width="1"></rect>'
        )

    fill = "var(--rp-accent-ink, #fff)" if shape.kind in ("start", "end") else "var(--rp-ink, #0f172a)"
    # Two lines for anything long, so a condition is not clipped.
    words, lines, current = label.split(" "), [], ""
    per_line = 24 if shape.kind in ("decision", "loop") else 26
    for word in words:
        if len(current) + len(word) + 1 > per_line and current:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    lines = lines[:2]
    start_y = y + h // 2 + 4 - (len(lines) - 1) * 8
    text = "".join(
        f'<text x="{shape.x}" y="{start_y + i * 16}" text-anchor="middle" font-size="12" '
        f'font-family="ui-monospace, SFMono-Regular, Menlo, monospace" fill="{fill}">{ln}</text>'
        for i, ln in enumerate(lines)
    )
    title = f"<title>{label} · line {shape.line}</title>" if shape.line else ""
    return f'<g class="rp-flow-shape rp-flow-{shape.kind}" {common}>{body}{text}{title}</g>'


def _link_svg(link: Link) -> str:
    if link.elbow:
        mid = link.y1 + GAP_Y // 2
        d = f"M{link.x1},{link.y1} V{mid} H{link.x2} V{link.y2}"
    else:
        d = f"M{link.x1},{link.y1} V{link.y2}"
    out = (
        f'<path class="rp-flow-link" d="{d}" fill="none" stroke="var(--rp-edge, #94a3b8)" '
        f'stroke-width="1.4" marker-end="url(#rp-flow-arrow)"></path>'
    )
    if link.label:
        lx = (link.x1 + link.x2) // 2 + (10 if link.elbow else 0)
        ly = link.y1 + GAP_Y // 2 - 4
        out += (
            f'<text class="rp-flow-label" x="{lx}" y="{ly}" text-anchor="middle" font-size="11" '
            f'font-family="ui-monospace, SFMono-Regular, Menlo, monospace" '
            f'fill="var(--rp-ink-dim, #6b7280)">{escape(link.label)}</text>'
        )
    return out


def render_flowchart_svg(flow: list, *, symbol: str = "") -> str:
    if not flow:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 460 60" width="460" '
            'height="60" role="img" aria-label="No control flow found">'
            '<text x="12" y="34" fill="var(--rp-ink-dim, #6b7280)" font-size="13" '
            'font-family="system-ui, sans-serif">'
            "This function has no branches or loops to chart.</text></svg>"
        )
    chart = build_chart(flow)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {chart.width} {chart.height}" '
        f'width="{chart.width}" height="{chart.height}" class="rp-flow" role="img" '
        f'aria-label="Flowchart of {escape(symbol, quote=True)}: {len(chart.shapes)} steps">',
        '<defs><marker id="rp-flow-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0,0 L8,4 L0,8 z" fill="var(--rp-edge, #94a3b8)"/></marker></defs>',
        '<g class="rp-flow-links">',
        *(_link_svg(link) for link in chart.links),
        "</g><g class='rp-flow-shapes'>",
        *(_shape_svg(shape) for shape in chart.shapes),
        "</g></svg>",
    ]
    return "".join(parts)
