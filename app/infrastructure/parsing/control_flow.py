"""
Control flow of one function, read off the syntax tree.

The third diagram type, and the one people picture when they say
"flowchart": boxes for steps, diamonds for decisions, a Yes and a No.

It is derivable the same way the other two are. tree-sitter already
produces the exact shape a flowchart wants — an `if_statement` with a
condition, a consequence and an alternative IS a diamond with two
branches — so nothing here is inferred, and a model is not involved at
any point.

Node type names were read off real parse trees for each language rather
than assumed. They turned out to agree almost everywhere; Go is the
exception, keeping its else on a field instead of a separate clause node.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Statement types that become a shape. Anything not listed is a plain
# step, which keeps a flowchart about decisions rather than about every
# assignment in the function.
# Grammars disagree on the name. Ruby calls it `if`, Rust `if_expression`;
# both were invisible to a check for "if_statement" alone.
_IF = ("if_statement", "if_expression", "if", "elsif", "conditional_expression")
_RETURN = ("return_statement",)
_LOOPS = (
    "for_statement",
    "for_in_statement",
    "foreach_statement",
    "while_statement",
    "do_statement",
    "for_range_loop",
)
# Where the branches hang off an if. Go has no else_clause node; it puts
# the else on this field, and so does every other grammar checked.
_CONSEQUENCE_FIELDS = ("consequence", "body")
_ALTERNATIVE_FIELD = "alternative"
_CONDITION_FIELDS = ("condition", "value")

# Wrappers that hold statements without being one. Go puts a
# statement_list inside its block; Ruby uses body_statement. Missing one
# makes a whole body collapse into a single unreadable step.
_BLOCKS = (
    "block",
    "statement_list",
    "statement_block",
    "function_body",
    "else_clause",
    "compound_statement",
    "body_statement",
    "do_block",
)

# Dart exposes no condition field at all: the test is simply the first
# named child, ahead of the consequence block. Anything that is not a
# block is the condition.
_NOT_A_CONDITION = _BLOCKS + ("then", "else", "else_clause")

MAX_STEPS = 40  # a flowchart past this is a wall, not a diagram


@dataclass
class Step:
    """One plain step: a call, an assignment, anything without a branch."""
    label: str
    line: int


@dataclass
class Decision:
    """An if. `yes` runs when the condition holds, `no` when it does not."""
    label: str
    line: int
    yes: list = field(default_factory=list)
    no: list = field(default_factory=list)


@dataclass
class Loop:
    """A for or while. `body` repeats while the condition holds."""
    label: str
    line: int
    body: list = field(default_factory=list)


@dataclass
class Return:
    """A return, which ends the flow on this path."""
    label: str
    line: int


def _text(node, limit: int = 46) -> str:
    raw = " ".join(node.text.decode("utf-8", "replace").split())
    return raw if len(raw) <= limit else raw[: limit - 1] + "…"


def _child_by_fields(node, fields):
    for name in fields:
        found = node.child_by_field_name(name)
        if found is not None:
            return found
    return None


def _first_condition(node):
    """The first child that is not a body, for grammars with no condition field."""
    for child in node.named_children:
        if child.type not in _NOT_A_CONDITION:
            return child
    return None


def _statements(node):
    """
    The statements directly inside a block, skipping the block wrappers
    themselves so nesting does not add an empty level per brace.
    """
    if node is None:
        return []
    if node.type in _BLOCKS:
        out = []
        for child in node.named_children:
            out.extend(_statements(child))
        return out
    # Rust (and others) wrap a control-flow expression in a statement.
    # Left wrapped, an `if` reads as a plain step and its branches vanish.
    if node.type == "expression_statement" and len(node.named_children) == 1:
        inner = node.named_children[0]
        if inner.type in _IF or inner.type in _LOOPS:
            return [inner]
    return [node]


def _first_call_label(node) -> str | None:
    """
    A short label for a plain statement: the call it makes, if it makes
    one. `final user = await getProfile(id)` reads better as `getProfile`
    than as the whole line.
    """
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in ("call", "call_expression", "method_invocation",
                            "function_call_expression", "argument_part"):
            callee = (
                current.child_by_field_name("function")
                or current.child_by_field_name("name")
                or current.child_by_field_name("method")
            )
            if callee is not None:
                return callee.text.decode("utf-8", "replace").rsplit(".", 1)[-1] + "()"
            return None
        stack.extend(reversed(current.children))
    return None


def build_flow(body_node) -> tuple[list, bool]:
    """
    Returns (items, truncated) for a function body.

    Flat statements collapse into steps; ifs and loops keep their shape.
    """
    budget = [MAX_STEPS]

    def walk(statements) -> list:
        items: list = []
        for statement in statements:
            if budget[0] <= 0:
                return items
            budget[0] -= 1
            line = statement.start_point[0] + 1

            if statement.type in _IF:
                condition = _child_by_fields(statement, _CONDITION_FIELDS) or _first_condition(statement)
                decision = Decision(
                    label=_text(condition) if condition is not None else "condition",
                    line=line,
                    yes=walk(_statements(_child_by_fields(statement, _CONSEQUENCE_FIELDS))),
                    no=walk(_statements(statement.child_by_field_name(_ALTERNATIVE_FIELD))),
                )
                items.append(decision)
            elif statement.type in _LOOPS:
                condition = _child_by_fields(statement, _CONDITION_FIELDS) or _first_condition(statement)
                items.append(
                    Loop(
                        label=_text(condition) if condition is not None else "each item",
                        line=line,
                        body=walk(_statements(statement.child_by_field_name("body"))),
                    )
                )
            elif statement.type in _RETURN:
                items.append(Return(label=_text(statement, 34), line=line))
            else:
                items.append(Step(label=_first_call_label(statement) or _text(statement), line=line))
        return items

    flow = walk(_statements(body_node))
    return flow, budget[0] <= 0


def _find_all(node, node_type: str) -> list:
    hits = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == node_type:
            hits.append(current)
        stack.extend(current.children)
    return hits


def _body_of(node):
    """A definition's body, whether it is a field or the next sibling (Dart)."""
    body = node.child_by_field_name("body")
    if body is not None:
        return body
    sibling = node.next_named_sibling
    if sibling is not None and sibling.type in ("function_body", "block"):
        return sibling
    return None


def _names(node) -> str | None:
    """The declared name of a definition node, by field or first identifier."""
    named = node.child_by_field_name("name")
    if named is not None:
        return named.text.decode("utf-8", "replace")
    for child in node.named_children:
        if child.type == "identifier":
            return child.text.decode("utf-8", "replace")
        if child.type in ("function_signature", "getter_signature", "setter_signature"):
            for inner in child.named_children:
                if inner.type == "identifier":
                    return inner.text.decode("utf-8", "replace")
    return None


def flow_for_symbol(*, content: str, language: str, symbol: str, start_line: int):
    """
    The control flow of one named function, or None if it cannot be found.

    Takes the symbol's start line as well as its name because a file can
    define the same name twice; the line is what the rest of the product
    already knows about a symbol, so it is the cheapest way to be exact.
    """
    from tree_sitter_language_pack import get_parser

    try:
        tree = get_parser(language).parse(content.encode("utf-8"))
    except Exception:  # noqa: BLE001 - an unsupported language is not an error here
        return None

    best = None
    best_body = None
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.is_named and node.start_point[0] + 1 == start_line and _names(node) == symbol:
            # The candidate must own a body. Without that check the file's
            # own root node matched too — it starts on line 1 and its first
            # child is the definition, so it answered to the name as well,
            # and being the largest it won.
            body = _body_of(node)
            if body is not None and (
                best is None or node.end_byte - node.start_byte > best.end_byte - best.start_byte
            ):
                best, best_body = node, body
        stack.extend(node.children)

    if best_body is None:
        return None
    return build_flow(best_body)


def demo() -> None:
    """Self-check: the shapes a flowchart needs, in four languages."""
    from tree_sitter_language_pack import get_parser

    cases = {
        "python": (b"def f(x):\n    setup()\n    if x > 1:\n        go()\n    else:\n        stop()\n    return 0\n", "function_definition"),
        "javascript": (b"function f(x){ setup(); if(x>1){go();}else{stop();} return 0; }", "function_declaration"),
        "java": (b"class C{ int f(int x){ setup(); if(x>1){go();}else{stop();} return 0; } }", "method_declaration"),
        "go": (b"package m\nfunc f(x int) int { setup(); if x>1 { go1() } else { stop() }\n return 0 }", "function_declaration"),
    }
    for lang, (src, def_type) in cases.items():
        tree = get_parser(lang).parse(src)
        found = _find_all(tree.root_node, def_type)
        assert found, f"{lang}: no {def_type}"
        body = found[0].child_by_field_name("body") or found[0].next_named_sibling
        flow, _ = build_flow(body)
        kinds = [type(i).__name__ for i in flow]
        assert "Decision" in kinds, f"{lang}: no decision found in {kinds}"
        assert "Return" in kinds, f"{lang}: no return found in {kinds}"
        decision = next(i for i in flow if isinstance(i, Decision))
        assert decision.yes and decision.no, f"{lang}: branch missing {decision}"
        print(f"  {lang:11s} {kinds}  yes={[type(i).__name__ for i in decision.yes]} "
              f"no={[type(i).__name__ for i in decision.no]}")
    print("control_flow: ok")


if __name__ == "__main__":
    demo()
