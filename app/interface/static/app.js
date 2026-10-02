// Simple vanilla JS — no build step, no framework. Talks to our
// FastAPI JSON endpoints and renders results into the page.

let currentRepo = null; // { repoFullName, commitSha }

// The seam features.js talks to. app.js owns ingest/ask/diagram state;
// features.js owns onboarding, streaming, the file drawer and impact,
// and the two only ever meet through this object.
window.RepoPilot = {
    getCurrentRepo: () => currentRepo,
    getPersona: () => getPersona(),
    setStatus: (el, message, type) => setStatus(el, message, type),
    askQuestion: (text) => askQuestion(text),
    setAskContext: (path) => setAskContext(path),
    clearAskContext: () => setAskContext(null),
    setImpactHighlight: (highlight) => setImpactHighlight(highlight),
    conversationPayload: () => conversationPayload(),
    recordTurn: (question, data) => recordTurn(question, data),
    clearConversation: () => clearConversation(),
    // Filled in by features.js once it loads.
    openFile: () => {},
    streamInvestigation: null,
    hideInvestigationTimeline: () => {},
};

// People paste "github.com/owner/repo", "owner/repo", or a URL to a
// file inside the repo. Reduce all of those to the canonical form the
// backend validates, so the common pastes just work.
function normalizeRepoUrl(raw) {
    let value = raw.trim().replace(/\s+/g, "");
    value = value.replace(/^(https?:\/\/)?(www\.)?/i, "");
    if (/^[\w.-]+\/[\w.-]+$/.test(value)) value = "github.com/" + value;  // "owner/repo"
    const match = value.match(/^github\.com\/([\w.-]+)\/([\w.-]+?)(?:\.git)?(?:[\/#?].*)?$/i);
    if (!match) return raw.trim();  // let the backend explain what is wrong
    return "https://github.com/" + match[1] + "/" + match[2];
}

function getPersona() {
    const select = document.getElementById("persona-select");
    return select ? select.value : "newcomer";
}

// The ask options are drawn as option cards. The quick/deep pair is a
// radio-style choice backed by the existing deep-mode checkbox, and the
// persona radios mirror into the hidden select, so everything that reads
// #deep-mode-checkbox or #persona-select keeps working unchanged.
(function wireAskOptions() {
    const deep = document.getElementById("deep-mode-checkbox");
    const quick = document.querySelector('input[name="depth"][value="quick"]');
    const personaSelect = document.getElementById("persona-select");
    if (!deep || !quick || !personaSelect) return;

    const syncDepth = () => {
        quick.checked = !deep.checked;
        deep.closest(".option-card").classList.toggle("selected", deep.checked);
        quick.closest(".option-card").classList.toggle("selected", !deep.checked);
    };
    quick.addEventListener("change", () => { deep.checked = false; syncDepth(); });
    deep.addEventListener("change", syncDepth);
    syncDepth();

    const personaRadios = document.querySelectorAll('input[name="persona"]');
    const syncPersona = () => {
        personaRadios.forEach((r) => {
            r.closest(".option-card").classList.toggle("selected", r.checked);
            if (r.checked) personaSelect.value = r.value;
        });
    };
    personaRadios.forEach((r) => r.addEventListener("change", syncPersona));
    syncPersona();
})();

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

// Example repositories on the empty state. Delegated from the list so a
// repo can be added in the template without touching this file, and it
// goes through the form's own submit rather than a second ingest path,
// so validation, status and error handling stay in one place.
document.addEventListener("click", (e) => {
    const button = e.target.closest(".example");
    if (!button) return;
    const url = button.getAttribute("data-repo");
    if (!url) return;
    document.getElementById("repo-url").value = url;
    ingestForm.requestSubmit();
});

ingestForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const repoUrl = normalizeRepoUrl(document.getElementById("repo-url").value);
    document.getElementById("repo-url").value = repoUrl;
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

        const switchedRepo = !currentRepo || currentRepo.repoFullName !== data.repo_full_name;
        const switchedCommit = !currentRepo || currentRepo.commitSha !== data.commit_sha;
        currentRepo = { repoFullName: data.repo_full_name, commitSha: data.commit_sha };
        if (switchedRepo) clearConversation();  // a thread is about one repo

        // Results belong to the commit they were produced from, so a new
        // commit invalidates them even when the repo is the same. Anything
        // still on screen would be describing code that is no longer
        // loaded, under the name of the repo that is.
        if (switchedRepo || switchedCommit) {
            resetDiagram();
            if (typeof RepoPilot.resetForNewRepo === "function") {
                RepoPilot.resetForNewRepo();
            }
        }

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
        setStatus(ingestStatus, "Ingest failed: " + err.message, "error");
    } finally {
        btn.disabled = false;
    }
});

// --- The phone navigation drawer ---
//
// Below 768px the sidebar is off-canvas. Everything here is a no-op on
// a desktop width, where the drawer parts are display:none and the
// sidebar is an ordinary column.
const navToggle = document.getElementById("nav-toggle");
const navScrim = document.getElementById("nav-scrim");
const sidebarEl = document.getElementById("sidebar");

function setNavOpen(open) {
    if (!navToggle || !sidebarEl || !navScrim) return;
    sidebarEl.classList.toggle("is-open", open);
    navScrim.classList.toggle("is-open", open);
    navToggle.setAttribute("aria-expanded", String(open));
    // hidden rather than only transparent, so a closed scrim cannot
    // swallow taps meant for the page under it.
    navScrim.hidden = !open;
    // The page behind a drawer should not scroll with it.
    document.body.classList.toggle("nav-locked", open);
    navToggle.querySelector(".visually-hidden").textContent =
        open ? "Close the menu" : "Open the menu";
}

if (navToggle) {
    navToggle.addEventListener("click", () => {
        const open = navToggle.getAttribute("aria-expanded") === "true";
        setNavOpen(!open);
        // Opening moves the eye to the drawer; focus should follow it,
        // and come back to the button when it closes.
        if (!open) {
            const first = sidebarEl.querySelector(".tab-btn");
            // The drawer is visibility:hidden until .is-open lands, and a
            // hidden element cannot take focus -- calling focus() right
            // after the class change was a silent no-op, which left a
            // keyboard user tabbing through the page behind the drawer.
            // Reading a layout property forces the style recalc now.
            // Deliberately not requestAnimationFrame: this has to work on
            // a tab that is not painting.
            void sidebarEl.offsetWidth;
            if (first) first.focus();
        }
    });
    navScrim.addEventListener("click", () => {
        setNavOpen(false);
        navToggle.focus();
    });
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && navToggle.getAttribute("aria-expanded") === "true") {
            setNavOpen(false);
            navToggle.focus();
        }
    });
}

// --- Tabs ---
document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
        document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
        btn.classList.add("active");
        document.getElementById(btn.dataset.tab).classList.add("active");
        // Picking a tab is the whole reason the drawer was open.
        setNavOpen(false);
    });
});

// --- Ask ---
const askForm = document.getElementById("ask-form");
const askStatus = document.getElementById("ask-status");
const answerBox = document.getElementById("answer-box");

// Conversation history for follow-up questions, kept in the page only:
// it is sent with each request and never stored on the server, so a
// reload starts a fresh thread. Trimmed to the last few turns because
// the backend only uses the most recent ones.
const MAX_HISTORY_TURNS = 6;
let conversation = [];

function conversationPayload() {
    return conversation.slice(-MAX_HISTORY_TURNS).map((t) => ({
        question: t.question,
        answer: t.answer,
        cited_files: t.cited_files,
    }));
}

function recordTurn(question, data) {
    conversation.push({
        question: question,
        // Only the gist: the backend truncates anyway, and the whole
        // thread has to fit alongside code context in one prompt.
        answer: (data.answer_text || "").slice(0, 1200),
        cited_files: (data.citations || []).map((c) => String(c).split(":")[0]).slice(0, 5),
    });
    renderConversation();
}

function clearConversation() {
    conversation = [];
    renderConversation();
}

function renderConversation() {
    const box = document.getElementById("conversation-thread");
    const list = document.getElementById("conversation-turns");
    if (!box || !list) return;
    box.classList.toggle("hidden", conversation.length === 0);
    list.innerHTML = "";
    conversation.forEach((turn, i) => {
        const li = document.createElement("li");
        li.className = "thread-turn";
        const n = document.createElement("span");
        n.className = "thread-num";
        n.textContent = i + 1;
        const q = document.createElement("span");
        q.className = "thread-q";
        q.textContent = turn.question;  // user text: never innerHTML
        li.append(n, q);
        list.appendChild(li);
    });
}

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
document.getElementById("conversation-clear").addEventListener("click", () => clearConversation());

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
            recordTurn(question, data);
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
                persona: getPersona(),
                history: conversationPayload(),
            }),
        });

        const data = await res.json();

        if (!res.ok) {
            setStatus(askStatus, data.detail || "Failed to answer.", "error");
            return;
        }

        setStatus(askStatus, "", "success");
        renderAnswer(data);
        recordTurn(question, data);
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

/**
 * Clears a rendered diagram and the buttons that only make sense once one
 * exists. Called when a new repo or commit is ingested: a diagram of the
 * previous repo left on screen under the new repo's name is worse than an
 * empty panel.
 */
function resetDiagram() {
    if (!mermaidContainer) return;
    // Clear the inner target, never the container: #mermaid-diagram is a
    // child that the render path looks up by id, so wiping the container
    // removed the element the next render needed.
    const target = document.getElementById("mermaid-diagram");
    if (target) {
        target.textContent = "";
        target.removeAttribute("data-processed");
    }
    mermaidContainer.classList.add("hidden");
    mermaidContainer.classList.remove("pan-zoom-active", "impact-active");
    ["download-png-btn", "download-svg-btn"].forEach((id) => {
        const btn = document.getElementById(id);
        if (btn) btn.classList.add("hidden");
    });
}
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
                detail: (document.getElementById("diagram-detail") || {}).value || "balanced",
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
            // Clicking nodes is the point of the diagram, so it is wired
            // up first and never made to wait on anything.
            makeNodesInteractive(svgEl);
            applyImpactHighlight();
            enablePanZoom(svgEl);
        }

        downloadPngBtn.classList.remove("hidden");
        downloadSvgBtn.classList.remove("hidden");
    } catch (err) {
        setStatus(diagramStatus, "Could not draw the diagram: " + err.message, "error");
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
// And an upper bound. svg-pan-zoom's fit() scales to FILL the container,
// so a five-node diagram arrived blown up to comic size. Mermaid already
// drew it at a sensible scale; never open above that. "fit" still fits,
// because that is the reader asking for it.
const NATURAL_ZOOM = 1;

/**
 * Turns on pan and zoom once the SVG has been laid out.
 *
 * svg-pan-zoom reads getScreenCTM() on init, and that matrix is not
 * invertible while the element has no layout — exactly the case when the
 * diagram tab has only just been shown. So this waits a frame, and it is
 * deliberately not awaited by the render path: pan and zoom are a
 * convenience, and a browser that never fires the callback (a background
 * tab throttling frames) must still leave a diagram you can click.
 */
function enablePanZoom(svgEl) {
    requestAnimationFrame(() => {
        try {
            if (svgEl.getBoundingClientRect().width <= 0) return;
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
        } catch (panErr) {
            // A diagram that renders but cannot be panned is still a
            // diagram. This used to reach the outer catch and replace it
            // with an error message.
            panZoomInstance = null;
            mermaidContainer.classList.remove("pan-zoom-active");
            console.warn("pan/zoom unavailable for this diagram:", panErr);
        }
    });
}

function showReadable() {
    if (!panZoomInstance) return;
    fitDiagram();
    const sizes = panZoomInstance.getSizes();
    // getZoom() is relative to the fitted scale; convert a target real scale.
    const zoomFor = (real) => real / (sizes.realZoom / panZoomInstance.getZoom());
    if (sizes.realZoom < READABLE_ZOOM) {
        panZoomInstance.zoom(zoomFor(READABLE_ZOOM));
        panZoomInstance.pan({ x: 24, y: 24 });
    } else if (sizes.realZoom > NATURAL_ZOOM) {
        panZoomInstance.zoom(zoomFor(NATURAL_ZOOM));
        panZoomInstance.center();
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