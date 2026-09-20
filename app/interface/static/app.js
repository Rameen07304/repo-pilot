// Simple vanilla JS — no build step, no framework. Talks to our
// FastAPI JSON endpoints and renders results into the page.

let currentRepo = null; // { repoFullName, commitSha }

// The seam features.js talks to. app.js owns ingest/ask/diagram state;
// features.js owns onboarding, streaming, the file drawer and impact,
// and the two only ever meet through this object.
window.RepoPilot = {
    getCurrentRepo: () => currentRepo,
    setStatus: (el, message, type) => setStatus(el, message, type),
    askQuestion: (text) => askQuestion(text),
    setAskContext: (path) => setAskContext(path),
    clearAskContext: () => setAskContext(null),
    setImpactHighlight: (highlight) => setImpactHighlight(highlight),
    // Filled in by features.js once it loads.
    openFile: () => {},
    streamInvestigation: null,
    hideInvestigationTimeline: () => {},
};

const ingestForm = document.getElementById("ingest-form");
const ingestStatus = document.getElementById("ingest-status");
const repoInfo = document.getElementById("repo-info");
const tabsSection = document.getElementById("tabs");

// --- Recent repos (localStorage) ---
const RECENT_REPOS_KEY = "repoPilot.recentRepos";
const MAX_RECENT_REPOS = 8;

const recentReposSection = document.getElementById("recent-repos-section");
const recentReposList = document.getElementById("recent-repos-list");

function getRecentRepos() {
    try {
        const raw = localStorage.getItem(RECENT_REPOS_KEY);
        return raw ? JSON.parse(raw) : [];
    } catch (err) {
        console.warn("Failed to read recent repos from localStorage:", err);
        return [];
    }
}

function saveRecentRepo(entry) {
    try {
        let list = getRecentRepos();
        // Remove any existing entry for the same repo before re-adding,
        // so re-ingesting an already-recent repo just moves it to the
        // top instead of creating a duplicate.
        list = list.filter((r) => r.repoUrl !== entry.repoUrl);
        list.unshift(entry);
        list = list.slice(0, MAX_RECENT_REPOS);
        localStorage.setItem(RECENT_REPOS_KEY, JSON.stringify(list));
        renderRecentRepos();
    } catch (err) {
        console.warn("Failed to save recent repo to localStorage:", err);
    }
}

function removeRecentRepo(repoUrl) {
    const list = getRecentRepos().filter((r) => r.repoUrl !== repoUrl);
    localStorage.setItem(RECENT_REPOS_KEY, JSON.stringify(list));
    renderRecentRepos();
}

function renderRecentRepos() {
    const list = getRecentRepos();

    if (list.length === 0) {
        recentReposSection.classList.add("hidden");
        return;
    }

    recentReposSection.classList.remove("hidden");
    recentReposList.innerHTML = "";

    list.forEach((entry) => {
        const li = document.createElement("li");
        li.className = "recent-repo-item";

        const nameSpan = document.createElement("span");
        nameSpan.className = "recent-repo-name";
        nameSpan.textContent = entry.repoFullName;
        nameSpan.title = entry.repoUrl;

        const removeBtn = document.createElement("button");
        removeBtn.className = "recent-repo-remove";
        removeBtn.textContent = "×";
        removeBtn.title = "Remove from recent";
        removeBtn.addEventListener("click", (e) => {
            e.stopPropagation(); // don't trigger the li's click (re-ingest) when removing
            removeRecentRepo(entry.repoUrl);
        });

        li.addEventListener("click", () => {
            document.getElementById("repo-url").value = entry.repoUrl;
            ingestForm.requestSubmit(); // re-ingest; hits cache instantly if nothing changed
        });

        li.appendChild(nameSpan);
        li.appendChild(removeBtn);
        recentReposList.appendChild(li);
    });
}

// Render on page load, in case there's already saved history.
renderRecentRepos();

ingestForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const repoUrl = document.getElementById("repo-url").value.trim();
    const btn = document.getElementById("ingest-btn");

    setStatus(ingestStatus, "ingesting: fetching, indexing and summarizing... usually under a minute", "loading");
    btn.disabled = true;

    try {
        const res = await fetch("/api/ingest", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ repo_url: repoUrl }),
        });

        const data = await res.json();

        if (!res.ok) {
            setStatus(ingestStatus, data.detail || "Ingestion failed.", "error");
            return;
        }

        currentRepo = { repoFullName: data.repo_full_name, commitSha: data.commit_sha };

        if (data.skipped_file_count > 0) {
            setStatus(
                ingestStatus,
                `ingested ${data.repo_full_name}, but ${data.skipped_file_count} of ${data.file_count + data.skipped_file_count} files could not be fetched (network) — answers and the diagram may be incomplete. Run ingest again to retry.`,
                "warn"
            );
        } else {
            setStatus(
                ingestStatus,
                `ingested ${data.repo_full_name} (${data.was_cached ? "from cache" : "fresh"})`,
                "success"
            );
        }

        document.getElementById("repo-summary").textContent = data.summary;
        document.getElementById("repo-files").textContent = `${data.file_count} files`;
        document.getElementById("repo-stack").textContent = data.detected_stack.join(", ") || "stack not detected";

        repoInfo.classList.remove("hidden");
        tabsSection.classList.remove("hidden");

        saveRecentRepo({
            repoUrl: repoUrl,
            repoFullName: data.repo_full_name,
            commitSha: data.commit_sha,
            summary: data.summary,
            timestamp: Date.now(),
        });
    } catch (err) {
        setStatus(ingestStatus, "Network error: " + err.message, "error");
    } finally {
        btn.disabled = false;
    }
});

// --- Tabs ---
document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
        document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
        btn.classList.add("active");
        document.getElementById(btn.dataset.tab).classList.add("active");
    });
});

// --- Ask ---
const askForm = document.getElementById("ask-form");
const askStatus = document.getElementById("ask-status");
const answerBox = document.getElementById("answer-box");

// A file chosen from the diagram or the onboarding guide. When set,
// the question is prefixed with the path so retrieval is grounded in
// that file — the backend still does its own searching, so this steers
// the question rather than bypassing the repo-wide index.
let askContextPath = null;

const askContextBox = document.getElementById("ask-context");

function setAskContext(path) {
    askContextPath = path;
    askContextBox.classList.toggle("hidden", !path);
    if (path) {
        document.getElementById("ask-context-path").textContent = path;
    }
}

document.getElementById("ask-context-clear").addEventListener("click", () => setAskContext(null));

function applyAskContext(question) {
    return askContextPath ? `In the file ${askContextPath}: ${question}` : question;
}

/** Fills the ask box, switches to the tab, and submits. */
function askQuestion(text) {
    document.getElementById("question-input").value = text;
    document.querySelector('.tab-btn[data-tab="ask-tab"]').click();
    askForm.requestSubmit();
}

function renderAnswer(data) {
    // Render the LLM's Markdown output as real HTML, then sanitize
    // it before insertion. This is NOT optional: the answer text
    // ultimately traces back to repo content the LLM was shown, so
    // we never trust it enough to skip DOMPurify, even though it's
    // "our own" model's output.
    const rawHtml = marked.parse(data.answer_text);
    const safeHtml = DOMPurify.sanitize(rawHtml);
    document.getElementById("answer-text").innerHTML = safeHtml;

    document.getElementById("answer-meta").textContent =
        `mode: ${data.mode_used} · confidence: ${(data.confidence * 100).toFixed(0)}% · steps: ${data.steps_taken}`;

    // Deep mode returns one line per search round; textContent keeps
    // it inert even though the planner's phrases passed through an LLM.
    const logBox = document.getElementById("investigation-log");
    const stepsList = document.getElementById("investigation-steps");
    stepsList.innerHTML = "";
    (data.investigation_log || []).forEach((line) => {
        const li = document.createElement("li");
        li.textContent = line;
        stepsList.appendChild(li);
    });
    logBox.classList.toggle("hidden", stepsList.children.length === 0);

    const list = document.getElementById("citations-list");
    list.innerHTML = "";
    data.citations.forEach((c) => {
        const li = document.createElement("li");
        // A citation reads "path:start-end"; the path before the last
        // colon is what opens in the file drawer.
        const path = c.slice(0, c.lastIndexOf(":"));
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "citation-link";
        btn.textContent = c;
        btn.title = "Open " + path;
        btn.addEventListener("click", () => window.RepoPilot.openFile(path));
        li.appendChild(btn);
        list.appendChild(li);
    });

    answerBox.classList.remove("hidden");
}

askForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!currentRepo) {
        setStatus(askStatus, "Ingest a repo first.", "error");
        return;
    }

    const question = applyAskContext(document.getElementById("question-input").value.trim());
    const deepMode = document.getElementById("deep-mode-checkbox").checked;
    const btn = document.getElementById("ask-btn");

    answerBox.classList.add("hidden");
    btn.disabled = true;

    try {
        // Deep mode streams, so the user watches the investigation
        // instead of a spinner. Quick mode is a single round trip and
        // has nothing worth streaming.
        if (deepMode && window.RepoPilot.streamInvestigation) {
            setStatus(askStatus, "", "success");
            const data = await window.RepoPilot.streamInvestigation(question, {
                repo_full_name: currentRepo.repoFullName,
                commit_sha: currentRepo.commitSha,
            });
            renderAnswer(data);
            return;
        }

        window.RepoPilot.hideInvestigationTimeline();
        setStatus(askStatus, "thinking...", "loading");

        const res = await fetch("/api/ask", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                repo_full_name: currentRepo.repoFullName,
                commit_sha: currentRepo.commitSha,
                question: question,
                deep_mode: deepMode,
            }),
        });

        const data = await res.json();

        if (!res.ok) {
            setStatus(askStatus, data.detail || "Failed to answer.", "error");
            return;
        }

        setStatus(askStatus, "", "success");
        renderAnswer(data);
    } catch (err) {
        setStatus(askStatus, err.message || "Something went wrong.", "error");
    } finally {
        btn.disabled = false;
    }
});

// --- Diagram ---
const diagramBtn = document.getElementById("diagram-btn");
const diagramStatus = document.getElementById("diagram-status");
const mermaidContainer = document.getElementById("mermaid-container");
const folderToggleCheckbox = document.getElementById("folder-toggle-checkbox");
const downloadPngBtn = document.getElementById("download-png-btn");
const downloadSvgBtn = document.getElementById("download-svg-btn");

let panZoomInstance = null; // holds the active svg-pan-zoom controller
let diagramNodePaths = {};  // mermaid node id -> real repo path, from the API
let impactHighlight = null; // { target, direct[], indirect[] } while impact analysis is shown

mermaid.initialize({
    startOnLoad: false,
    theme: "base",
    themeVariables: {
        background: "#121613",
        mainBkg: "#121613",
        primaryColor: "#121613",
        primaryTextColor: "#F2F6F3",
        primaryBorderColor: "#6EE7A8",
        secondaryColor: "#0F1310",
        tertiaryColor: "#0F1310",
        lineColor: "#6EE7A8",
        textColor: "#C7D3CC",
        nodeBorder: "#6EE7A8",
        clusterBkg: "#0F1310",
        clusterBorder: "#23302A",
        edgeLabelBackground: "#121613",
        fontFamily: "JetBrains Mono, monospace",
    },
    flowchart: { useMaxWidth: false },
});

async function generateDiagram() {
    if (!currentRepo) {
        setStatus(diagramStatus, "Ingest a repo first.", "error");
        return;
    }

    setStatus(diagramStatus, "Generating diagram...", "loading");
    diagramBtn.disabled = true;

    // Destroy any previous pan/zoom instance before replacing the SVG —
    // leaving a stale instance attached to a removed node leaks memory
    // and its event listeners keep firing on nothing.
    if (panZoomInstance) {
        panZoomInstance.destroy();
        panZoomInstance = null;
    }

    try {
        const orientationCheckbox = document.getElementById("orientation-checkbox");
        const res = await fetch("/api/diagram", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                repo_full_name: currentRepo.repoFullName,
                commit_sha: currentRepo.commitSha,
                orientation: orientationCheckbox && orientationCheckbox.checked ? "LR" : "TD",
                group_by_folder: folderToggleCheckbox.checked,
            }),
        });

        const data = await res.json();

        if (!res.ok) {
            setStatus(diagramStatus, data.detail || "Failed to generate diagram.", "error");
            return;
        }

        if (data.granularity === "folder") {
            setStatus(
                diagramStatus,
                `grouped to folder level for readability (${data.total_files} files with internal imports) · edge labels = number of imports`,
                "success"
            );
        } else {
            setStatus(diagramStatus, "", "success");
        }

        diagramNodePaths = data.node_paths || {};

        const diagramEl = document.getElementById("mermaid-diagram");
        diagramEl.removeAttribute("data-processed");
        diagramEl.textContent = data.mermaid_syntax;
        mermaidContainer.classList.remove("hidden");
        mermaidContainer.classList.add("pan-zoom-active");

        await mermaid.run({ nodes: [diagramEl] });

        const svgEl = diagramEl.querySelector("svg");
        if (svgEl) {
            // controlIconsEnabled is OFF deliberately: svg-pan-zoom's
            // built-in +/- buttons get injected as real elements inside
            // this same SVG, which taints canvas-based PNG export later.
            // Scroll-wheel zoom, drag-to-pan, and our own Reset View
            // button already cover the same functionality without that
            // side effect.
            panZoomInstance = svgPanZoom(svgEl, {
                zoomEnabled: true,
                panEnabled: true,
                controlIconsEnabled: false,
                fit: true,
                center: true,
                minZoom: 0.2,
                maxZoom: 10,
            });
            showReadable();
            makeNodesInteractive(svgEl);
            applyImpactHighlight();
        }

        downloadPngBtn.classList.remove("hidden");
        downloadSvgBtn.classList.remove("hidden");
    } catch (err) {
        setStatus(diagramStatus, "Network error: " + err.message, "error");
    } finally {
        diagramBtn.disabled = false;
    }
}

/**
 * Makes each rendered file node clickable. Mermaid emits one <g> per
 * node whose id embeds the node id we generated server-side ("N3"),
 * which node_paths maps back to a real path. Pan/zoom is untouched:
 * these listeners sit on the node groups, and a drag is filtered out
 * by comparing pointer positions so panning across a node does not
 * count as a click.
 */
function makeNodesInteractive(svgEl) {
    svgEl.querySelectorAll("g.node").forEach((group) => {
        const nodeId = nodeIdOf(group);
        const path = nodeId && diagramNodePaths[nodeId];
        if (!path) return;

        group.classList.add("clickable-node");
        group.setAttribute("tabindex", "0");
        group.setAttribute("role", "button");
        group.setAttribute("aria-label", "Open " + path);

        const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
        title.textContent = path;
        group.appendChild(title);

        let downAt = null;
        group.addEventListener("pointerdown", (e) => {
            downAt = { x: e.clientX, y: e.clientY };
        });
        group.addEventListener("pointerup", (e) => {
            if (!downAt) return;
            const moved = Math.hypot(e.clientX - downAt.x, e.clientY - downAt.y);
            downAt = null;
            if (moved > 4) return; // that was a pan, not a click
            selectNode(group, path);
        });
        group.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                selectNode(group, path);
            }
        });
    });
}

/**
 * Mermaid's node element ids look like "flowchart-N3-7": our id sits
 * between the prefix and a trailing counter, so match it explicitly
 * rather than by position, which differs between Mermaid versions.
 */
function nodeIdOf(group) {
    const match = /(?:^|-)(N\d+)(?:-|$)/.exec(group.id || "");
    return match ? match[1] : null;
}

function selectNode(group, path) {
    document.querySelectorAll("g.node.selected-node").forEach((n) => n.classList.remove("selected-node"));
    group.classList.add("selected-node");
    window.RepoPilot.openFile(path);
}

/**
 * Paints the blast radius onto the diagram. Purely additive CSS
 * classes on existing nodes — no edge or node is added or removed, so
 * what is highlighted is exactly what the graph traversal returned.
 */
function setImpactHighlight(highlight) {
    impactHighlight = highlight;
    applyImpactHighlight();
}

function applyImpactHighlight() {
    const svgEl = document.querySelector("#mermaid-diagram svg");
    if (!svgEl) return;

    svgEl.querySelectorAll("g.node").forEach((g) => {
        g.classList.remove("impact-target", "impact-direct-node", "impact-indirect-node", "impact-dimmed");
    });
    mermaidContainer.classList.toggle("impact-active", Boolean(impactHighlight));
    if (!impactHighlight) return;

    const direct = new Set(impactHighlight.direct || []);
    const indirect = new Set(impactHighlight.indirect || []);

    svgEl.querySelectorAll("g.node").forEach((group) => {
        const nodeId = nodeIdOf(group);
        const path = nodeId && diagramNodePaths[nodeId];
        if (!path) return;
        if (path === impactHighlight.target) {
            group.classList.add("impact-target");
        } else if (direct.has(path)) {
            group.classList.add("impact-direct-node");
        } else if (indirect.has(path)) {
            group.classList.add("impact-indirect-node");
        } else {
            group.classList.add("impact-dimmed");
        }
    });
}

diagramBtn.addEventListener("click", generateDiagram);

// Re-generate immediately when the folder-grouping or orientation
// toggles change, so it feels like a live view option rather than a
// "click twice" step.
folderToggleCheckbox.addEventListener("change", () => {
    if (currentRepo && !mermaidContainer.classList.contains("hidden")) {
        generateDiagram();
    }
});
document.getElementById("orientation-checkbox").addEventListener("change", () => {
    if (currentRepo && !mermaidContainer.classList.contains("hidden")) {
        generateDiagram();
    }
});

function fitDiagram() {
    if (panZoomInstance) {
        panZoomInstance.resize();
        panZoomInstance.fit();
        panZoomInstance.center();
    }
}

// Below this real scale, Mermaid's 16px labels drop under ~10px and
// file names stop being readable. A big diagram therefore opens at
// this zoom from its top-left corner; "fit" still gives the overview.
const READABLE_ZOOM = 0.6;

function showReadable() {
    if (!panZoomInstance) return;
    fitDiagram();
    const sizes = panZoomInstance.getSizes();
    if (sizes.realZoom < READABLE_ZOOM) {
        // getZoom() is relative to the fitted scale; convert the target real scale
        panZoomInstance.zoom(READABLE_ZOOM / (sizes.realZoom / panZoomInstance.getZoom()));
        panZoomInstance.pan({ x: 24, y: 24 });
    }
}

document.getElementById("reset-view-btn").addEventListener("click", fitDiagram);
document.getElementById("zoom-in-btn").addEventListener("click", () => panZoomInstance && panZoomInstance.zoomIn());
document.getElementById("zoom-out-btn").addEventListener("click", () => panZoomInstance && panZoomInstance.zoomOut());

// --- Expand to the whole window ---
// Deliberately NOT the browser Fullscreen API: embedded webviews and
// iframes either refuse it or "grant" it with no visible effect. A fixed
// overlay behaves the same everywhere.
const fullscreenBtn = document.getElementById("fullscreen-btn");
const EXPANDED_CLASS = "diagram-fullscreen";

function setExpanded(on) {
    mermaidContainer.classList.toggle(EXPANDED_CLASS, on);
    document.body.classList.toggle("has-fullscreen-diagram", on);
    fullscreenBtn.textContent = on ? "exit" : "full";
    showReadable(); // the viewport just changed size; re-derive the starting view for it
}

fullscreenBtn.addEventListener("click", () => {
    setExpanded(!mermaidContainer.classList.contains(EXPANDED_CLASS));
});
document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && mermaidContainer.classList.contains(EXPANDED_CLASS)) {
        setExpanded(false);
    }
});
window.addEventListener("resize", () => {
    if (panZoomInstance) panZoomInstance.resize();
});

downloadSvgBtn.addEventListener("click", () => {
    const svgEl = document.querySelector("#mermaid-diagram svg");
    if (!svgEl) return;

    const serializer = new XMLSerializer();
    const svgString = serializer.serializeToString(svgEl);
    const blob = new Blob([svgString], { type: "image/svg+xml" });
    const url = URL.createObjectURL(blob);

    const a = document.createElement("a");
    a.href = url;
    a.download = `${(currentRepo?.repoFullName || "diagram").replace("/", "_")}.svg`;
    a.click();
    URL.revokeObjectURL(url);
});

downloadPngBtn.addEventListener("click", () => {
    const svgEl = document.querySelector("#mermaid-diagram svg");
    if (!svgEl) return;

    const clone = svgEl.cloneNode(true);

    // Force every text element to a safe, non-external font before
    // rasterizing. Mermaid's SVG inherits "Space Grotesk"/"Inter" from
    // the page's Google Fonts <link>, and canvas rasterization taints
    // itself trying to resolve that external font reference. Overriding
    // to a generic system font sidesteps the taint entirely.
    const styleEl = document.createElementNS("http://www.w3.org/2000/svg", "style");
    styleEl.textContent = "* { font-family: Arial, sans-serif !important; }";
    clone.insertBefore(styleEl, clone.firstChild);

    // Strip anything else that could reference an external resource —
    // these also taint the canvas even without an explicit external
    // <link> tag inside the SVG itself.
    clone.querySelectorAll("foreignObject, image, link").forEach((el) => el.remove());

    const serializer = new XMLSerializer();
    const svgString = serializer.serializeToString(clone);
    const svgBlob = new Blob([svgString], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(svgBlob);

    const img = new Image();

    img.onerror = (err) => {
        console.error("PNG export failed: image failed to load from SVG blob", err);
        setStatus(diagramStatus, "PNG export failed — try Download SVG instead.", "error");
        URL.revokeObjectURL(url);
    };

    img.onload = () => {
        try {
            const canvas = document.createElement("canvas");
            const width = svgEl.viewBox.baseVal.width || svgEl.clientWidth || 1200;
            const height = svgEl.viewBox.baseVal.height || svgEl.clientHeight || 800;
            const scale = 2; // sharper export than on-screen resolution
            canvas.width = width * scale;
            canvas.height = height * scale;

            const ctx = canvas.getContext("2d");
            ctx.scale(scale, scale);
            ctx.fillStyle = "#121613"; // matches --surface, so the exported PNG isn't a jarring white square against the dark diagram panel
            ctx.fillRect(0, 0, width, height);
            ctx.drawImage(img, 0, 0, width, height);
            URL.revokeObjectURL(url);

            canvas.toBlob((blob) => {
                if (!blob) {
                    console.error("PNG export failed: canvas.toBlob returned null (likely a tainted canvas)");
                    setStatus(diagramStatus, "PNG export failed — try Download SVG instead.", "error");
                    return;
                }
                const pngUrl = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = pngUrl;
                a.download = `${(currentRepo?.repoFullName || "diagram").replace("/", "_")}.png`;
                a.click();
                URL.revokeObjectURL(pngUrl);
            });
        } catch (err) {
            console.error("PNG export failed:", err);
            setStatus(diagramStatus, "PNG export failed — try Download SVG instead.", "error");
        }
    };

    img.src = url;
});

// --- Helper ---
function setStatus(el, message, type) {
    el.textContent = message;
    el.className = message ? `status-${type}` : "";
}