// A smoke test for the landing page scripts.
//
// `node --check` parses landing.js happily with a call to a function
// that does not exist -- a ReferenceError only happens when the line
// runs. A refactor dropped setUpCounters and left its call behind, so
// every visit to the front page threw, and nothing caught it because
// the page still looked right. Loading the real template and running
// the real script is the whole test.

const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");

const ROOT = path.resolve(__dirname, "..", "..");
const STATIC = path.join(ROOT, "app", "interface", "static");
const TEMPLATES = path.join(ROOT, "app", "interface", "templates");

const read = (p) => fs.readFileSync(p, "utf8");

function loadLanding() {
    const html = read(path.join(TEMPLATES, "landing.html"))
        .replace(/\{\{.*?\}\}/g, "test")
        .replace(/\{%[\s\S]*?%\}/g, "")
        .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "");

    const errors = [];
    const virtualConsole = new VirtualConsole();
    virtualConsole.on("jsdomError", (e) => errors.push(e));

    const dom = new JSDOM(html, {
        url: "http://localhost/",
        runScripts: "dangerously",
        pretendToBeVisual: true,
        virtualConsole,
    });
    const { window } = dom;
    window.addEventListener("error", (e) => errors.push(e.error || new Error(e.message)));

    for (const file of ["theme.js", "landing.js"]) {
        const script = window.document.createElement("script");
        script.textContent = read(path.join(STATIC, file));
        window.document.body.appendChild(script);
    }
    return { window, errors };
}

test("landing: the scripts run without throwing", () => {
    const { errors } = loadLanding();
    assert.deepEqual(errors.map((e) => e.message), [],
        "the landing scripts threw: " + errors.map((e) => e.message).join("; "));
});

test("landing: every stat still shows its number without the animation", () => {
    // IntersectionObserver does not exist here, which is the same
    // position a browser without it is in. The figure must still be
    // on the page -- the count-up only animates toward what the
    // server already rendered.
    const { window } = loadLanding();
    const stats = [...window.document.querySelectorAll("[data-count-to]")];
    assert.ok(stats.length > 0, "the landing page should have stat numbers");
    for (const node of stats) {
        assert.equal(node.textContent.trim(), node.getAttribute("data-count-to"));
    }
});
