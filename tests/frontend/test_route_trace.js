// Route tracing: the shortest dependency chain between two files.
//
// The chain ends up on a share card in a pull request, so two properties
// matter more than cleverness: it must be the same chain every time for
// the same commit, and every hop must carry the line it came from.
//
// Run with:  node --test tests/frontend/

const test = require("node:test");
const assert = require("node:assert");
const { trace } = require("../../app/interface/static/route_trace");

function edges(...triples) {
    return triples.map(([from, to, lines]) => ({ from, to, lines: lines || [] }));
}

const CHAIN = edges(
    ["app/main.py", "app/routes.py", [3]],
    ["app/routes.py", "app/service.py", [5]],
    ["app/service.py", "app/db.py", [1, 9]],
);

test("route: a direct dependency is a single hop", () => {
    const r = trace(CHAIN, "app/main.py", "app/routes.py");
    assert.equal(r.found, true);
    assert.equal(r.hops.length, 1);
    assert.deepEqual(r.hops[0], { from: "app/main.py", to: "app/routes.py", lines: [3] });
});

test("route: every hop of a longer chain keeps its own import line", () => {
    const r = trace(CHAIN, "app/main.py", "app/db.py");
    assert.equal(r.found, true);
    assert.deepEqual(
        r.hops.map((h) => [h.from, h.to, h.lines]),
        [
            ["app/main.py", "app/routes.py", [3]],
            ["app/routes.py", "app/service.py", [5]],
            ["app/service.py", "app/db.py", [1, 9]],
        ],
    );
});

test("route: the shortest chain wins over a longer one", () => {
    const g = edges(
        ["a", "b", [1]], ["b", "c", [2]], ["c", "d", [3]],
        ["a", "d", [9]],
    );
    const r = trace(g, "a", "d");
    assert.equal(r.hops.length, 1);
    assert.deepEqual(r.hops[0].lines, [9]);
});

test("route: other routes of the same length are counted, not hidden", () => {
    // a -> b -> d and a -> c -> d are both two hops. One is shown; the
    // reader is told the other exists rather than being left to assume
    // the chain is the only way through.
    const g = edges(["a", "b", [1]], ["a", "c", [2]], ["b", "d", [3]], ["c", "d", [4]]);
    const r = trace(g, "a", "d");
    assert.equal(r.found, true);
    assert.equal(r.hops.length, 2);
    assert.equal(r.alternatives, 1);
});

test("route: a single route reports no alternatives", () => {
    assert.equal(trace(CHAIN, "app/main.py", "app/db.py").alternatives, 0);
});

test("route: the same pair always yields the same chain", () => {
    // Ties are broken by sorted neighbour order, so a card pasted into a
    // review can be reproduced by whoever reads it.
    const g = edges(["a", "z", [1]], ["a", "b", [2]], ["z", "d", [3]], ["b", "d", [4]]);
    const first = JSON.stringify(trace(g, "a", "d").hops);
    for (let i = 0; i < 20; i++) {
        assert.equal(JSON.stringify(trace(g, "a", "d").hops), first);
    }
    assert.equal(JSON.parse(first)[0].to, "b", "the lexically first tie should win");
});

test("route: no connection says so, and points out the reverse", () => {
    // Asking in the wrong direction is the usual reason a trace is empty.
    const r = trace(CHAIN, "app/db.py", "app/main.py");
    assert.equal(r.found, false);
    assert.equal(r.reverseExists, true);
    assert.deepEqual(r.hops, []);
});

test("route: genuinely unconnected files claim no reverse either", () => {
    const g = edges(["a", "b", [1]], ["x", "y", [2]]);
    const r = trace(g, "a", "y");
    assert.equal(r.found, false);
    assert.equal(r.reverseExists, false);
});

test("route: a file to itself is not a route", () => {
    const r = trace(CHAIN, "app/main.py", "app/main.py");
    assert.equal(r.found, false);
    assert.equal(r.sameNode, true);
});

test("route: a cycle does not hang the trace", () => {
    const g = edges(["a", "b", [1]], ["b", "c", [2]], ["c", "a", [3]], ["c", "d", [4]]);
    const r = trace(g, "a", "d");
    assert.equal(r.found, true);
    assert.equal(r.hops.length, 3);
});

test("route: a long chain traces without recursion limits", () => {
    const g = [];
    for (let i = 0; i < 5000; i++) {
        g.push({ from: "m" + i, to: "m" + (i + 1), lines: [i + 1] });
    }
    const r = trace(g, "m0", "m5000");
    assert.equal(r.found, true);
    assert.equal(r.hops.length, 5000);
    assert.deepEqual(r.hops[4999].lines, [5000]);
});

test("route: an edge with no recorded line still traces", () => {
    const g = edges(["page.html", "app.js", []], ["app.js", "util.js", [2]]);
    const r = trace(g, "page.html", "util.js");
    assert.equal(r.found, true);
    assert.deepEqual(r.hops[0].lines, []);
});
