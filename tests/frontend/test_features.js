// Frontend behaviour for the four tier-1 features, driven through the
// real app.html + app.js + features.js in jsdom.
//
// Run with:  node --test tests/frontend/

const test = require("node:test");
const assert = require("node:assert");
const { createHarness } = require("./dom_harness");

// --- Canned API responses -------------------------------------------------

const GUIDE = {
    repo_full_name: "acme/app",
    commit_sha: "sha1",
    overview: "A test project.",
    entry_points: ["app/main.py"],
    reading_order: [
        {
            file_path: "app/main.py",
            why_it_matters: "An entry point: execution starts here.",
            what_to_understand: "What run() does.",
            depends_on: ["core/db.py"],
            depended_on_by: [],
            key_symbols: ["run"],
            is_entry_point: true,
        },
        {
            file_path: "core/db.py",
            why_it_matters: "1 module(s) import it.",
            what_to_understand: "What save() does.",
            depends_on: [],
            depended_on_by: ["app/main.py"],
            key_symbols: ["save"],
            is_entry_point: false,
        },
    ],
    suggested_questions: ["How does a request flow through app/main.py?"],
    graph_available: true,
};

const FILE_SOURCE = {
    path: "core/db.py",
    content: "def save():\n    pass\n",
    language: "python",
    size_bytes: 20,
    line_count: 3,
    truncated: false,
    symbols: [{ name: "save", kind: "function", start_line: 1, end_line: 2 }],
    imports: [],
    imported_by: ["app/main.py"],
};

const IMPACT = {
    target_path: "core/db.py",
    target_symbol: null,
    direct: [{ path: "app/main.py", depth: 1, symbols: ["run"], via: ["core/db.py"], is_direct: true }],
    indirect: [{ path: "cli/run.py", depth: 2, symbols: [], via: ["app/main.py"], is_direct: false }],
    callers: [],
    entry_points_reached: ["app/main.py"],
    affected_files: ["app/main.py", "cli/run.py"],
    max_depth: 2,
    summary: "Changing core/db.py directly affects 1 file(s) and indirectly affects 1 more.",
    truncated: false,
};

function defaultRoutes(overrides = {}) {
    return {
        "/api/onboarding": async () => GUIDE,
        "/api/file": async () => FILE_SOURCE,
        "/api/impact": async () => IMPACT,
        ...overrides,
    };
}

function harnessWithRepo(overrides) {
    const h = createHarness({ routes: defaultRoutes(overrides) });
    h.setRepo();
    return h;
}

// =========================================================================
// Feature 1 — Onboarding guide
// =========================================================================

test("onboarding: renders the reading order with entry-point badges", async () => {
    const h = harnessWithRepo();
    h.$("onboarding-btn").click();
    await h.flush();

    const steps = h.document.querySelectorAll(".reading-step");
    assert.strictEqual(steps.length, 2);
    assert.match(steps[0].textContent, /app\/main\.py/);
    assert.match(steps[0].textContent, /entry point/);
    assert.match(steps[0].textContent, /Take away: What run\(\) does\./);
    // The second file is not an entry point, so it gets no badge.
    assert.strictEqual(steps[1].querySelector(".badge-entry"), null);
    assert.ok(!h.$("onboarding-guide").classList.contains("hidden"));
});

test("onboarding: shows a loading state and clears it when done", async () => {
    let resolveGuide;
    const h = harnessWithRepo({
        "/api/onboarding": () => new Promise((r) => { resolveGuide = () => r(GUIDE); }),
    });

    h.$("onboarding-btn").click();
    await h.flush(1);
    assert.strictEqual(h.$("onboarding-status").className, "status-loading");
    assert.strictEqual(h.$("onboarding-btn").disabled, true);

    resolveGuide();
    await h.flush();
    assert.notStrictEqual(h.$("onboarding-status").className, "status-loading");
    assert.strictEqual(h.$("onboarding-btn").disabled, false);
});

test("onboarding: surfaces a server error instead of an empty guide", async () => {
    const h = harnessWithRepo({
        "/api/onboarding": async () => ({ __error: true, status: 404, detail: "Ingest the repo first." }),
    });
    h.$("onboarding-btn").click();
    await h.flush();

    assert.strictEqual(h.$("onboarding-status").className, "status-error");
    assert.match(h.$("onboarding-status").textContent, /Ingest the repo first/);
    assert.ok(h.$("onboarding-guide").classList.contains("hidden"));
});

test("onboarding: warns when the repo has no import graph", async () => {
    const h = harnessWithRepo({
        "/api/onboarding": async () => ({ ...GUIDE, graph_available: false }),
    });
    h.$("onboarding-btn").click();
    await h.flush();
    assert.strictEqual(h.$("onboarding-status").className, "status-warn");
});

test("onboarding: an empty reading order shows a useful message, not a blank list", async () => {
    const h = harnessWithRepo({
        "/api/onboarding": async () => ({
            ...GUIDE, reading_order: [], entry_points: [], suggested_questions: [],
        }),
    });
    h.$("onboarding-btn").click();
    await h.flush();

    assert.match(h.$("reading-order-list").textContent, /No files could be ranked/);
    assert.match(h.$("entry-points-list").textContent, /none detected/);
});

test("onboarding: requires an ingested repo", async () => {
    // No setRepo() call: this is the page exactly as it loads.
    const h = createHarness({ routes: defaultRoutes() });
    h.$("onboarding-btn").click();
    await h.flush();

    assert.strictEqual(h.$("onboarding-status").className, "status-error");
    assert.strictEqual(h.fetch.calls.length, 0);
});

test("onboarding: clicking an important file opens it in the drawer", async () => {
    const h = harnessWithRepo();
    h.$("onboarding-btn").click();
    await h.flush();

    h.document.querySelectorAll(".reading-step-path")[1].click();
    await h.flush();

    assert.ok(!h.$("file-panel").hidden);
    assert.strictEqual(h.$("file-panel-path").textContent, "core/db.py");
    assert.deepStrictEqual(
        h.fetch.calls.at(-1).body,
        { repo_full_name: "acme/app", commit_sha: "sha1", file_path: "core/db.py" }
    );
});

test("onboarding: a suggested question is asked without file context", async () => {
    const h = harnessWithRepo({
        "/api/ask": async () => ({
            answer_text: "Here is how.", citations: [], confidence: 0.8,
            mode_used: "quick", steps_taken: 1, investigation_log: [],
        }),
    });
    h.$("onboarding-btn").click();
    await h.flush();

    h.document.querySelector("#suggested-questions button").click();
    await h.flush();

    const askCall = h.fetch.calls.find((c) => c.url === "/api/ask");
    assert.strictEqual(askCall.body.question, "How does a request flow through app/main.py?");
    assert.ok(h.$("ask-tab").classList.contains("active"));
});

// =========================================================================
// Feature 3 — File drawer: source and "ask about this file"
// =========================================================================

test("file drawer: shows source, symbols and both edge directions", async () => {
    const h = harnessWithRepo();
    await h.window.RepoPilot.openFile("core/db.py");
    await h.flush();

    assert.match(h.$("file-source-code").textContent, /def save\(\)/);
    assert.match(h.$("file-panel-meta").textContent, /python/);
    assert.match(h.$("file-panel-meta").textContent, /3 lines/);
    assert.match(h.$("file-symbols").textContent, /save/);
    assert.match(h.$("file-imported-by").textContent, /app\/main\.py/);
    assert.match(h.$("file-imports").textContent, /imports nothing in this repo/);
});

test("file drawer: populates the impact symbol picker from the parsed symbols", async () => {
    const h = harnessWithRepo();
    await h.window.RepoPilot.openFile("core/db.py");
    await h.flush();

    const options = [...h.$("symbol-select").options].map((o) => o.value);
    assert.deepStrictEqual(options, ["", "save"]);
    assert.strictEqual(h.$("symbol-select").disabled, false);
});

test("file drawer: a file with no symbols disables the symbol picker", async () => {
    const h = harnessWithRepo({
        "/api/file": async () => ({ ...FILE_SOURCE, symbols: [] }),
    });
    await h.window.RepoPilot.openFile("core/db.py");
    await h.flush();

    assert.strictEqual(h.$("symbol-select").disabled, true);
    assert.match(h.$("file-symbols").textContent, /no parsed symbols/);
});

test("file drawer: a file with no stored content says so rather than showing nothing", async () => {
    const h = harnessWithRepo({
        "/api/file": async () => ({ ...FILE_SOURCE, content: "", line_count: 0 }),
    });
    await h.window.RepoPilot.openFile("a.bin");
    await h.flush();
    assert.match(h.$("file-source-code").textContent, /no stored content/);
});

test("file drawer: a failed load shows an error and no stale source", async () => {
    const h = harnessWithRepo({
        "/api/file": async () => ({ __error: true, status: 404, detail: "not part of this commit" }),
    });
    await h.window.RepoPilot.openFile("ghost.py");
    await h.flush();

    assert.strictEqual(h.$("file-panel-status").className, "status-error");
    assert.match(h.$("file-panel-status").textContent, /not part of this commit/);
    assert.strictEqual(h.$("file-source-code").textContent, "");
});

test("file drawer: close button and Escape both dismiss it", async () => {
    const h = harnessWithRepo();
    await h.window.RepoPilot.openFile("core/db.py");
    await h.flush();
    assert.ok(!h.$("file-panel").hidden);

    h.$("file-panel-close").click();
    assert.ok(h.$("file-panel").hidden);

    await h.window.RepoPilot.openFile("core/db.py");
    await h.flush();
    h.document.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Escape" }));
    assert.ok(h.$("file-panel").hidden);
});

test("ask about this file: carries the file as context into the question", async () => {
    const h = harnessWithRepo({
        "/api/ask": async () => ({
            answer_text: "It saves rows.", citations: ["core/db.py:1-2"], confidence: 0.9,
            mode_used: "quick", steps_taken: 1, investigation_log: [],
        }),
    });
    await h.window.RepoPilot.openFile("core/db.py");
    await h.flush();

    h.$("ask-about-file-btn").click();
    await h.flush();

    const askCall = h.fetch.calls.find((c) => c.url === "/api/ask");
    assert.strictEqual(askCall.body.question, "In the file core/db.py: What does core/db.py do?");
    assert.ok(h.$("ask-tab").classList.contains("active"));
    assert.ok(h.$("file-panel").hidden, "the drawer gets out of the way");
    assert.match(h.$("ask-context-path").textContent, /core\/db\.py/);
    assert.match(h.$("answer-text").textContent, /It saves rows/);
});

test("ask context: can be cleared, and then questions go un-prefixed", async () => {
    const h = harnessWithRepo({
        "/api/ask": async () => ({
            answer_text: "ok", citations: [], confidence: 0.5,
            mode_used: "quick", steps_taken: 1, investigation_log: [],
        }),
    });
    await h.window.RepoPilot.openFile("core/db.py");
    await h.flush();
    h.$("ask-about-file-btn").click();
    await h.flush();

    h.$("ask-context-clear").click();
    assert.ok(h.$("ask-context").classList.contains("hidden"));

    h.$("question-input").value = "how does auth work?";
    h.$("ask-form").dispatchEvent(new h.window.Event("submit"));
    await h.flush();

    assert.strictEqual(h.fetch.calls.at(-1).body.question, "how does auth work?");
});

test("citations in an answer open the cited file", async () => {
    const h = harnessWithRepo({
        "/api/ask": async () => ({
            answer_text: "See here.", citations: ["core/db.py:1-2"], confidence: 0.9,
            mode_used: "quick", steps_taken: 1, investigation_log: [],
        }),
    });
    h.$("question-input").value = "where is save?";
    h.$("ask-form").dispatchEvent(new h.window.Event("submit"));
    await h.flush();

    const link = h.document.querySelector(".citation-link");
    assert.strictEqual(link.textContent, "core/db.py:1-2");
    link.click();
    await h.flush();
    assert.strictEqual(h.$("file-panel-path").textContent, "core/db.py");
});

// =========================================================================
// Feature 4 — Impact analysis
// =========================================================================

test("impact: renders direct and indirect dependents distinctly", async () => {
    const h = harnessWithRepo();
    await h.window.RepoPilot.openFile("core/db.py");
    await h.flush();

    h.$("impact-btn").click();
    await h.flush();

    assert.match(h.$("impact-summary").textContent, /directly affects 1 file/);
    const direct = h.$("impact-direct").querySelectorAll(".impact-row");
    const indirect = h.$("impact-indirect").querySelectorAll(".impact-row");

    assert.strictEqual(direct.length, 1);
    assert.ok(direct[0].classList.contains("impact-direct"));
    assert.match(direct[0].textContent, /app\/main\.py/);
    assert.match(direct[0].textContent, /direct/);

    assert.strictEqual(indirect.length, 1);
    assert.ok(indirect[0].classList.contains("impact-indirect"));
    assert.match(indirect[0].textContent, /depth 2/);
    assert.match(indirect[0].textContent, /via app\/main\.py/);
});

test("impact: shows the computed counts as stats", async () => {
    const h = harnessWithRepo();
    await h.window.RepoPilot.openFile("core/db.py");
    await h.flush();
    h.$("impact-btn").click();
    await h.flush();

    const stats = h.$("impact-stats").textContent;
    assert.match(stats, /direct/);
    assert.match(stats, /indirect/);
    assert.match(stats, /max depth/);
    assert.match(stats, /entry points/);
});

test("impact: switches to the impact tab and hides the empty state", async () => {
    const h = harnessWithRepo();
    await h.window.RepoPilot.openFile("core/db.py");
    await h.flush();
    assert.ok(!h.$("impact-empty").classList.contains("hidden"));

    h.$("impact-btn").click();
    await h.flush();

    assert.ok(h.$("panel-impact").classList.contains("active"));
    assert.ok(h.$("impact-empty").classList.contains("hidden"));
    assert.ok(!h.$("impact-result").classList.contains("hidden"));
});

test("impact: a symbol chip scopes the analysis to that symbol", async () => {
    const h = harnessWithRepo({
        "/api/impact": async (body) => ({
            ...IMPACT,
            target_symbol: body.symbol,
            callers: [{ path: "app/main.py", depth: 1, symbols: ["run"], via: [], is_direct: true }],
        }),
    });
    await h.window.RepoPilot.openFile("core/db.py");
    await h.flush();

    h.document.querySelector("#file-symbols button").click();
    await h.flush();

    assert.strictEqual(h.fetch.calls.at(-1).body.symbol, "save");
    assert.strictEqual(h.$("symbol-select").value, "save");
    assert.match(h.$("impact-callers").textContent, /app\/main\.py/);
});

test("impact: an empty blast radius reads as a real answer, not a blank panel", async () => {
    const h = harnessWithRepo({
        "/api/impact": async () => ({
            ...IMPACT, direct: [], indirect: [], entry_points_reached: [], affected_files: [],
            max_depth: 0, summary: "Nothing in the parsed dependency graph imports or calls core/db.py.",
        }),
    });
    await h.window.RepoPilot.openFile("core/db.py");
    await h.flush();
    h.$("impact-btn").click();
    await h.flush();

    assert.match(h.$("impact-summary").textContent, /Nothing in the parsed dependency graph/);
    assert.match(h.$("impact-direct").textContent, /nothing imports this directly/);
    assert.match(h.$("impact-indirect").textContent, /no indirect dependents/);
});

test("impact: a failure falls back to the empty state with an error", async () => {
    const h = harnessWithRepo({
        "/api/impact": async () => ({ __error: true, status: 404, detail: "not in the graph" }),
    });
    await h.window.RepoPilot.openFile("core/db.py");
    await h.flush();
    h.$("impact-btn").click();
    await h.flush();

    assert.strictEqual(h.$("file-panel-status").className, "status-error");
    assert.match(h.$("file-panel-status").textContent, /not in the graph/);
    assert.ok(!h.$("impact-empty").classList.contains("hidden"));
});

test("impact: clicking an affected file navigates to it", async () => {
    const h = harnessWithRepo();
    await h.window.RepoPilot.openFile("core/db.py");
    await h.flush();
    h.$("impact-btn").click();
    await h.flush();

    h.document.querySelector("#impact-direct .impact-path").click();
    await h.flush();
    assert.strictEqual(h.$("file-panel-path").textContent, "app/main.py");
});

test("impact: opening another file resets the previous result", async () => {
    const h = harnessWithRepo();
    await h.window.RepoPilot.openFile("core/db.py");
    await h.flush();
    h.$("impact-btn").click();
    await h.flush();
    assert.ok(!h.$("impact-result").classList.contains("hidden"));

    await h.window.RepoPilot.openFile("app/main.py");
    await h.flush();
    assert.ok(h.$("impact-result").classList.contains("hidden"));
});

module.exports = {};

// --- Document viewer ------------------------------------------------------

test("viewer: the frame is sandboxed without same-origin access", () => {
    // A blob: URL inherits the origin of the page that created it, so an
    // un-sandboxed frame would run the generated document's inline script
    // with full access to this app. allow-same-origin must never appear.
    const fs = require("fs");
    const path = require("path");
    const html = fs.readFileSync(
        path.resolve(__dirname, "..", "..", "app", "interface", "templates", "app.html"),
        "utf8",
    );
    const frame = html.match(/<iframe id="doc-viewer-frame"[\s\S]*?>/)[0];
    assert.match(frame, /sandbox="/);
    assert.ok(!/allow-same-origin/.test(frame), "allow-same-origin would defeat the sandbox");
    // It still needs scripts, and popups for its own GitHub links.
    assert.match(frame, /allow-scripts/);
    assert.match(frame, /allow-popups/);
});

test("viewer: opens the generated page instead of downloading it", async () => {
    const h = harnessWithRepo({
        "/api/architecture": async () => ({ __html: "<html><body>graph</body></html>" }),
    });
    const doc = h.document;

    doc.getElementById("export-arch-btn").click();
    await h.flush();

    const viewer = doc.getElementById("doc-viewer");
    assert.ok(!viewer.classList.contains("hidden"), "viewer should be open");
    assert.equal(h.objectUrls.created.length, 1);
    assert.equal(doc.getElementById("doc-viewer-frame").src, h.objectUrls.created[0].url);
    // Downloading is still offered, as a button inside the viewer.
    assert.match(doc.getElementById("doc-viewer-download").download, /architecture/);
});

test("viewer: a rate-limit message is shown rather than swallowed", async () => {
    // The whole reason this was hard to diagnose in the real app: a 429
    // looked like nothing happening.
    const h = harnessWithRepo({
        "/api/architecture": async () => ({
            __error: true,
            status: 429,
            detail: "Too many documents generated from your IP this hour.",
        }),
    });
    const doc = h.document;

    doc.getElementById("export-arch-btn").click();
    await h.flush();

    assert.ok(doc.getElementById("doc-viewer").classList.contains("hidden"));
    assert.match(doc.getElementById("diagram-status").textContent, /Too many documents/);
});

test("viewer: closes on Escape and releases the object URL", async () => {
    const h = harnessWithRepo({
        "/api/architecture": async () => ({ __html: "<html><body>graph</body></html>" }),
    });
    const doc = h.document;

    doc.getElementById("export-arch-btn").click();
    await h.flush();
    assert.equal(h.objectUrls.revoked.length, 0);

    doc.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await h.flush();

    assert.ok(doc.getElementById("doc-viewer").classList.contains("hidden"));
    assert.equal(h.objectUrls.revoked.length, 1, "the blob URL should be released on close");
    assert.match(doc.getElementById("doc-viewer-frame").src, /about:blank/);
});

test("viewer: a second document releases the first one's URL", async () => {
    const h = harnessWithRepo({
        "/api/architecture": async () => ({ __html: "<html><body>graph</body></html>" }),
    });
    const doc = h.document;

    doc.getElementById("export-arch-btn").click();
    await h.flush();
    doc.getElementById("export-arch-btn").click();
    await h.flush();

    assert.equal(h.objectUrls.created.length, 2);
    assert.equal(h.objectUrls.revoked.length, 1, "opening another must not leak the first");
});

// --- Stale results across repos -------------------------------------------

test("ingest: a new repo clears the previous repo's results", async () => {
    // The handbook button is revealed by onboarding. Left visible after
    // switching repos it offers a handbook for a repository nobody is
    // looking at, under the new repository's name.
    const h = harnessWithRepo({
        "/api/ingest": async () => ({
            repo_full_name: "acme/other",
            commit_sha: "sha2",
            summary: "A different project.",
            file_count: 3,
            detected_stack: ["python"],
            skipped_file_count: 0,
            was_cached: false,
        }),
    });
    const doc = h.document;

    doc.getElementById("onboarding-btn").click();
    await h.flush();
    assert.ok(!doc.getElementById("handbook-btn").classList.contains("hidden"),
        "precondition: onboarding reveals the handbook button");
    assert.ok(doc.getElementById("onboarding-guide").textContent.length > 0);

    doc.getElementById("repo-url").value = "https://github.com/acme/other";
    doc.getElementById("ingest-form").dispatchEvent(
        new h.window.Event("submit", { bubbles: true, cancelable: true }));
    await h.flush(30);

    assert.ok(doc.getElementById("handbook-btn").classList.contains("hidden"),
        "the previous repo's handbook button should be gone");
    assert.ok(doc.getElementById("onboarding-guide").classList.contains("hidden"),
        "the previous repo's reading order should be hidden");

    // Hidden, NOT emptied. The template puts the section scaffolding
    // inside #onboarding-guide and the renderer only fills those
    // children; wiping it destroyed them for good, so onboarding failed
    // on every repo after the first. This assertion used to demand the
    // destructive version.
    assert.ok(doc.getElementById("onboarding-overview"),
        "the guide scaffolding must survive a repo switch");
});

test("ingest: onboarding still works on a second repo", async () => {
    // The end-to-end version of the bug above: the failure only showed
    // on the SECOND repo, which is exactly the path a reset test skips
    // if it only checks what is hidden.
    const h = harnessWithRepo({
        "/api/ingest": async () => ({
            repo_full_name: "acme/other", commit_sha: "sha2", summary: "Another.",
            file_count: 3, detected_stack: ["python"], skipped_file_count: 0, was_cached: false,
        }),
    });
    const doc = h.document;

    doc.getElementById("onboarding-btn").click();
    await h.flush();

    doc.getElementById("repo-url").value = "https://github.com/acme/other";
    doc.getElementById("ingest-form").dispatchEvent(
        new h.window.Event("submit", { bubbles: true, cancelable: true }));
    await h.flush(30);

    doc.getElementById("onboarding-btn").click();
    await h.flush();

    assert.ok(!doc.getElementById("onboarding-guide").classList.contains("hidden"),
        "onboarding should render again after switching repos");
    assert.match(doc.getElementById("onboarding-status").textContent, /^(?!.*Cannot set properties)/);
});

test("ingest: re-ingesting the same repo at a new commit also clears results", async () => {
    // Results belong to a commit, not just a repo.
    const h = harnessWithRepo({
        "/api/ingest": async () => ({
            repo_full_name: "acme/app",
            commit_sha: "sha-NEW",
            summary: "Same project, new commit.",
            file_count: 3,
            detected_stack: ["python"],
            skipped_file_count: 0,
            was_cached: false,
        }),
    });
    const doc = h.document;

    doc.getElementById("onboarding-btn").click();
    await h.flush();
    assert.ok(!doc.getElementById("handbook-btn").classList.contains("hidden"));

    doc.getElementById("repo-url").value = "https://github.com/acme/app";
    doc.getElementById("ingest-form").dispatchEvent(
        new h.window.Event("submit", { bubbles: true, cancelable: true }));
    await h.flush(30);

    assert.ok(doc.getElementById("handbook-btn").classList.contains("hidden"));
});


// --- The phone navigation drawer ---
//
// Below 768px the sidebar is off-canvas and a hamburger opens it. jsdom
// applies no media queries, so these test the behaviour and the ARIA
// contract, which are what break silently; the widths themselves are
// checked in a real browser.

test("nav drawer: starts closed and says so", () => {
    const doc = harnessWithRepo().document;
    const toggle = doc.getElementById("nav-toggle");

    assert.equal(toggle.getAttribute("aria-expanded"), "false");
    assert.ok(!doc.getElementById("sidebar").classList.contains("is-open"));
    assert.ok(doc.getElementById("nav-scrim").hidden,
        "a closed scrim must not sit over the page swallowing taps");
});

test("nav drawer: the toggle opens it and locks the page behind it", () => {
    const doc = harnessWithRepo().document;
    const toggle = doc.getElementById("nav-toggle");
    toggle.click();

    assert.equal(toggle.getAttribute("aria-expanded"), "true");
    assert.ok(doc.getElementById("sidebar").classList.contains("is-open"));
    assert.ok(!doc.getElementById("nav-scrim").hidden);
    assert.ok(doc.body.classList.contains("nav-locked"),
        "the page must not scroll behind an open drawer");
    assert.match(toggle.textContent, /Close the menu/);
});

test("nav drawer: opening it moves focus into it", () => {
    // It opened because someone wants the menu. Leaving focus on the
    // button sends the next Tab into the page behind the drawer, which
    // is exactly the trap this pattern is supposed to avoid.
    const doc = harnessWithRepo().document;
    doc.getElementById("nav-toggle").click();

    assert.notEqual(doc.activeElement, doc.body, "focus should have moved");
    assert.ok(doc.activeElement.classList.contains("tab-btn"),
        "focus belongs on the first option in the drawer");
});

test("nav drawer: closing it gives focus back to the button", () => {
    const h = harnessWithRepo();
    const doc = h.document;
    const toggle = doc.getElementById("nav-toggle");

    toggle.click();
    doc.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    assert.equal(doc.activeElement, toggle);
});

test("nav drawer: choosing a tab closes it", () => {
    // The only reason it was open. Leaving it covering the thing the
    // reader just asked for would be the obvious bug here.
    const doc = harnessWithRepo().document;
    doc.getElementById("nav-toggle").click();
    doc.querySelector('.tab-btn[data-tab="ask-tab"]').click();

    assert.ok(!doc.getElementById("sidebar").classList.contains("is-open"));
    assert.equal(doc.getElementById("nav-toggle").getAttribute("aria-expanded"), "false");
    assert.ok(!doc.body.classList.contains("nav-locked"));
    assert.ok(doc.getElementById("ask-tab").classList.contains("active"),
        "and the tab it was opened to reach is the one now showing");
});

test("nav drawer: the scrim and Escape both close it", () => {
    const h = harnessWithRepo();
    const doc = h.document;

    doc.getElementById("nav-toggle").click();
    doc.getElementById("nav-scrim").click();
    assert.ok(!doc.getElementById("sidebar").classList.contains("is-open"), "scrim");

    doc.getElementById("nav-toggle").click();
    doc.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    assert.ok(!doc.getElementById("sidebar").classList.contains("is-open"), "Escape");
    assert.equal(doc.getElementById("nav-toggle").getAttribute("aria-expanded"), "false");
});


// --- The "what this is built with" panel ---
//
// It appears on ingest, before the reader has thought of a question, so
// its job is to answer "what IS this" without being asked.

test("stack panel: appears on ingest, grouped, with the file that proved each", () => {
    const h = harnessWithRepo({
        "/api/ingest": async () => ({
            repo_full_name: "acme/app", commit_sha: "sha1", summary: "A thing.",
            file_count: 9, detected_stack: ["python", "fastapi", "docker"],
            stack_detail: [
                { name: "python", kind: "language", evidence: "requirements.txt" },
                { name: "fastapi", kind: "framework", evidence: "requirements.txt" },
                { name: "docker", kind: "tool", evidence: "Dockerfile" },
            ],
            skipped_file_count: 0, was_cached: false,
        }),
    });
    const doc = h.document;

    doc.getElementById("repo-url").value = "https://github.com/acme/app";
    doc.getElementById("ingest-form").dispatchEvent(
        new h.window.Event("submit", { bubbles: true, cancelable: true }));
    return h.flush(30).then(() => {
        const panel = doc.getElementById("stack-panel");
        assert.ok(!panel.classList.contains("hidden"), "the panel should be showing");

        const headings = [...doc.querySelectorAll(".stack-group-title")].map((h) => h.textContent);
        assert.deepEqual(headings, ["Languages", "Frameworks", "Tooling"],
            "grouped, and in that order");

        const names = [...doc.querySelectorAll(".stack-name")].map((n) => n.textContent);
        assert.deepEqual(names, ["python", "fastapi", "docker"]);

        const evidence = [...doc.querySelectorAll(".stack-evidence")].map((e) => e.textContent);
        assert.deepEqual(evidence, ["requirements.txt", "requirements.txt", "Dockerfile"],
            "every entry names the file it was read from");
        assert.ok(doc.getElementById("stack-empty").classList.contains("hidden"));
    });
});

test("stack panel: a repo with no stack says so rather than showing an empty box", () => {
    // A documentation or config repository genuinely has none. An empty
    // panel would read as a failed request.
    const h = harnessWithRepo({
        "/api/ingest": async () => ({
            repo_full_name: "acme/docs", commit_sha: "sha1", summary: "Docs.",
            file_count: 40, detected_stack: [], stack_detail: [],
            skipped_file_count: 0, was_cached: false,
        }),
    });
    const doc = h.document;

    doc.getElementById("repo-url").value = "https://github.com/acme/docs";
    doc.getElementById("ingest-form").dispatchEvent(
        new h.window.Event("submit", { bubbles: true, cancelable: true }));
    return h.flush(30).then(() => {
        assert.ok(!doc.getElementById("stack-panel").classList.contains("hidden"));
        assert.ok(!doc.getElementById("stack-empty").classList.contains("hidden"),
            "it should explain why there is nothing");
        assert.equal(doc.querySelectorAll(".stack-name").length, 0);
    });
});

test("stack panel: a cached ingest from before evidence existed still names the stack", () => {
    // Entries cached before stack_detail existed come back without it.
    // Falling through to "no build files were found" would state
    // something false about the repository, which is the one thing this
    // panel exists not to do. It shows what it knows, minus the file.
    const h = harnessWithRepo({
        "/api/ingest": async () => ({
            repo_full_name: "acme/old", commit_sha: "sha1", summary: "Old.",
            file_count: 5, detected_stack: ["python"],
            skipped_file_count: 0, was_cached: true,
        }),
    });
    const doc = h.document;

    doc.getElementById("repo-url").value = "https://github.com/acme/old";
    doc.getElementById("ingest-form").dispatchEvent(
        new h.window.Event("submit", { bubbles: true, cancelable: true }));
    return h.flush(30).then(() => {
        assert.ok(!doc.getElementById("tabs").classList.contains("hidden"),
            "the rest of the page must still work");
        assert.ok(doc.getElementById("stack-empty").classList.contains("hidden"),
            "it must NOT claim no build files were found");

        const names = [...doc.querySelectorAll(".stack-name")].map((n) => n.textContent);
        assert.deepEqual(names, ["python"]);
        assert.equal(doc.querySelectorAll(".stack-evidence").length, 0,
            "no evidence chip, because none was recorded");
    });
});

test("stack panel: the python-entrypoint signal is never shown as a technology", () => {
    // It is a hint for the summary prompt, not something a reader should
    // see listed under "what this is built with".
    const h = harnessWithRepo({
        "/api/ingest": async () => ({
            repo_full_name: "acme/app", commit_sha: "sha1", summary: "A thing.",
            file_count: 9, detected_stack: ["python", "python-entrypoint"],
            skipped_file_count: 0, was_cached: true,
        }),
    });
    const doc = h.document;

    doc.getElementById("repo-url").value = "https://github.com/acme/app";
    doc.getElementById("ingest-form").dispatchEvent(
        new h.window.Event("submit", { bubbles: true, cancelable: true }));
    return h.flush(30).then(() => {
        const names = [...doc.querySelectorAll(".stack-name")].map((n) => n.textContent);
        assert.deepEqual(names, ["python"]);
    });
});
