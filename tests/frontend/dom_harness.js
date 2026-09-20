// Loads the real app.html, style.css, app.js and features.js into a
// jsdom document so the frontend tests drive the SAME markup and
// scripts the server serves — not a hand-written fixture that could
// drift from them.
//
// The third-party globals the page pulls from a CDN (mermaid, marked,
// DOMPurify, svgPanZoom) are stubbed, since the tests are about our
// own behaviour, not theirs.

const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const ROOT = path.resolve(__dirname, "..", "..");
const STATIC = path.join(ROOT, "app", "interface", "static");
const TEMPLATES = path.join(ROOT, "app", "interface", "templates");

function read(file) {
    return fs.readFileSync(file, "utf8");
}

/** Strips the Jinja template tags so jsdom parses plain HTML. */
function templateHtml() {
    return read(path.join(TEMPLATES, "app.html")).replace(/\{\{.*?\}\}/g, "test");
}

/**
 * Minimal fetch double. Routes are registered per test as
 * { "/api/file": (body) => response }, and every call is recorded so
 * a test can assert what the page requested.
 */
function makeFetch(routes) {
    const calls = [];
    const fetchImpl = async (url, options = {}) => {
        const body = options.body ? JSON.parse(options.body) : null;
        calls.push({ url, body });
        const handler = routes[url];
        if (!handler) {
            return { ok: false, status: 404, json: async () => ({ detail: "no stub for " + url }) };
        }
        const result = await handler(body);
        if (result && result.__error) {
            return {
                ok: false,
                status: result.status || 500,
                json: async () => ({ detail: result.detail || "failed" }),
            };
        }
        return { ok: true, status: 200, json: async () => result };
    };
    fetchImpl.calls = calls;
    return fetchImpl;
}

/**
 * An EventSource double. Instances register themselves on
 * `opened`, so a test can push events into the live stream:
 *
 *     const es = harness.streams.at(-1);
 *     es.emit("search_result", { type, message, metadata });
 */
function makeEventSource(streams) {
    return class FakeEventSource {
        constructor(url) {
            this.url = url;
            this.listeners = {};
            this.closed = false;
            streams.push(this);
        }
        addEventListener(type, fn) {
            (this.listeners[type] = this.listeners[type] || []).push(fn);
        }
        close() {
            this.closed = true;
        }
        emit(type, payload) {
            (this.listeners[type] || []).forEach((fn) => fn({ data: JSON.stringify(payload) }));
        }
        fail() {
            if (this.onerror) this.onerror(new Error("stream error"));
        }
    };
}

function createHarness({ routes = {} } = {}) {
    // The page's own <script src> tags are stripped: the CDN ones
    // cannot load offline, and ours are injected below only after the
    // stubs they depend on exist on window.
    const html = templateHtml().replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "");
    const dom = new JSDOM(html, {
        url: "http://localhost/app",
        runScripts: "dangerously",
        pretendToBeVisual: true,
    });
    const { window } = dom;

    const streams = [];
    const fetchImpl = makeFetch(routes);

    window.fetch = fetchImpl;
    window.EventSource = makeEventSource(streams);
    window.scrollTo = () => {};
    window.Element.prototype.scrollIntoView = () => {};

    // CDN globals the page expects to already exist.
    window.mermaid = { initialize: () => {}, run: async () => {} };
    window.marked = { parse: (text) => `<p>${text}</p>` };
    window.DOMPurify = { sanitize: (html) => html };
    window.svgPanZoom = () => ({
        destroy: () => {},
        resize: () => {},
        fit: () => {},
        center: () => {},
        zoom: () => {},
        zoomIn: () => {},
        zoomOut: () => {},
        pan: () => {},
        getSizes: () => ({ realZoom: 1 }),
        getZoom: () => 1,
    });

    // Run our two scripts in page order, exactly as the template does.
    // Injected as real <script> elements so their top-level `const`
    // and `function` declarations land in the page's global scope,
    // which is where each file expects to find the other's.
    const runScript = (file) => {
        const script = window.document.createElement("script");
        script.textContent = read(path.join(STATIC, file));
        window.document.body.appendChild(script);
    };
    runScript("app.js");
    runScript("features.js");

    return {
        dom,
        window,
        document: window.document,
        fetch: fetchImpl,
        streams,
        routes,
        $: (id) => window.document.getElementById(id),
        /**
         * Puts the page in the state it reaches after a successful
         * ingest. app.js keeps the repo in its own `currentRepo`
         * binding, so this sets that one — overriding only the
         * RepoPilot getter would leave app.js's own handlers thinking
         * no repo is loaded.
         */
        setRepo(repoFullName = "acme/app", commitSha = "sha1") {
            window.eval(
                `currentRepo = ${JSON.stringify({ repoFullName, commitSha })};`
            );
            window.document.getElementById("tabs").classList.remove("hidden");
        },
        /**
         * Lets queued promise callbacks run. The default is generous
         * on purpose: a click can chain several awaits (fetch ->
         * res.json() -> render), and each needs its own turn.
         */
        flush(times = 12) {
            let chain = Promise.resolve();
            for (let i = 0; i < times; i++) chain = chain.then(() => {});
            return chain;
        },
    };
}

module.exports = { createHarness, templateHtml };
