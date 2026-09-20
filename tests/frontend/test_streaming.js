// Live investigation streaming and diagram node interaction.
//
// The EventSource double lets these tests push events one at a time,
// which is the only way to assert that the timeline shows a *current*
// step while the investigation is still running rather than only its
// final state.

const test = require("node:test");
const assert = require("node:assert");
const { createHarness } = require("./dom_harness");

function event(type, message, metadata = {}) {
    return { type, message, metadata };
}

const ANSWER = {
    answer_text: "Auth works like this.",
    citations: ["auth/login.py:1-5"],
    confidence: 0.82,
    mode_used: "deep",
    steps_taken: 2,
    investigation_log: ['step 1: searched "auth" -> 2 new location(s)'],
};

/** Starts a deep-mode question and returns the live stream double. */
async function startDeepAsk(h, question = "how does auth work?") {
    h.$("deep-mode-checkbox").checked = true;
    h.$("question-input").value = question;
    h.$("ask-form").dispatchEvent(new h.window.Event("submit", { cancelable: true }));
    await h.flush();
    return h.streams.at(-1);
}

function harness(routes = {}) {
    const h = createHarness({ routes });
    h.setRepo();
    return h;
}

// =========================================================================
// Streaming timeline
// =========================================================================

test("streaming: deep mode opens an SSE stream with the question as a query param", async () => {
    const h = harness();
    const stream = await startDeepAsk(h);

    assert.ok(stream, "an EventSource was opened");
    const url = new h.window.URL(stream.url, "http://localhost");
    assert.strictEqual(url.pathname, "/api/ask/stream");
    assert.strictEqual(url.searchParams.get("question"), "how does auth work?");
    assert.strictEqual(url.searchParams.get("repo_full_name"), "acme/app");
    assert.strictEqual(url.searchParams.get("commit_sha"), "sha1");
    // Deep mode must not also fire the non-streaming endpoint.
    assert.strictEqual(h.fetch.calls.length, 0);
});

test("streaming: quick mode does not open a stream", async () => {
    const h = harness({
        "/api/ask": async () => ({ ...ANSWER, mode_used: "quick" }),
    });
    h.$("deep-mode-checkbox").checked = false;
    h.$("question-input").value = "q";
    h.$("ask-form").dispatchEvent(new h.window.Event("submit", { cancelable: true }));
    await h.flush();

    assert.strictEqual(h.streams.length, 0);
    assert.strictEqual(h.fetch.calls.at(-1).url, "/api/ask");
});

test("streaming: the timeline appears and reports a running state", async () => {
    const h = harness();
    await startDeepAsk(h);

    assert.ok(!h.$("live-investigation").classList.contains("hidden"));
    assert.strictEqual(h.$("live-state").textContent, "running");
    assert.ok(h.$("live-state").classList.contains("live-state-running"));
});

test("streaming: each event appends a step in order", async () => {
    const h = harness();
    const stream = await startDeepAsk(h);

    stream.emit("investigation_started", event("investigation_started", "Investigating: how does auth work?"));
    stream.emit("search_started", event("search_started", 'Searching for "auth"...', { query: "auth" }));
    stream.emit("search_result", event("search_result", "Found 4 references", { count: 4 }));

    const steps = h.document.querySelectorAll(".timeline-step");
    assert.strictEqual(steps.length, 3);
    assert.match(steps[0].textContent, /Investigating: how does auth work\?/);
    assert.match(steps[2].textContent, /Found 4 references/);
});

test("streaming: the newest in-progress step is current, earlier ones are done", async () => {
    const h = harness();
    const stream = await startDeepAsk(h);

    stream.emit("search_started", event("search_started", 'Searching for "auth"...'));
    let steps = h.document.querySelectorAll(".timeline-step");
    assert.ok(steps[0].classList.contains("current"), "the in-flight search is the current step");

    stream.emit("graph_traversal_started", event("graph_traversal_started", "Tracing callers..."));
    steps = h.document.querySelectorAll(".timeline-step");
    assert.ok(steps[0].classList.contains("done"), "the finished search is marked done");
    assert.ok(steps[1].classList.contains("current"));
});

test("streaming: a result event lands as done, not as an in-progress step", async () => {
    const h = harness();
    const stream = await startDeepAsk(h);
    stream.emit("search_result", event("search_result", "Found 4 references"));

    const step = h.document.querySelector(".timeline-step");
    assert.ok(step.classList.contains("done"));
    assert.ok(!step.classList.contains("current"));
});

test("streaming: files in an event become clickable chips", async () => {
    const h = harness({ "/api/file": async () => ({
        path: "moderation.py", content: "x", language: "python", size_bytes: 1,
        line_count: 1, truncated: false, symbols: [], imports: [], imported_by: [],
    }) });
    const stream = await startDeepAsk(h);

    stream.emit("search_result", event("search_result", "Found callers", {
        count: 2, files: ["moderation.py", "pipeline.py"],
    }));

    const chips = h.document.querySelectorAll(".timeline-files .chip");
    assert.strictEqual(chips.length, 2);
    assert.strictEqual(chips[0].textContent, "moderation.py");

    chips[0].click();
    await h.flush();
    assert.strictEqual(h.$("file-panel-path").textContent, "moderation.py");
});

test("streaming: completion marks the timeline complete and renders the answer", async () => {
    const h = harness();
    const stream = await startDeepAsk(h);

    stream.emit("search_result", event("search_result", "Found 4 references"));
    stream.emit("completed", event("completed", "Investigation complete", { citations: 1, steps: 2 }));
    assert.strictEqual(h.$("live-state").textContent, "complete");
    assert.ok(h.$("live-state").classList.contains("live-state-done"));

    stream.emit("answer", ANSWER);
    await h.flush();

    assert.ok(!h.$("answer-box").classList.contains("hidden"));
    assert.match(h.$("answer-text").textContent, /Auth works like this/);
    assert.match(h.$("answer-meta").textContent, /mode: deep/);
    assert.match(h.$("citations-list").textContent, /auth\/login\.py:1-5/);
    assert.strictEqual(stream.closed, true, "the stream is closed once the answer arrives");
});

test("streaming: the timeline survives alongside the finished answer", async () => {
    const h = harness();
    const stream = await startDeepAsk(h);
    stream.emit("search_started", event("search_started", "Searching..."));
    stream.emit("completed", event("completed", "Investigation complete"));
    stream.emit("answer", ANSWER);
    await h.flush();

    assert.ok(!h.$("live-investigation").classList.contains("hidden"));
    assert.strictEqual(h.document.querySelectorAll(".timeline-step").length, 2);
});

test("streaming: an error event is shown as a failed step and state", async () => {
    const h = harness();
    const stream = await startDeepAsk(h);

    stream.emit("error", event("error", "The investigation failed unexpectedly.", { status: 500 }));
    await h.flush();

    const step = h.document.querySelector(".timeline-step");
    assert.ok(step.classList.contains("error"));
    assert.strictEqual(h.$("live-state").textContent, "failed");
    assert.ok(h.$("live-state").classList.contains("live-state-error"));
    assert.strictEqual(h.$("ask-status").className, "status-error");
    assert.match(h.$("ask-status").textContent, /failed unexpectedly/);
    assert.ok(h.$("answer-box").classList.contains("hidden"));
});

test("streaming: a dropped connection is reported rather than hanging", async () => {
    const h = harness();
    const stream = await startDeepAsk(h);

    stream.fail();
    await h.flush();

    assert.strictEqual(h.$("live-state").textContent, "disconnected");
    assert.strictEqual(h.$("ask-status").className, "status-error");
    assert.strictEqual(h.$("ask-btn").disabled, false, "the ask button is usable again");
});

test("streaming: a normal close after the answer is not treated as an error", async () => {
    const h = harness();
    const stream = await startDeepAsk(h);
    stream.emit("completed", event("completed", "Investigation complete"));
    stream.emit("answer", ANSWER);
    await h.flush();

    stream.fail(); // EventSource also fires onerror on a clean server close
    await h.flush();

    assert.strictEqual(h.$("live-state").textContent, "complete");
    assert.notStrictEqual(h.$("ask-status").className, "status-error");
});

test("streaming: a second investigation starts a fresh timeline", async () => {
    const h = harness();
    const first = await startDeepAsk(h);
    first.emit("search_started", event("search_started", "Searching..."));
    first.emit("completed", event("completed", "done"));
    first.emit("answer", ANSWER);
    await h.flush();

    const second = await startDeepAsk(h, "another question");
    assert.notStrictEqual(second, first);
    assert.strictEqual(first.closed, true, "the previous stream is closed");
    assert.strictEqual(h.document.querySelectorAll(".timeline-step").length, 0);
    assert.strictEqual(h.$("live-state").textContent, "running");
});

test("streaming: a malformed event payload is ignored, not rendered", async () => {
    const h = harness();
    const stream = await startDeepAsk(h);

    (stream.listeners["search_result"] || []).forEach((fn) => fn({ data: "not json" }));
    assert.strictEqual(h.document.querySelectorAll(".timeline-step").length, 0);
});

// =========================================================================
// Diagram node interaction
// =========================================================================

const DIAGRAM = {
    mermaid_syntax: "flowchart LR\n    N0[\"main.py\"]\n    N1[\"db.py\"]\n    N0 --> N1",
    modules: ["app/main.py", "core/db.py"],
    granularity: "file",
    total_files: 2,
    node_paths: { N0: "app/main.py", N1: "core/db.py" },
};

/**
 * Mermaid is stubbed, so the test writes the node markup Mermaid would
 * have produced (including its "flowchart-N0-1" id shape) into the
 * container before generateDiagram() wires up the listeners.
 */
function stubRenderedSvg(h) {
    h.window.mermaid.run = async () => {
        h.$("mermaid-diagram").innerHTML = `
            <svg>
              <g class="node" id="flowchart-N0-1"><rect></rect></g>
              <g class="node" id="flowchart-N1-2"><rect></rect></g>
              <g class="cluster" id="cluster_app"><rect></rect></g>
            </svg>`;
    };
}

async function renderDiagram(h) {
    stubRenderedSvg(h);
    h.$("diagram-btn").click();
    await h.flush();
}

function diagramHarness(routes = {}) {
    return harness({ "/api/diagram": async () => DIAGRAM, ...routes });
}

test("diagram: file nodes become clickable and labelled with their path", async () => {
    const h = diagramHarness();
    await renderDiagram(h);

    const nodes = h.document.querySelectorAll("g.node.clickable-node");
    assert.strictEqual(nodes.length, 2, "both file nodes, not the cluster");
    assert.strictEqual(nodes[0].getAttribute("role"), "button");
    assert.strictEqual(nodes[0].getAttribute("aria-label"), "Open app/main.py");
    assert.strictEqual(nodes[0].getAttribute("tabindex"), "0");
    assert.strictEqual(nodes[0].querySelector("title").textContent, "app/main.py");
});

test("diagram: clusters are not made clickable", async () => {
    const h = diagramHarness();
    await renderDiagram(h);
    assert.ok(!h.document.querySelector("#cluster_app").classList.contains("clickable-node"));
});

test("diagram: clicking a node opens that file and marks it selected", async () => {
    const h = diagramHarness({
        "/api/file": async (body) => ({
            path: body.file_path, content: "x", language: "python", size_bytes: 1,
            line_count: 1, truncated: false, symbols: [], imports: [], imported_by: [],
        }),
    });
    await renderDiagram(h);

    const node = h.document.querySelector("#flowchart-N1-2");
    node.dispatchEvent(new h.window.MouseEvent("pointerdown", { clientX: 10, clientY: 10 }));
    node.dispatchEvent(new h.window.MouseEvent("pointerup", { clientX: 10, clientY: 10 }));
    await h.flush();

    assert.ok(node.classList.contains("selected-node"));
    assert.strictEqual(h.$("file-panel-path").textContent, "core/db.py");
    assert.strictEqual(h.fetch.calls.at(-1).body.file_path, "core/db.py");
});

test("diagram: dragging across a node pans instead of opening it", async () => {
    const h = diagramHarness();
    await renderDiagram(h);

    const node = h.document.querySelector("#flowchart-N0-1");
    node.dispatchEvent(new h.window.MouseEvent("pointerdown", { clientX: 10, clientY: 10 }));
    node.dispatchEvent(new h.window.MouseEvent("pointerup", { clientX: 90, clientY: 40 }));
    await h.flush();

    assert.ok(h.$("file-panel").hidden, "a pan must not open the drawer");
    assert.ok(!node.classList.contains("selected-node"));
});

test("diagram: a node is keyboard-activatable", async () => {
    const h = diagramHarness({
        "/api/file": async (body) => ({
            path: body.file_path, content: "x", language: "python", size_bytes: 1,
            line_count: 1, truncated: false, symbols: [], imports: [], imported_by: [],
        }),
    });
    await renderDiagram(h);

    const node = h.document.querySelector("#flowchart-N0-1");
    node.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await h.flush();

    assert.strictEqual(h.$("file-panel-path").textContent, "app/main.py");
});

test("diagram: impact analysis paints the blast radius onto the nodes", async () => {
    const h = diagramHarness({
        "/api/file": async (body) => ({
            path: body.file_path, content: "x", language: "python", size_bytes: 1,
            line_count: 1, truncated: false, symbols: [], imports: [], imported_by: [],
        }),
        "/api/impact": async () => ({
            target_path: "core/db.py", target_symbol: null,
            direct: [{ path: "app/main.py", depth: 1, symbols: [], via: [], is_direct: true }],
            indirect: [], callers: [], entry_points_reached: [],
            affected_files: ["app/main.py"], max_depth: 1,
            summary: "Changing core/db.py directly affects 1 file(s).", truncated: false,
        }),
    });
    await renderDiagram(h);

    await h.window.RepoPilot.openFile("core/db.py");
    await h.flush();
    h.$("impact-btn").click();
    await h.flush();

    assert.ok(h.$("mermaid-container").classList.contains("impact-active"));
    assert.ok(h.document.querySelector("#flowchart-N1-2").classList.contains("impact-target"));
    assert.ok(h.document.querySelector("#flowchart-N0-1").classList.contains("impact-direct-node"));
});

test("diagram: opening another file clears the previous blast radius", async () => {
    const h = diagramHarness({
        "/api/file": async (body) => ({
            path: body.file_path, content: "x", language: "python", size_bytes: 1,
            line_count: 1, truncated: false, symbols: [], imports: [], imported_by: [],
        }),
        "/api/impact": async () => ({
            target_path: "core/db.py", target_symbol: null,
            direct: [{ path: "app/main.py", depth: 1, symbols: [], via: [], is_direct: true }],
            indirect: [], callers: [], entry_points_reached: [],
            affected_files: ["app/main.py"], max_depth: 1, summary: "s", truncated: false,
        }),
    });
    await renderDiagram(h);
    await h.window.RepoPilot.openFile("core/db.py");
    await h.flush();
    h.$("impact-btn").click();
    await h.flush();
    assert.ok(h.$("mermaid-container").classList.contains("impact-active"));

    await h.window.RepoPilot.openFile("app/main.py");
    await h.flush();
    assert.ok(!h.$("mermaid-container").classList.contains("impact-active"));
});

test("diagram: a repo whose diagram has no node_paths still renders", async () => {
    const h = harness({
        "/api/diagram": async () => ({
            mermaid_syntax: "flowchart TD\n    A[No internal module dependencies detected]",
            modules: [], granularity: "file", total_files: 0, node_paths: {},
        }),
    });
    await renderDiagram(h);

    assert.strictEqual(h.document.querySelectorAll("g.node.clickable-node").length, 0);
    assert.ok(!h.$("mermaid-container").classList.contains("hidden"));
});

module.exports = {};
