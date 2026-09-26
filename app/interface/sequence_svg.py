"""
Compiles a CallSequence into SVG, deterministically.

A sequence diagram, in the ordinary sense: files are the participants,
each gets a lifeline, and every call is an arrow between two lifelines in
the order the calls appear in the source.

Nesting is drawn as activation bars rather than left to the reader. A
call made *inside* another call is the difference between "these things
happen" and "this happens because of that", and a flat list of arrows
cannot show it.

Same rules as the architecture compiler: integer coordinates, sorted
iteration, no layout library, and every arrow carries the file and line
it was resolved from so the picture stays checkable.
"""
from __future__ import annotations

from dataclasses import dataclass
from html import escape

LANE_MIN = 150
LANE_MAX = 260
CHAR_WIDTH = 7
LANE_PAD = 26
HEAD_HEIGHT = 34
ROW_HEIGHT = 46
TOP = 22
BOTTOM = 34
MARGIN = 24
ACTIVATION_WIDTH = 9
SELF_REACH = 38  # how far a same-file call loops out from its lifeline


@dataclass
class Lane:
    path: str
    label: str
    x: int  # centre of the lifeline
    width: int


def _label_for(path: str) -> str:
    parts = path.split("/")
    label = "/".join(parts[-2:]) if len(parts) > 1 else parts[0]
    return label if len(label) <= 30 else "…" + label[-29:]


def _activation_spans(steps) -> list[tuple[int, int]]:
    """
    For each step, the row it starts on and the row its callee stops
    being on the stack. A call's activation covers every deeper call made
    underneath it, which is exactly the run of following steps whose
    depth is greater than its own.
    """
    spans: list[tuple[int, int]] = []
    for i, step in enumerate(steps):
        end = i
        j = i + 1
        while j < len(steps) and steps[j].depth > step.depth:
            end = j
            j += 1
        spans.append((i, end))
    return spans


def render_sequence_svg(sequence) -> str:
    steps = sequence.steps
    if not steps:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 60" '
            'width="420" height="60" role="img" '
            'aria-label="No calls inside this function resolved to this repository">'
            '<text x="12" y="34" fill="var(--rp-ink-dim, #6b7280)" font-size="13" '
            'font-family="system-ui, sans-serif">'
            "Nothing this function calls resolves to a file in this repository."
            "</text></svg>"
        )

    participants = sequence.participants
    lanes: dict[str, Lane] = {}
    x = MARGIN
    for path in participants:
        label = _label_for(path)
        width = max(LANE_MIN, min(LANE_MAX, len(label) * CHAR_WIDTH + LANE_PAD * 2))
        lanes[path] = Lane(path=path, label=label, x=x + width // 2, width=width)
        x += width
    # A self-call draws its loop and label to the RIGHT of its lifeline,
    # so one on the last participant runs past the edge of a canvas sized
    # from the lanes alone. The label was being clipped mid-word.
    right_edge = x
    for step in steps:
        if step.from_file == step.to_file:
            label = f"{step.to_symbol}()" + ("  ↺" if step.is_recursive else "")
            caption = f"{step.from_file.rsplit('/', 1)[-1]}:{step.line}"
            longest = max(len(label), len(caption))
            right_edge = max(
                right_edge,
                lanes[step.to_file].x + SELF_REACH + 10 + longest * CHAR_WIDTH,
            )
    canvas_width = right_edge + MARGIN
    canvas_height = TOP + HEAD_HEIGHT + len(steps) * ROW_HEIGHT + BOTTOM

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {canvas_width} {canvas_height}" '
        f'width="{canvas_width}" height="{canvas_height}" class="rp-seq" role="img" '
        f'aria-label="Call sequence from {escape(sequence.entry_symbol, quote=True)}: '
        f'{len(steps)} calls across {len(participants)} files">',
        "<defs>"
        '<marker id="rp-seq-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0,0 L8,4 L0,8 z" fill="var(--rp-edge, #94a3b8)"/></marker>'
        "</defs>",
    ]

    # Lifelines first, so arrows and activations sit on top of them.
    parts.append('<g class="rp-lifelines">')
    line_top = TOP + HEAD_HEIGHT
    line_bottom = canvas_height - BOTTOM + 12
    for path in participants:
        lane = lanes[path]
        parts.append(
            f'<line x1="{lane.x}" y1="{line_top}" x2="{lane.x}" y2="{line_bottom}" '
            f'stroke="var(--rp-node-line, #cbd5e1)" stroke-width="1" '
            f'stroke-dasharray="3 4"></line>'
        )
    parts.append("</g>")

    # Activation bars: how long each callee is on the stack.
    parts.append('<g class="rp-activations">')
    for (start, end), step in zip(_activation_spans(steps), steps, strict=True):
        lane = lanes[step.to_file]
        y = TOP + HEAD_HEIGHT + start * ROW_HEIGHT + 4
        height = (end - start) * ROW_HEIGHT + ROW_HEIGHT - 14
        parts.append(
            f'<rect x="{lane.x - ACTIVATION_WIDTH // 2}" y="{y}" '
            f'width="{ACTIVATION_WIDTH}" height="{max(height, 12)}" rx="2" '
            f'fill="var(--rp-node-bg, #ffffff)" stroke="var(--rp-node-line, #cbd5e1)" '
            f'stroke-width="1"></rect>'
        )
    parts.append("</g>")

    # Participant headers.
    parts.append('<g class="rp-participants">')
    for path in participants:
        lane = lanes[path]
        box_w = lane.width - 16
        parts.append(
            f'<g class="rp-node" data-path="{escape(path, quote=True)}" '
            f'tabindex="0" role="button">'
            f'<rect x="{lane.x - box_w // 2}" y="{TOP}" width="{box_w}" '
            f'height="{HEAD_HEIGHT - 6}" rx="8" fill="var(--rp-node-bg, #ffffff)" '
            f'stroke="var(--rp-node-line, #cbd5e1)" stroke-width="1"></rect>'
            f'<text x="{lane.x}" y="{TOP + HEAD_HEIGHT // 2 + 2}" text-anchor="middle" '
            f'font-size="12" font-family="ui-monospace, SFMono-Regular, Menlo, monospace" '
            f'fill="var(--rp-ink, #0f172a)">{escape(lane.label)}</text>'
            f"<title>{escape(path)}</title></g>"
        )
    parts.append("</g>")

    # Messages.
    parts.append('<g class="rp-messages">')
    for i, step in enumerate(steps):
        src, dst = lanes[step.from_file], lanes[step.to_file]
        y = TOP + HEAD_HEIGHT + i * ROW_HEIGHT + ROW_HEIGHT // 2
        classes = "rp-msg" + (" rp-msg-recursive" if step.is_recursive else "")
        common = (
            f'data-from="{escape(step.from_file, quote=True)}" '
            f'data-to="{escape(step.to_file, quote=True)}" '
            f'data-symbol="{escape(step.to_symbol, quote=True)}" '
            f'data-caller="{escape(step.from_symbol, quote=True)}" '
            f'data-line="{step.line}" data-depth="{step.depth}"'
        )
        label = f"{step.to_symbol}()"
        if step.is_recursive:
            label += "  ↺"

        if src.x == dst.x:
            # A call inside the same file: a loop beside the lifeline,
            # since an arrow to itself would be a dot.
            reach = SELF_REACH
            path_d = (
                f"M{src.x + ACTIVATION_WIDTH // 2},{y - 10} "
                f"h{reach} v20 h{-reach}"
            )
            parts.append(
                f'<path class="{classes}" d="{path_d}" fill="none" '
                f'stroke="var(--rp-edge, #94a3b8)" stroke-width="1.4" '
                f'marker-end="url(#rp-seq-arrow)" {common}></path>'
            )
            text_x, anchor = src.x + reach + 10, "start"
        else:
            forward = dst.x > src.x
            x1 = src.x + (ACTIVATION_WIDTH // 2 if forward else -ACTIVATION_WIDTH // 2)
            x2 = dst.x - (ACTIVATION_WIDTH // 2 if forward else -ACTIVATION_WIDTH // 2)
            parts.append(
                f'<line class="{classes}" x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" '
                f'stroke="var(--rp-edge, #94a3b8)" stroke-width="1.4" '
                f'marker-end="url(#rp-seq-arrow)" {common}></line>'
            )
            text_x, anchor = (x1 + x2) // 2, "middle"

        parts.append(
            f'<text class="rp-msg-label" x="{text_x}" y="{y - 7}" text-anchor="{anchor}" '
            f'font-size="11.5" font-family="ui-monospace, SFMono-Regular, Menlo, monospace" '
            f'fill="var(--rp-ink, #0f172a)">{escape(label)}</text>'
        )
        parts.append(
            f'<text class="rp-msg-line" x="{text_x}" y="{y + 13}" text-anchor="{anchor}" '
            f'font-size="10" font-family="ui-monospace, SFMono-Regular, Menlo, monospace" '
            f'fill="var(--rp-ink-dim, #6b7280)">{escape(step.from_file.rsplit("/", 1)[-1])}'
            f':{step.line}</text>'
        )
        # A thin transparent band makes the whole row clickable; a 1.4px
        # line is not a target.
        parts.append(
            f'<rect class="rp-msg-hit" x="0" y="{y - ROW_HEIGHT // 2}" '
            f'width="{canvas_width}" height="{ROW_HEIGHT}" fill="transparent" '
            f'tabindex="0" role="button" {common}>'
            f"<title>{escape(step.from_symbol)} calls {escape(step.to_symbol)} at "
            f"{escape(step.from_file)}:{step.line}</title></rect>"
        )
    parts.append("</g></svg>")
    return "".join(parts)
