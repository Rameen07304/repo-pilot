// Shortest dependency route between two files, with the import line
// behind every hop.
//
// This answers a question the diagram cannot: "how does this file end up
// depending on that one?" On anything past a dozen nodes the arrows
// alone will not tell you, and the honest answer is a specific chain of
// imports rather than a shape.
//
// Pure functions, no DOM. It is inlined verbatim into the exported page,
// so the route still traces with no network and no server — and it is a
// real file rather than a string inside the renderer, so the frontend
// suite can test it directly instead of trusting it by eye.
(function (root, factory) {
    var api = factory();
    if (typeof module === "object" && module.exports) module.exports = api;
    else root.RPRoute = api;
})(typeof self !== "undefined" ? self : this, function () {
    var SEP = "\u0000";

    // Neighbour lists are sorted so a tie between two equally short
    // routes always resolves the same way. Two people tracing the same
    // pair on the same commit must get the same chain, or the card one
    // of them pastes into a review is not reproducible by the other.
    function buildGraph(edges) {
        var out = {};
        var lines = {};
        for (var i = 0; i < edges.length; i++) {
            var e = edges[i];
            (out[e.from] = out[e.from] || []).push(e.to);
            lines[e.from + SEP + e.to] = (e.lines || []).slice();
        }
        for (var key in out) {
            if (Object.prototype.hasOwnProperty.call(out, key)) {
                out[key] = out[key].sort();
            }
        }
        return { out: out, lines: lines };
    }

    function shortest(graph, from, to) {
        if (from === to) return null;
        var dist = {}, parent = {}, ways = {};
        dist[from] = 0;
        ways[from] = 1;
        var queue = [from], head = 0;

        while (head < queue.length) {
            var node = queue[head++];
            if (node === to) continue;  // no shorter route leaves the target
            var next = graph.out[node] || [];
            for (var i = 0; i < next.length; i++) {
                var child = next[i];
                if (dist[child] === undefined) {
                    dist[child] = dist[node] + 1;
                    parent[child] = node;
                    ways[child] = ways[node];
                    queue.push(child);
                } else if (dist[child] === dist[node] + 1) {
                    // Another route of the same length reaches it. The
                    // first parent wins the chain; this only counts.
                    ways[child] += ways[node];
                }
            }
        }
        if (dist[to] === undefined) return null;

        var path = [to];
        var cursor = to;
        while (cursor !== from) {
            cursor = parent[cursor];
            path.unshift(cursor);
        }
        return { path: path, alternatives: Math.max(0, (ways[to] || 1) - 1) };
    }

    /**
     * trace(edges, from, to)
     *
     * edges: [{ from, to, lines: [number] }]
     * Returns { found, hops, alternatives, reverseExists, sameNode }.
     *
     * When there is no route, reverseExists says whether one runs the
     * other way. Asking in the wrong direction is the usual reason a
     * trace comes back empty, and saying so beats a bare "no route".
     */
    function trace(edges, from, to) {
        var graph = buildGraph(edges);
        if (from === to) {
            return {
                found: false, hops: [], alternatives: 0,
                reverseExists: false, sameNode: true,
            };
        }
        var forward = shortest(graph, from, to);
        if (!forward) {
            return {
                found: false, hops: [], alternatives: 0,
                reverseExists: !!shortest(graph, to, from), sameNode: false,
            };
        }
        var hops = [];
        for (var i = 0; i < forward.path.length - 1; i++) {
            var a = forward.path[i], b = forward.path[i + 1];
            hops.push({ from: a, to: b, lines: graph.lines[a + SEP + b] || [] });
        }
        return {
            found: true,
            hops: hops,
            alternatives: forward.alternatives,
            reverseExists: false,
            sameNode: false,
        };
    }

    return { trace: trace, buildGraph: buildGraph };
});
